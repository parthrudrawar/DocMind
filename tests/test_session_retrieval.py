"""Phase 2B real native algorithms and subprocess tests; no external providers."""
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import fitz
import numpy as np
import pytest

from session_cloud.index_contract import IndexFailure, IndexLimits
from session_cloud.index_engine import build, load, search
from session_cloud.runtime import SessionError, WorkspaceStore

A, B = "a" * 64, "b" * 64


@pytest.fixture(scope="module")
def model_cache():
    path = os.environ.get("DOCUMIND_TEST_MODEL_CACHE")
    if not path:
        pytest.skip("Actual MiniLM cache not supplied; not a retrieval-parity pass")
    return Path(path)


@pytest.fixture(scope="module")
def model(model_cache):
    from session_cloud.original_embedder import Embedder
    return Embedder(model_cache)


@pytest.fixture
def environment(tmp_path, model_cache):
    now, users = [1000.], {A: "11111111-1111-4111-8111-111111111111", B: "22222222-2222-4222-8222-222222222222"}

    def authorize(token):
        if token not in users:
            raise SessionError("expired")
        return SimpleNamespace(user_id=users[token], deadline=10000)

    store = WorkspaceStore(SimpleNamespace(authorize=authorize, logout=lambda t: users.pop(t, None)),
                           clock=lambda: now[0], base_dir=tmp_path, model_cache=model_cache)
    yield store, now, users
    store._index_service.close()


def prepared(store, token=A, text="The Amber solar farm generates twelve megawatts of electricity from sunlight. Batteries store renewable power overnight."):
    ref = store.open(token)
    with fitz.open() as pdf:
        page = pdf.new_page()
        assert page.insert_textbox(fitz.Rect(40, 40, 550, 800), text, fontsize=8) >= 0
        data = pdf.tobytes()
    doc = store.upload(token, ref, "fixture.pdf", BytesIO(data))["id"]
    store.prepare(token, ref, doc)
    return ref, doc


def test_real_model_atomic_index_search_reindex_delete_and_selection(environment):
    store, _, _ = environment
    ref, da = prepared(store)
    _, db = prepared(store, text="Drug adverse reactions include nausea and dizziness. Patients should immediately inform their physician of severe symptoms.")
    result = store.index_document(A, ref, da)
    pid = store._index_service.process.pid
    assert result["status"] == "indexed" and result["metrics"]["peak_rss_bytes"] > 0
    first = store.retrieve(A, ref, "solar energy capacity", [da])
    assert first and "twelve megawatts" in first[0]["text"] and first[0]["page_number"] == 1
    assert first[0]["parent_chunk_id"] and first[0]["filename"] == "fixture.pdf"
    store.index_document(A, ref, db)
    assert store._index_service.process.pid == pid  # one shared model, no per-user model load
    assert all(h["doc_id"] == db for h in store.retrieve(A, ref, "side effects", [db]))
    store.index_document(A, ref, da)
    assert store.retrieve(A, ref, "solar energy capacity", [da])[0]["chunk_id"] == first[0]["chunk_id"]
    with store._db(store.check(A, ref)) as connection:
        snapshot = store._snapshot(connection, ref)[0]
        files = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
        assert "chunks.json" in files and not any(p.endswith((".pkl", ".pickle")) for p in files)
    store.delete_document(A, ref, da)
    with pytest.raises(SessionError):
        store.retrieve(A, ref, "solar", [da])
    assert all(h["doc_id"] == db for h in store.retrieve(A, ref, "medicine", [db]))
    store.delete_document(A, ref, db)
    assert store.documents(A, ref) == [] and not list(store._folder(ref).glob("snapshot-*"))


def test_two_simultaneous_users_and_no_cross_workspace_queries(environment):
    store, _, _ = environment
    wa, da = prepared(store)
    wb, db = prepared(store, B, "The private cobalt laboratory stores blood samples at four degrees Celsius for transport. The maximum transit time is two hours.")
    store.index_document(A, wa, da)
    shared_pid = store._index_service.process.pid
    store.index_document(B, wb, db)
    assert store._index_service.process.pid == shared_pid
    for operation in [lambda: store.retrieve(B, wa, "solar", [da]),
                      lambda: store.retrieve(B, wb, "solar", [da]),
                      lambda: store.index_document(B, wb, da), lambda: store.delete_document(B, wb, da)]:
        with pytest.raises(SessionError):
            operation()
    assert all("cobalt" not in h["text"] for h in store.retrieve(A, wa, "blood", [da]))
    assert all("Amber" not in h["text"] for h in store.retrieve(B, wb, "solar", [db]))
    # Concurrent operations fail busy, never mix buffers/results or reuse IDs.
    entered, release = threading.Event(), threading.Event()
    real = store._index_service.run

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return real(*args, **kwargs)

    store._index_service.run = blocked
    errors = []

    def run():
        try:
            store.retrieve(A, wa, "solar", [da])
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(SessionError) as caught:
            store.retrieve(B, wb, "blood", [db])
        assert caught.value.code == "busy"
    finally:
        release.set()
        thread.join(10)
    assert not errors and not thread.is_alive()


def test_failed_reindex_preserves_complete_snapshot(environment):
    store, _, _ = environment
    ref, doc = prepared(store)
    store.index_document(A, ref, doc)
    before = store.retrieve(A, ref, "capacity", [doc])
    real = store._index_service.run

    def failure(request, folder, limits, active):
        Path(request["destination"], "partial.index").write_bytes(b"incomplete")
        raise IndexFailure("index_resource")

    store._index_service.run = failure
    with pytest.raises(SessionError):
        store.index_document(A, ref, doc)
    store._index_service.run = real
    assert store.retrieve(A, ref, "capacity", [doc]) == before
    assert len(list(store._folder(ref).glob("snapshot-*"))) == 1


def test_failed_first_index_not_searchable_and_chunk_limit(environment):
    store, _, _ = environment
    ref, doc = prepared(store, text=("The solar capacity is twelve megawatts and the battery backup serves the hospital during emergency outages. " * 60))
    with pytest.raises(SessionError) as error:
        store.index_document(A, ref, doc, limits=IndexLimits(chunks=1))
    assert error.value.code == "index_resource"
    assert store.documents(A, ref)[0]["status"] == "prepared"
    with pytest.raises(SessionError):
        store.retrieve(A, ref, "solar", [doc])
    assert not list(store._folder(ref).glob("snapshot-*"))


def test_corrupted_index_fails_closed_then_reindex_repairs(environment):
    store, _, _ = environment
    ref, doc = prepared(store)
    store.index_document(A, ref, doc)
    with store._db(store.check(A, ref)) as connection:
        snapshot = store._snapshot(connection, ref)[0]
    (snapshot / "vectors.npy").write_bytes(b"corruption is not pickle")
    with pytest.raises(SessionError) as error:
        store.retrieve(A, ref, "solar", [doc])
    assert error.value.code == "index_invalid"
    store.index_document(A, ref, doc)
    assert store.retrieve(A, ref, "solar", [doc])


def test_expiry_logout_and_restart_do_not_restore_old_indexes(environment, tmp_path, model_cache):
    store, now, _ = environment
    ref, doc = prepared(store)
    store.index_document(A, ref, doc)
    replacement = WorkspaceStore(store.auth, base_dir=tmp_path, clock=lambda: now[0], model_cache=model_cache)
    with pytest.raises(SessionError):
        replacement.retrieve(A, ref, "solar", [doc])
    fresh = replacement.open(A)
    assert replacement.documents(A, fresh) == []
    store.logout(A)
    with pytest.raises(SessionError):
        store.retrieve(A, ref, "solar", [doc])
    assert not store._leases
    replacement._index_service.close()


@pytest.mark.parametrize("kwargs", [{"query": ""}, {"query": "x" * 501}, {"selected": []},
                                    {"selected": ["../other"]}, {"top_k": 31}, {"top_k": True}])
def test_malformed_retrieval_requests(environment, kwargs):
    store, _, _ = environment
    ref = store.open(A)
    request = {"query": "solar", "selected": ["0" * 64], **kwargs}
    with pytest.raises(SessionError):
        store.retrieve(A, ref, **request)


def test_native_snapshot_hash_model_dimension_guards(tmp_path, model):
    text = "Solar power at the Amber farm produces twelve megawatts of clean electricity."
    page = dict(page_number=1, text=text, paragraphs=[text], table_blocks=[], has_tables=False, is_ocr=False, word_count=len(text.split()))
    result = build([{"id": "f" * 64, "filename": "fixture.pdf", "pages": [page]}], tmp_path, model, IndexLimits())
    chunks, vectors = load(tmp_path, result["checksum"], IndexLimits())
    assert vectors.shape[1] == 384 and np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert search(tmp_path, result["checksum"], "solar", ["f" * 64], model, IndexLimits())
    with pytest.raises(IndexFailure):
        load(tmp_path, "0" * 64, IndexLimits())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["model"] = "wrong-model"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    import hashlib
    with pytest.raises(IndexFailure):
        load(tmp_path, hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest(), IndexLimits())


@pytest.mark.parametrize("field", ["chunks", "text_bytes", "snapshot_bytes", "memory_bytes", "seconds"])
def test_limits_cannot_be_relaxed(field):
    with pytest.raises(ValueError):
        replace(IndexLimits(), **{field: getattr(IndexLimits(), field) + 1})


def test_real_child_timeout_is_killed_without_publication(tmp_path):
    import subprocess
    import sys
    import time
    from session_cloud.index_service import IndexService

    class SlowService(IndexService):
        def _start(self, limits):
            self.process = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                "import sys,time;sys.stdin.buffer.readline();time.sleep(30)"], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
            self.child = self.process

    service = SlowService(tmp_path / "public-cache")
    started = time.monotonic()
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(seconds=1), lambda: None)
    assert caught.value.code == "index_time" and time.monotonic() - started < 8
    assert service.child.poll() is not None
    assert not list(tmp_path.glob("index-*.json"))


def test_real_child_low_memory_limit_fails_closed(tmp_path, model_cache):
    from session_cloud.index_service import IndexService
    service = IndexService(model_cache)
    with pytest.raises(IndexFailure):
        service.run({"action": "build", "documents": []}, tmp_path, IndexLimits(memory_bytes=1024 * 1024), lambda: None)
    assert service.process is None


def test_disk_reservation_and_indexing_budget_cannot_publish(environment):
    store, _, _ = environment
    ref, doc = prepared(store)
    store._indexing = 20
    with pytest.raises(SessionError) as caught:
        store.index_document(A, ref, doc)
    assert caught.value.code == "limit"
    store._indexing = 0
    store.limits = replace(store.limits, workspace_bytes=4096)
    with pytest.raises(SessionError) as caught:
        store.index_document(A, ref, doc)
    assert caught.value.code == "disk"
    assert not list(store._folder(ref).glob("snapshot-*"))


def test_index_expiry_during_build_revokes_and_kills_worker(environment):
    store, now, _ = environment
    ref, doc = prepared(store)
    real = store._index_service.run

    def expire(request, folder, limits, active):
        now[0] += 3601
        active()
        return real(request, folder, limits, active)

    store._index_service.run = expire
    with pytest.raises(SessionError) as caught:
        store.index_document(A, ref, doc)
    assert caught.value.code == "expired"
    assert not (store.root / ref).exists()


def test_identity_change_before_publication_is_rejected(environment):
    store, _, users = environment
    ref, doc = prepared(store)
    wb = store.open(B)
    owner = users[A]
    real = store._index_service.run

    def changed_identity(*args):
        result = real(*args)
        users[A] = users[B]
        return result

    store._index_service.run = changed_identity
    with pytest.raises(SessionError):
        store.index_document(A, ref, doc)
    users[A] = owner
    assert store.documents(A, ref)[0]["status"] == "prepared"
    assert not list(store._folder(ref).glob("snapshot-*"))
    assert store.documents(B, wb) == []


def test_real_worker_secret_environment_and_diagnostics_are_sanitized(tmp_path, monkeypatch, caplog, capsys):
    from session_cloud import index_service
    monkeypatch.setenv("GROQ_API_KEY", "dummy-private-provider-sentinel")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dummy-private-provider-sentinel")
    worker = tmp_path / "dummy-worker.py"
    worker.write_text("""import sys,os,json
from pathlib import Path
json.loads(sys.stdin.buffer.readline())
paths=json.loads(sys.stdin.buffer.readline())
assert 'GROQ_API_KEY' not in os.environ and 'SUPABASE_SERVICE_ROLE_KEY' not in os.environ
print('dummy-private-provider-sentinel')
sys.stderr.write('dummy-private-provider-sentinel')
output=Path(paths['output'])
pending=output.with_suffix('.part')
pending.write_text(json.dumps({'error':'index_invalid'}),encoding='utf-8')
pending.replace(output)
""", encoding="utf-8")
    monkeypatch.setattr(index_service, "WORKER", worker)
    service = index_service.IndexService(tmp_path / "public-cache")
    with pytest.raises(IndexFailure) as caught:
        service.run({"action": "build"}, tmp_path, IndexLimits(), lambda: None)
    assert caught.value.code == "index_invalid"
    captured = capsys.readouterr()
    assert "dummy-private-provider-sentinel" not in caplog.text + captured.out + captured.err
    assert "documind_index stage=" in caplog.text


def test_idle_model_is_released_before_another_pdf_parser(environment, monkeypatch):
    from session_cloud import parser_runner
    store, _, _ = environment
    ref, doc = prepared(store)
    store.index_document(A, ref, doc)
    child = store._index_service.process
    original = parser_runner.run_parser

    def checked(*args):
        assert store._index_service.process is None and child.poll() is not None
        return original(*args)

    monkeypatch.setattr(parser_runner, "run_parser", checked)
    prepared(store, B, "A laboratory stores blood samples under refrigeration at four degrees Celsius. The maximum transit time to the laboratory is two hours.")
    assert child.poll() is not None


def test_linux_public_model_download_and_snapshot_file_caps_are_separate(monkeypatch):
    import sys
    from session_cloud.index_worker import unix_file_limit
    calls = []
    resource = SimpleNamespace(RLIMIT_FSIZE=1, setrlimit=lambda *args: calls.append(args))
    monkeypatch.setitem(sys.modules, "resource", resource)
    unix_file_limit(loading_model=True)
    unix_file_limit(loading_model=False)
    assert calls == [(1, (160 * 1024**2, 160 * 1024**2)), (1, (16 * 1024**2, 160 * 1024**2))]
