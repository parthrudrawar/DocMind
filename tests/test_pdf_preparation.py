"""Real native PDF/worker tests with generated public fixtures; no cloud services."""
from dataclasses import replace
from io import BytesIO
import subprocess
import threading
import time
from types import SimpleNamespace

import fitz
import pytest

from session_cloud.bounded_pdf import BoundedPDFParser
from session_cloud.original_pdf import OriginalPageParser
from session_cloud.parse_contract import ParseFailure, ParseLimits, validate_result
from session_cloud import parser_runner
from session_cloud.runtime import SessionError, WorkspaceStore

A, B = "a" * 64, "b" * 64


def pdf_fixture(*, pages=1, table=False, scanned=False, encrypted=False):
    with fitz.open() as doc:
        for number in range(pages):
            page = doc.new_page()
            if scanned and number == pages - 1:
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
                pix.clear_with(255)
                page.insert_image(fitz.Rect(50, 50, 150, 150), pixmap=pix)
            else:
                page.insert_text((40, 50), f"Project Amber page {number + 1} uses solar energy. Its documented capacity is twelve megawatts.")
            if table:
                for x in (40, 240, 440):
                    page.draw_line((x, 100), (x, 190))
                for y in (100, 130, 160, 190):
                    page.draw_line((40, y), (440, y))
                for y, cells in zip((120, 150, 180), (("Metric", "Value"), ("Energy", "Solar"), ("Capacity", "12 MW"))):
                    for x, text in zip((50, 250), cells):
                        page.insert_text((x, y), text)
        return doc.tobytes(**({"encryption": fitz.PDF_ENCRYPT_AES_256,
                              "owner_pw": "dummy-owner", "user_pw": "dummy-user"} if encrypted else {}))


@pytest.fixture
def environment(tmp_path):
    now, users = [1000.0], {A: "11111111-1111-4111-8111-111111111111", B: "22222222-2222-4222-8222-222222222222"}

    def authorize(token):
        if token not in users:
            raise SessionError("expired")
        return SimpleNamespace(user_id=users[token], deadline=10000)

    store = WorkspaceStore(SimpleNamespace(authorize=authorize, logout=lambda token: users.pop(token, None) is not None),
                           clock=lambda: now[0], base_dir=tmp_path)
    return store, now, users


def upload(store, token=A, ref=None, data=None):
    ref = ref or store.open(token)
    record = store.upload(token, ref, "public-fixture.pdf", BytesIO(data or pdf_fixture()))
    return ref, record["id"]


def test_real_pdf_tables_page_numbers_atomic_preparation_and_foreign_access(environment):
    store, _, _ = environment
    data = pdf_fixture(pages=2, table=True)
    wa, document = upload(store, data=data)
    wb = store.open(B)
    result = store.prepare(A, wa, document)
    assert result["status"] == "prepared" and result["pages"] == 2
    pages = store.parsed_pages(A, wa, document)
    assert [page["page_number"] for page in pages] == [1, 2]
    assert all(page["has_tables"] and "Energy | Solar" in "\n".join(page["table_blocks"]) for page in pages)
    assert pages[0]["text"].count("Energy") == 1
    assert result["metrics"]["peak_rss_bytes"] > 0
    assert store.documents(A, wa)[0]["status"] != "indexed"
    assert store.prepare(A, wa, document) == result  # No duplicate publication.
    assert store.upload(A, wa, "renamed.pdf", BytesIO(data))["status"] == "prepared"
    for operation in (lambda: store.prepare(B, wa, document), lambda: store.prepare(B, wb, document),
                      lambda: store.parsed_pages(B, wa, document), lambda: store.parsed_pages(B, wb, document)):
        with pytest.raises(SessionError):
            operation()
    store.delete_document(A, wa, document)
    with pytest.raises(SessionError):
        store.parsed_pages(A, wa, document)
    assert store.documents(B, wb) == [] and not list(store.root.rglob("prepare-*.json"))


@pytest.mark.parametrize("kind,limits,code", [
    ("malformed", ParseLimits(), "pdf_invalid"),
    ("encrypted", ParseLimits(), "pdf_encrypted"),
    ("pages", ParseLimits(pages=1), "pdf_pages"),
    ("page_text", ParseLimits(page_bytes=10), "pdf_text"),
    ("total_text", ParseLimits(text_bytes=120), "pdf_text"),
    ("mixed_scan", ParseLimits(), "pdf_ocr_unavailable"),
    ("blank", ParseLimits(), "pdf_blank"),
])
def test_failed_pdf_never_publishes_partial_pages(environment, kind, limits, code):
    store, _, _ = environment
    if kind == "malformed":
        data = b"%PDF-1.7\nnot a valid PDF"
    elif kind == "blank":
        with fitz.open() as doc:
            doc.new_page()
            data = doc.tobytes()
    else:
        data = pdf_fixture(pages=2 if kind in {"pages", "total_text", "mixed_scan"} else 1,
                           encrypted=kind == "encrypted", scanned=kind == "mixed_scan")
    ref, document = upload(store, data=data)
    with pytest.raises(SessionError) as caught:
        store.prepare(A, ref, document, limits=limits)
    assert caught.value.code == code
    assert store.documents(A, ref)[0]["status"] == "accepted"
    with pytest.raises(SessionError):
        store.parsed_pages(A, ref, document)
    assert not list(store.root.rglob("prepare-*.json"))
    with store._db(store.check(A, ref)) as db:
        assert db.execute("SELECT count(*) FROM prepared_pages").fetchone()[0] == 0


def test_table_failure_is_not_suppressed_or_logged(tmp_path, monkeypatch, caplog):
    source = tmp_path / "safe.pdf"
    source.write_bytes(pdf_fixture())

    def fail(_page):
        raise ValueError("dummy-private-content-never-log")

    monkeypatch.setattr(fitz.Page, "find_tables", fail)
    with pytest.raises(ParseFailure) as caught:
        BoundedPDFParser(ParseLimits()).parse(source)
    assert caught.value.code == "pdf_table" and "dummy-private" not in caplog.text


def test_second_page_failure_does_not_substitute_blank_page(tmp_path, monkeypatch):
    source = tmp_path / "safe.pdf"
    source.write_bytes(pdf_fixture(pages=2))
    original = OriginalPageParser._process_page

    def fail_second(self, page, number):
        if number == 2:
            raise RuntimeError("dummy failure")
        return original(self, page, number)

    monkeypatch.setattr(OriginalPageParser, "_process_page", fail_second)
    with pytest.raises(ParseFailure):
        BoundedPDFParser(ParseLimits()).parse(source)


def test_ocr_pixel_guard_runs_before_image_allocation(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(pdf_fixture(scanned=True))
    parser = BoundedPDFParser(ParseLimits(ocr_enabled=True, ocr_pixels=1))
    parser.tesseract = "dummy-tesseract-not-executed"
    monkeypatch.setattr(fitz.Page, "get_pixmap", lambda *_args, **_kwargs: pytest.fail("Pixel guard must run first"))
    with pytest.raises(ParseFailure) as caught:
        parser.parse(source)
    assert caught.value.code == "pdf_ocr_limit"


@pytest.mark.parametrize("field,value", [("pages", 201), ("file_bytes", 101 * 1024 * 1024),
                                        ("seconds", 0), ("memory_bytes", 513 * 1024 * 1024),
                                        ("ocr_enabled", "true"), ("page_bytes", True)])
def test_limits_cannot_be_weakened_past_cloud_ceilings(field, value):
    with pytest.raises(ValueError):
        replace(ParseLimits(), **{field: value})


def probe_worker(tmp_path, monkeypatch, body):
    script = tmp_path / "probe.py"
    script.write_text("import json,sys,os,time\nr=json.loads(sys.stdin.read())\n" + body, encoding="utf-8")
    monkeypatch.setattr(parser_runner, "WORKER", script)
    return tmp_path / "input.pdf", tmp_path / "result.json"


def test_actual_child_timeout_terminates_process(tmp_path, monkeypatch):
    source, output = probe_worker(tmp_path, monkeypatch, "time.sleep(60)\n")
    children = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(parser_runner.subprocess, "Popen", launch)
    started = time.monotonic()
    with pytest.raises(ParseFailure) as caught:
        parser_runner.run_parser(source, output, ParseLimits(seconds=1), lambda: None)
    assert caught.value.code == "pdf_time" and time.monotonic() - started < 10
    assert children and children[0].poll() is not None


def test_actual_child_memory_cap_rejects_allocation(tmp_path, monkeypatch):
    body = """
if os.name != 'nt':
    import resource
    n=r['limits']['memory_bytes']
    resource.setrlimit(resource.RLIMIT_AS,(n,n))
open(r['output']+'.started','w').write('ready')
buffer=bytearray(128*1024*1024)
open(r['output'],'w').write(json.dumps({'version':1,'error':'pdf_blank'}))
"""
    source, output = probe_worker(tmp_path, monkeypatch, body)
    with pytest.raises(ParseFailure) as caught:
        parser_runner.run_parser(source, output, ParseLimits(memory_bytes=64 * 1024 * 1024), lambda: None)
    assert caught.value.code == "pdf_resource"
    assert output.with_name(output.name + ".started").is_file()


def test_secrets_not_forwarded_to_actual_child_and_diagnostics_discarded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "dummy-sensitive-sentinel")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-sensitive-sentinel")
    body = """
assert not any(name in os.environ for name in ['GROQ_API_KEY','SUPABASE_SERVICE_ROLE_KEY'])
sys.stderr.write('dummy-provider-private-diagnostic')
open(r['output'],'w').write(json.dumps({'version':1,'error':'pdf_blank'}))
"""
    source, output = probe_worker(tmp_path, monkeypatch, body)
    with pytest.raises(ParseFailure) as caught:
        parser_runner.run_parser(source, output, ParseLimits(), lambda: None)
    assert caught.value.code == "pdf_blank"
    captured = capsys.readouterr()
    assert "dummy-provider" not in captured.out + captured.err


def test_logout_or_expiry_during_child_work_revokes_publication(environment, tmp_path, monkeypatch):
    store, _, users = environment
    ref, document = upload(store)
    entered, release, errors = threading.Event(), threading.Event(), []
    original = parser_runner.run_parser

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(parser_runner, "run_parser", delayed)

    def work():
        try:
            store.prepare(A, ref, document)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(5)
        users.pop(A)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], SessionError)
    assert not list(store.root.rglob("prepare-*.json"))
    # Inspect only the test fixture DB: no prepared state escaped revoked auth.
    with store._db(next(iter(store._leases.values()))) as db:
        assert db.execute("SELECT status FROM documents").fetchone()[0] == "accepted"


def test_transaction_rolls_back_all_pages_if_storage_publication_fails(environment, monkeypatch):
    store, _, _ = environment
    ref, document = upload(store, data=pdf_fixture(pages=2))
    # Fail the publication write directly; observing a transaction from another
    # SQLite connection cannot see uncommitted rows.
    original_db = store._db
    from contextlib import contextmanager

    @contextmanager
    def fail_publication(lease):
        with original_db(lease) as db:
            yield db
            if db.execute("SELECT count(*) FROM prepared_pages").fetchone()[0]:
                raise SessionError("disk")

    monkeypatch.setattr(store, "_db", fail_publication)
    with pytest.raises(SessionError) as caught:
        store.prepare(A, ref, document)
    assert caught.value.code == "disk"
    monkeypatch.setattr(store, "_db", original_db)
    assert store.documents(A, ref)[0]["status"] == "accepted"
    with store._db(store.check(A, ref)) as db:
        assert db.execute("SELECT count(*) FROM prepared_pages").fetchone()[0] == 0


def test_contract_rejects_nonconsecutive_pages_and_unbounded_text():
    page = {"page_number": 2, "text": "text", "paragraphs": ["text"], "table_blocks": [],
            "is_ocr": False, "has_tables": False, "word_count": 1}
    result = {"version": 1, "pages": [page], "metrics": {"wall_seconds": 0, "cpu_seconds": 0, "peak_rss_bytes": 0}}
    with pytest.raises(ParseFailure):
        validate_result(result, ParseLimits())
    page["page_number"] = 1
    page["text"] = "fabricated unrelated text"
    with pytest.raises(ParseFailure):
        validate_result(result, ParseLimits())


def test_protocol_error_values_do_not_escape_as_raw_exceptions():
    with pytest.raises(ParseFailure) as caught:
        validate_result({"version": 1, "error": {"private": "dummy-private-error"}}, ParseLimits())
    assert caught.value.code == "pdf_worker" and "dummy-private" not in str(caught.value)


def test_actual_child_revocation_terminates_work(tmp_path, monkeypatch):
    source, output = probe_worker(tmp_path, monkeypatch, "time.sleep(60)\n")
    children, checks = [], []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    def revoked():
        checks.append(True)
        raise SessionError("expired")

    monkeypatch.setattr(parser_runner.subprocess, "Popen", launch)
    with pytest.raises(SessionError):
        parser_runner.run_parser(source, output, ParseLimits(), revoked)
    assert checks and children and children[0].poll() is not None


def test_prepared_not_searchable_in_two_independent_ui_sessions(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui

    store, _, _ = environment
    ref, document = upload(store)
    store.prepare(A, ref, document)
    monkeypatch.setattr(ui, "_fixture_store", store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    b = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{B}')").run(timeout=20)
    assert not a.exception and not b.exception
    # Phase 2B must not advertise a merely prepared PDF as searchable.
    assert a.multiselect[0].options == [] and b.multiselect[0].options == []
    assert any(button.label == "Index PDF" for button in a.sidebar.button)
    assert a.chat_input[0].disabled and b.chat_input[0].disabled


def test_failed_preparation_quota_survives_new_login_and_workspace(environment):
    store, _, users = environment
    store.limits = replace(store.limits, user_preparations=1)
    ref, document = upload(store, data=b"%PDF-1.7\ninvalid")
    with pytest.raises(SessionError) as caught:
        store.prepare(A, ref, document)
    assert caught.value.code == "pdf_invalid"
    owner = users[A]
    assert store.logout(A)
    token = "c" * 64
    users[token] = owner
    ref, document = upload(store, token=token, data=b"%PDF-1.7\ninvalid")
    with pytest.raises(SessionError) as caught:
        store.prepare(token, ref, document)
    assert caught.value.code == "preparation_limit"


def test_global_preparation_quota_blocks_another_verified_account(environment):
    store, _, _ = environment
    store.limits = replace(store.limits, global_preparations=1)
    wa, da = upload(store, data=b"%PDF-1.7\ninvalid")
    wb, db = upload(store, token=B, data=b"%PDF-1.7\ninvalid B")
    with pytest.raises(SessionError):
        store.prepare(A, wa, da)
    with pytest.raises(SessionError) as caught:
        store.prepare(B, wb, db)
    assert caught.value.code == "preparation_limit"
