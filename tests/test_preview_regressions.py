"""Preview ingestion/UI recovery and privacy-safe native guard diagnostics."""
from io import BytesIO
import errno
import json
from pathlib import Path

import pytest
import test_session_cloud
import test_session_retrieval

from session_cloud.index_contract import IndexFailure, IndexLimits, safe_diagnostic
from session_cloud.runtime import SessionError

environment = test_session_cloud.environment
model_cache = test_session_retrieval.model_cache
native_environment = test_session_retrieval.environment
A, B = test_session_cloud.A, test_session_cloud.B


@pytest.mark.parametrize("name", ["disk_bytes", "model_cache_bytes"])
def test_atomic_rename_churn_restarts_whole_scan_until_stable(tmp_path, monkeypatch, name):
    from session_cloud import disk_usage
    original = Path.rglob
    current = tmp_path / "download-0.part"
    current.write_bytes(b"x" * 65536)
    scans = []

    def racing(folder, pattern):
        nonlocal current
        paths = list(original(folder, pattern))
        if folder == tmp_path:
            scans.append(1)
            if len(scans) <= 3:
                target = tmp_path / f"download-{len(scans)}.part"
                current.replace(target)  # real atomic rename after enumeration
                current = target
        return iter(paths)

    monkeypatch.setattr(Path, "rglob", racing)
    assert getattr(disk_usage, name)(tmp_path) == 65536
    assert len(scans) == 4 and current.read_bytes() == b"x" * 65536


@pytest.mark.parametrize("name", ["disk_bytes", "model_cache_bytes"])
def test_persistent_accounting_churn_is_bounded_and_fails_closed(tmp_path, monkeypatch, name):
    import time
    from session_cloud import disk_usage
    scans = []
    def disappearing(*args):
        scans.append(1)
        raise FileNotFoundError()
    monkeypatch.setattr(Path, "rglob", disappearing)
    started = time.monotonic()
    with pytest.raises(OSError) as caught:
        getattr(disk_usage, name)(tmp_path)
    assert caught.value.errno == errno.EAGAIN
    assert len(scans) <= 50 and time.monotonic() - started < 2


@pytest.mark.parametrize("fault", ["workspace_accounting", "model_cache_accounting"])
def test_supervisor_reports_exact_accounting_stage_and_stops_worker(tmp_path, monkeypatch, caplog, fault):
    from session_cloud import index_service
    worker = tmp_path / "dummy-worker.py"
    worker.write_text("import sys,time\nsys.stdin.buffer.readline()\nsys.stdin.buffer.readline()\ntime.sleep(30)\n")
    monkeypatch.setattr(index_service, "WORKER", worker)
    service = index_service.IndexService(tmp_path / "public-cache")
    checks = []
    def active():
        checks.append(1)
        if fault == "workspace_accounting" and len(checks) > 1:
            raise OSError(errno.EAGAIN, "dummy-private-sentinel")
    def cache_size(*args):
        if fault == "model_cache_accounting":
            raise OSError(errno.EAGAIN, "dummy-private-sentinel")
        return 0
    monkeypatch.setattr(index_service, "model_cache_bytes", cache_size)
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), active)
    assert caught.value.reason == "filesystem_churn"
    assert f"stage={fault} reason=filesystem_churn" in caplog.text
    assert "native_code=11" in caplog.text and "dummy-private-sentinel" not in caplog.text
    assert service.process is None and not list(tmp_path.glob("index-*"))


def test_cache_budget_is_verified_even_after_fast_worker_completion(tmp_path, monkeypatch):
    from session_cloud import index_service
    worker = tmp_path / "dummy-worker.py"
    worker.write_text("""import sys,json
from pathlib import Path
sys.stdin.buffer.readline()
p=json.loads(sys.stdin.buffer.readline())
target=Path(p['output'])
pending=target.with_suffix('.part')
pending.write_text('{}')
pending.replace(target)
""")
    monkeypatch.setattr(index_service, "WORKER", worker)
    monkeypatch.setattr(index_service, "model_cache_bytes", lambda _: 161 * 1024**2
                        if (tmp_path / "index-response.json").exists() else 0)
    service = index_service.IndexService(tmp_path / "public-cache")
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), lambda: None)
    assert caught.value.reason == "model_cache_capacity" and service.process is None


def test_real_pdf_reproduces_errno_11_then_indexes_with_bounded_rescans(
    native_environment, monkeypatch, caplog
):
    import fitz
    from session_cloud import disk_usage, index_service
    store, _, _ = native_environment
    ref = store.open(A)
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((45, 60), "The Amber solar farm generates twelve megawatts. Batteries store energy overnight.")
        data = pdf.tobytes()
    probe = store.root.parent / "download-churn-probe"
    probe.mkdir()
    current = probe / "download-0.part"
    current.write_bytes(b"x" * 65536)
    scans = []
    original_glob, original_cache = Path.rglob, index_service.model_cache_bytes
    attempts = disk_usage._scan_attempts
    def racing(folder, pattern):
        nonlocal current
        paths = list(original_glob(folder, pattern))
        if folder == probe:
            scans.append(1)
            if len(scans) <= 3:
                target = probe / f"download-{len(scans)}.part"
                current.replace(target)
                current = target
        return iter(paths)
    def checked_cache(cache):
        if len(scans) < 4:
            assert disk_usage.model_cache_bytes(probe) == 65536
        return original_cache(cache)  # real model budget is never replaced
    monkeypatch.setattr(Path, "rglob", racing)
    monkeypatch.setattr(index_service, "model_cache_bytes", checked_cache)
    monkeypatch.setattr(disk_usage, "_scan_attempts", lambda: iter(range(3)))
    with pytest.raises(SessionError) as caught:
        store.ingest_upload(A, ref, "public-solar-report.pdf", BytesIO(data))
    assert caught.value.code == "index_resource" and "native_code=11" in caplog.text
    doc = store.documents(A, ref)[0]
    assert doc["status"] == "prepared" and not list(store._folder(ref).glob("snapshot-*"))
    scans.clear()
    monkeypatch.setattr(disk_usage, "_scan_attempts", attempts)
    result = store.ingest_upload(A, ref, "public-solar-report.pdf", BytesIO(data), retry=True)
    assert result["status"] == "indexed" and len(scans) == 4
    assert store._uploads == store._preparations == 1
    hits = store.retrieve(A, ref, "solar generating capacity", [result["id"]])
    assert hits and "twelve megawatts" in hits[0]["text"]
    assert all(h["doc_id"] == result["id"] and h["page_number"] == 1 for h in hits)


def app_for(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui
    monkeypatch.setattr(ui, "_fixture_store", store, raising=False)
    return AppTest.from_string(
        f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store,'{A}')"
    )


def mock_upload(monkeypatch, store, *, fail=False):
    import streamlit as st
    from session_cloud import streamlit_app as ui
    stream = BytesIO(b"%PDF-1.7 dummy-public-test-document")
    stream.file_id, stream.name = "upload-version-1", "sample.pdf"
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: stream)
    calls = []
    real_chat = ui.render_chat_tab

    def chat(*args, **kwargs):
        calls.append("chat")
        return real_chat(*args, **kwargs)

    def prepare(cap, ref, document):
        calls.append("prepare")
        with store._db(store.check(cap, ref)) as db:
            db.execute("UPDATE documents SET status='prepared' WHERE id=?", (document,))

    def index(cap, ref, document):
        calls.append("index")
        if fail:
            raise SessionError("index_resource")
        with store._db(store.check(cap, ref)) as db:
            folder = store._destination(ref)
            db.execute("INSERT OR REPLACE INTO active_index VALUES(1,?,?)", (folder.name, "c" * 64))
            db.execute("UPDATE documents SET status='indexed' WHERE id=?", (document,))

    monkeypatch.setattr(ui, "render_chat_tab", chat)
    monkeypatch.setattr(store, "prepare", prepare)
    monkeypatch.setattr(store, "index_document", index)
    return calls, stream, index


def assert_chat_available(app):
    assert not app.exception
    assert len(app.tabs) == 7
    assert len(app.chat_input) == 1 and not app.chat_input[0].disabled
    assert any(b.label == "New Chat" for b in app.button)


def test_empty_chat_can_explain_upload_without_calling_ai(environment, monkeypatch):
    store, _, _ = environment
    app = app_for(store, monkeypatch).run(timeout=25)
    assert_chat_available(app)
    app.chat_input[0].set_value("What is in my PDF?").run(timeout=25)
    assert_chat_available(app)
    assert any("Upload a PDF" in e.value for e in app.info)
    ref = store.open(A)
    assert store.history(A, ref, store.chats(A, ref)[0]) == []
    assert store._uploads == 0 and "pending_turn" not in app.session_state


def test_auto_index_runs_after_chat_once_and_selection_has_no_default_conflict(
    environment, monkeypatch, caplog
):
    store, _, _ = environment
    calls, _, _ = mock_upload(monkeypatch, store)
    app = app_for(store, monkeypatch).run(timeout=25)
    assert_chat_available(app)
    assert calls.index("chat") < calls.index("prepare") < calls.index("index")
    ref = store.open(A)
    doc = store.documents(A, ref)[0]
    assert doc["status"] == "indexed"
    assert app.multiselect[0].value == [doc["id"]]
    app.multiselect[0].set_value([]).run(timeout=25)
    app.run(timeout=25)
    assert app.multiselect[0].value == []  # deliberate deselection survives reruns
    assert calls.count("prepare") == calls.count("index") == store._uploads == 1
    assert "created with a default value" not in caplog.text
    assert not app.warning


def test_rerun_after_publication_reconciles_ready_without_retry(environment, monkeypatch):
    import streamlit as st
    store, _, _ = environment
    calls, _, index = mock_upload(monkeypatch, store)
    interrupted = []
    def published(cap, workspace, document):
        index(cap, workspace, document)
        if not interrupted:
            interrupted.append(1)
            st.rerun()  # publication completed, but upload feedback did not
    monkeypatch.setattr(store, "index_document", published)
    app = app_for(store, monkeypatch).run(timeout=25)
    app.run(timeout=25)
    assert_chat_available(app)
    assert calls.count("index") == calls.count("prepare") == store._uploads == 1
    assert "code" not in app.session_state["upload_feedback"]
    assert not any(b.label == "Retry processing" for b in app.button)


def test_index_failure_reruns_retry_and_manual_failure_keep_chat_visible(environment, monkeypatch):
    store, _, _ = environment
    calls, _, _ = mock_upload(monkeypatch, store, fail=True)
    app = app_for(store, monkeypatch).run(timeout=25)
    assert_chat_available(app)
    ref = store.open(A)
    assert store.documents(A, ref)[0]["status"] == "prepared"
    assert any(e.label == "sample.pdf" for e in app.sidebar.expander)
    assert any(b.label == "Delete" for b in app.sidebar.button)
    assert not list(store._folder(ref).glob("snapshot-*"))
    for _ in range(2):
        app.run(timeout=25)
        assert_chat_available(app)
    assert calls.count("index") == 1 and calls.count("prepare") == 1
    app.button(key="retry_upload").click().run(timeout=25)
    assert_chat_available(app)
    assert calls.count("index") == 2 and calls.count("prepare") == store._uploads == 1
    assert not app.warning  # single sidebar status, no duplicated warning banners
    doc = store.documents(A, ref)[0]
    app.button(key="index_" + doc["id"]).click().run(timeout=25)
    assert_chat_available(app)
    assert calls.count("index") == 3
    def recovered(cap, workspace, document):
        calls.append("recovered")
        with store._db(store.check(cap, workspace)) as db:
            folder = store._destination(workspace)
            db.execute("INSERT INTO active_index VALUES(1,?,?)", (folder.name, "c" * 64))
            db.execute("UPDATE documents SET status='indexed' WHERE id=?", (document,))
    monkeypatch.setattr(store, "index_document", recovered)
    app.button(key="index_" + doc["id"]).click().run(timeout=25)
    app.run(timeout=25)
    assert_chat_available(app)
    assert "code" not in app.session_state["upload_feedback"]
    assert calls.count("recovered") == 1 and calls.count("prepare") == 1
    assert app.multiselect[0].value == [doc["id"]]


def test_failed_index_retry_resumes_prepared_document_and_remains_user_scoped(environment, monkeypatch):
    store, _, _ = environment
    calls, stream, _ = mock_upload(monkeypatch, store, fail=True)
    ref, other = store.open(A), store.open(B)
    with pytest.raises(SessionError):
        store.ingest_upload(A, ref, stream.name, stream)
    doc = store.documents(A, ref)[0]
    assert doc["status"] == "prepared"
    assert store.documents(B, other) == []
    with pytest.raises(SessionError):
        store.ingest_upload(B, ref, stream.name, stream, retry=True)
    def success(cap, workspace, document):
        with store._db(store.check(cap, workspace)) as db:
            db.execute("UPDATE documents SET status='indexed' WHERE id=?", (document,))
    monkeypatch.setattr(store, "index_document", success)
    assert store.ingest_upload(A, ref, stream.name, stream, retry=True)["status"] == "indexed"
    assert calls.count("prepare") == store._uploads == 1
    for _ in range(3):
        assert store.ingest_upload(A, ref, stream.name, stream)["duplicate"]


@pytest.mark.parametrize("value", [None, [], {"stage": {}, "reason": []},
    {"stage": "private-document-text", "reason": "dummy-secret", "native_code": "credential"}])
def test_diagnostic_values_never_accept_arbitrary_text(value):
    assert safe_diagnostic(value) == {}


def test_real_worker_error_reports_sanitized_stage_not_exception_text(tmp_path, monkeypatch, caplog):
    from session_cloud import index_service
    worker = tmp_path / "dummy-worker.py"
    worker.write_text("""import sys,json
from pathlib import Path
json.loads(sys.stdin.buffer.readline())
p=json.loads(sys.stdin.buffer.readline())
out=Path(p['output'])
pending=out.with_suffix('.part')
pending.write_text(json.dumps({'error':'index_resource','diagnostic':{
 'stage':'embedding','reason':'memory_error','native_code':12,'secret':'dummy-secret'}}))
pending.replace(out)
""", encoding="utf-8")
    monkeypatch.setattr(index_service, "WORKER", worker)
    service = index_service.IndexService(tmp_path / "public-cache")
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), lambda: None)
    assert caught.value.reason == "memory_error"
    assert "worker_stage=embedding reason=memory_error" in caplog.text
    assert "native_code=12" in caplog.text and "dummy-secret" not in caplog.text
    assert not list(tmp_path.glob("index-*")) and service.process is None


def test_concurrent_worker_request_is_busy_not_a_memory_error(tmp_path, caplog):
    from session_cloud.index_service import IndexService
    service = IndexService(tmp_path / "public-cache")
    with service.lock, pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), lambda: None)
    assert caught.value.code == "index_busy" and "concurrent_request" in caplog.text


def test_invalid_snapshot_is_not_reported_as_storage_exhaustion(tmp_path):
    from session_cloud.index_engine import seal
    with pytest.raises(IndexFailure) as caught:
        seal(tmp_path, IndexLimits())
    assert caught.value.code == "index_invalid" and caught.value.reason == "invalid_snapshot"


def test_real_worker_exit_keeps_last_phase_and_does_not_claim_memory(tmp_path, monkeypatch, caplog):
    from session_cloud import index_service
    worker = tmp_path / "dummy-worker.py"
    worker.write_text("""import sys,json
from pathlib import Path
json.loads(sys.stdin.buffer.readline())
p=json.loads(sys.stdin.buffer.readline())
Path(p['output']).with_suffix('.diagnostic').write_text(json.dumps({'stage':'embedding_initialization'}))
sys.stderr.write('dummy-private-document-text')
raise SystemExit(7)
""", encoding="utf-8")
    monkeypatch.setattr(index_service, "WORKER", worker)
    service = index_service.IndexService(tmp_path / "public-cache")
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), lambda: None)
    assert caught.value.reason == "worker_exit"
    assert "worker_stage=embedding_initialization reason=worker_exit" in caplog.text
    assert "exit=7" in caplog.text and "dummy-private-document-text" not in caplog.text
    assert not list(tmp_path.glob("index-*"))


def test_real_pdf_chunk_guard_then_atomic_retry(native_environment, monkeypatch, caplog):
    """A real native reproduction of one guard, not proof of the cloud failure."""
    store, _, _ = native_environment
    text = "Solar energy measurements report twelve megawatts and overnight battery storage. " * 30
    ref, doc = test_session_retrieval.prepared(store, text=text)
    original = store.index_document
    def constrained(cap, workspace, document):
        return original(cap, workspace, document, limits=IndexLimits(chunks=1))
    monkeypatch.setattr(store, "index_document", constrained)
    store.check(A, ref)
    data = (store._folder(ref) / (doc + ".pdf")).read_bytes()
    with pytest.raises(SessionError) as caught:
        store.ingest_upload(A, ref, "sample.pdf", BytesIO(data))
    assert caught.value.code == "index_resource"
    assert "reason=chunk_capacity" in caplog.text and "worker_stage=chunking" in caplog.text
    assert store.documents(A, ref)[0]["status"] == "prepared"
    assert not list(store._folder(ref).glob("snapshot-*"))
    monkeypatch.setattr(store, "index_document", original)
    assert store.ingest_upload(A, ref, "sample.pdf", BytesIO(data), retry=True)["status"] == "indexed"
    hits = store.retrieve(A, ref, "solar capacity", [doc])
    assert hits and all(h["doc_id"] == doc and h["page_number"] == 1 for h in hits)
    with store._db(store.check(A, ref)) as db:
        snapshot = store._snapshot(db, ref)
        assert json.loads((Path(snapshot[0]) / "manifest.json").read_text())["version"] == 1
    store.delete_document(A, ref, doc)
    assert store.documents(A, ref) == []
    assert not list(store._folder(ref).glob("snapshot-*"))
    result = store.ingest_upload(A, ref, "sample.pdf", BytesIO(data))
    assert result["id"] == doc and not result["duplicate"]
    assert store._uploads == store._preparations == 2
    assert store.retrieve(A, ref, "solar capacity", [doc])
