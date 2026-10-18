"""Automatic ingestion, bounded whole-passage context and isolated inline UI."""

from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import threading

import fitz
import pytest
import test_generation
import test_session_cloud

from session_cloud.adapter import LocalAPI
from session_cloud.analysis_workspace import input_state
from session_cloud.context_engine import validate_context, actual_token_count
from session_cloud.context_packing import pack_passages
from session_cloud.index_contract import IndexFailure
from session_cloud.original_context import RetrievedChunk
from session_cloud.original_prompts import aggregator_node
from session_cloud.runtime import SessionError

env = test_generation.env
environment = test_session_cloud.environment
A, B = test_generation.A, test_generation.B


def passage(i, *, doc="a", text=None):
    return RetrievedChunk(
        str(i),
        doc,
        doc + ".pdf",
        doc + ".pdf",
        i,
        i,
        "Results",
        text or f"Finding {i}. " + "measurement " * 350,
        10 - i,
        256,
    )


def test_complete_passages_fit_keep_strongest_pages_and_renumber():
    primary = [passage(1), passage(2, doc="b"), passage(3)]
    parent = replace(
        primary[0], chunk_id="parent", text="\n".join(c.text for c in primary) * 4
    )
    original = primary + [parent]
    packed, state, metrics = pack_passages(
        primary, original, count_tokens=lambda s: len(s.split()), byte_limit=10_000
    )
    assert packed[0] is primary[0] and packed[1] is primary[1]
    assert "parent" not in [c.chunk_id for c in packed]
    assert (
        state["aggregated_evidence"]
        == aggregator_node({"retrieved_chunks": packed})["aggregated_evidence"]
    )
    assert metrics["packed"] and metrics["omitted"] > 0
    assert all(
        c.text in state["aggregated_evidence"] and c.page_number == int(c.chunk_id)
        for c in packed
    )


def test_preserve_original_bundle_when_already_within_budget():
    items = [passage(1, text="Complete supporting passage.")]
    packed, state, metrics = pack_passages(
        items, items, count_tokens=lambda s: len(s.split())
    )
    assert packed is items and not metrics["packed"]
    assert state == aggregator_node({"retrieved_chunks": items})


def test_packed_citation_numbers_resolve_only_to_final_complete_passages():
    from session_cloud.attribution import AttributionError, format_attribution

    primary = [passage(1), passage(2), passage(3, doc="b")]
    parent = replace(
        primary[0], chunk_id="parent", text="\n".join(c.text for c in primary) * 4
    )
    packed, state, metrics = pack_passages(
        primary,
        [*primary, parent],
        count_tokens=lambda text: len(text.split()),
        byte_limit=10_000,
    )
    assert [c.chunk_id for c in packed] == ["1", "3"]
    assert "[2] b.pdf" in state["aggregated_evidence"]
    assert metrics["packed"] and metrics["omitted"] == 2
    result = format_attribution(
        "Finding 3 is supported [2].", packed, fallback=lambda text, _: text
    )
    citation = result["citations"][0]
    assert (citation["number"], citation["doc_id"], citation["page_number"]) == (
        2,
        "b",
        3,
    )
    assert citation["filename"] == "b.pdf"
    assert citation["exact_quote"].removesuffix("…") in packed[1].text
    with pytest.raises(AttributionError):
        format_attribution("Removed evidence [3].", packed)


@pytest.mark.parametrize(
    "metrics,disclosed",
    [
        ({"context_packing": {"packed": True}}, True),
        ({"prompt_packing": {"packed": True}}, True),
        (
            {
                "context_packing": {"packed": True},
                "prompt_packing": {"packed": False},
            },
            True,
        ),
        (
            {
                "context_packing": {"packed": False},
                "prompt_packing": {"packed": False},
            },
            False,
        ),
    ],
)
def test_completed_answer_discloses_omissions_from_either_packing_stage(
    metrics, disclosed
):
    from streamlit.testing.v1 import AppTest

    message = {"content": "Answer.", "meta": {"metrics": {"evidence": metrics}}}
    app = AppTest.from_string(
        "from session_cloud.chat_view import _render_completed_message\n"
        f"_render_completed_message(0, {message!r}, allow_followups=False)"
    ).run(timeout=25)
    assert not app.exception
    assert any("context was omitted" in c.value for c in app.caption) is disclosed


def test_unfit_strongest_evidence_for_one_document_fails_not_partial_comparison():
    items = [passage(1), passage(2, doc="b", text="data " * 20_000)]
    with pytest.raises(IndexFailure, match="context_limit"):
        pack_passages(items, items, count_tokens=lambda s: len(s.split()))


def test_utf8_and_token_budgets_not_stored_cost_are_authority():
    items = [passage(i, text=("value " * 600)) for i in range(1, 9)]
    packed, state, _ = pack_passages(
        items, items, count_tokens=lambda s: len(s.split()), token_limit=2000
    )
    assert (
        len(packed) < len(items) and len(state["aggregated_evidence"].split()) <= 2000
    )
    with pytest.raises(IndexFailure):
        pack_passages(
            [passage(1, text="界" * 12_000)],
            [passage(1, text="界" * 12_000)],
            count_tokens=lambda _: 1,
        )


def test_native_multi_page_parent_reproduces_old_guard_then_packs(tmp_path):
    cache = os.environ.get("DOCUMIND_TEST_MODEL_CACHE")
    if not cache:
        pytest.skip("Real public MiniLM cache required")
    from session_cloud.original_embedder import Embedder

    model = Embedder(Path(cache))
    # Same failure characteristics: many text pages, compact retrieval children,
    # and a parent whose stored truncated count is only 256. No private PDFs.
    with fitz.open() as pdf:
        for i in range(16):
            page = pdf.new_page()
            assert (
                page.insert_textbox(
                    fitz.Rect(35, 35, 560, 800),
                    f"Page {i + 1} findings. "
                    + "measurement results renewable evidence " * 100,
                    fontsize=7,
                )
                >= 0
            )
        path = tmp_path / "synthetic.pdf"
        pdf.save(path)
    with fitz.open(path) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
    child = passage(1, text=text[:1800])
    parent = replace(child, chunk_id="parent", text=text, page_end=16)
    with pytest.raises(IndexFailure, match="context_limit"):
        validate_context(aggregator_node({"retrieved_chunks": [child, parent]}), model)
    packed, state, metrics = pack_passages(
        [child],
        [child, parent],
        count_tokens=lambda s, counter=model: actual_token_count(counter, s),
    )
    assert (
        packed == [child]
        and metrics["packed"]
        and validate_context(state, model) <= 8192
    )
    from types import SimpleNamespace
    import gc
    import time
    from session_cloud.runtime import WorkspaceStore

    del model
    gc.collect()
    auth = SimpleNamespace(
        authorize=lambda _: SimpleNamespace(
            user_id="synthetic", deadline=time.time() + 10000
        )
    )
    store = WorkspaceStore(auth, base_dir=tmp_path, model_cache=Path(cache))
    ref = store.open(A)
    try:
        with path.open("rb") as stream:
            result = store.ingest_upload(A, ref, "synthetic.pdf", stream)
        context = store.prepare_answer_context(
            A, ref, "What measurements are reported?", [result["id"]]
        )
        assert context["chunks"] and context["metrics"]["context_packing"]["packed"]
        assert all(
            c["doc_id"] == result["id"] and c["page_number"] >= 1
            for c in context["chunks"]
        )
        with path.open("rb") as stream:
            assert store.ingest_upload(A, ref, "duplicate.pdf", stream)["duplicate"]
        assert store._uploads == store._preparations == store._indexing == 1
    finally:
        store._index_service.close()


def test_auto_ingestion_complete_duplicate_and_rerun_do_not_spend_again(
    environment, monkeypatch
):
    store, _, _ = environment
    ref = store.open(A)
    calls = []

    def prepare(cap, ws, doc):
        calls.append("prepare")
        with store._db(store.check(cap, ws)) as db:
            db.execute("UPDATE documents SET status='prepared' WHERE id=?", (doc,))

    def index(cap, ws, doc):
        calls.append("index")
        with store._db(store.check(cap, ws)) as db:
            db.execute("UPDATE documents SET status='indexed' WHERE id=?", (doc,))

    monkeypatch.setattr(store, "prepare", prepare)
    monkeypatch.setattr(store, "index_document", index)
    for i in range(3):
        result = store.ingest_upload(A, ref, f"{i}.pdf", BytesIO(b"%PDF-1.7 dummy"))
        assert result["status"] == "indexed"
    assert calls == ["prepare", "index"] and store._uploads == 1
    assert len(store.documents(A, ref)) == 1


def test_failed_ingestion_requires_explicit_retry_and_resumes(environment, monkeypatch):
    store, _, _ = environment
    ref = store.open(A)
    calls = []

    def fail(*args):
        calls.append(1)
        raise SessionError("pdf_invalid")

    monkeypatch.setattr(store, "prepare", fail)
    for _ in range(3):
        with pytest.raises(SessionError):
            store.ingest_upload(A, ref, "a.pdf", BytesIO(b"%PDF-1.7 dummy"))
    assert len(calls) == 1 and store._uploads == 1
    with pytest.raises(SessionError):
        store.ingest_upload(A, ref, "a.pdf", BytesIO(b"%PDF-1.7 dummy"), retry=True)
    assert len(calls) == 2 and store._uploads == 1


def test_concurrent_duplicate_is_busy_and_other_workspace_cannot_ingest(
    environment, monkeypatch
):
    store, _, _ = environment
    ref, other = store.open(A), store.open(B)
    entered, release = threading.Event(), threading.Event()

    def prepare(*args):
        entered.set()
        release.wait(10)
        raise SessionError("pdf_time")

    monkeypatch.setattr(store, "prepare", prepare)
    errors = []

    def ingest():
        try:
            store.ingest_upload(A, ref, "a.pdf", BytesIO(b"%PDF-1.7 dummy"))
        except SessionError as error:
            errors.append(error.code)

    t = threading.Thread(target=ingest)
    t.start()
    assert entered.wait(5)
    try:
        with pytest.raises(SessionError, match="progress|operation"):
            store.ingest_upload(A, ref, "a.pdf", BytesIO(b"%PDF-1.7 dummy"))
        with pytest.raises(SessionError):
            store.ingest_upload(B, ref, "a.pdf", BytesIO(b"%PDF-1.7 dummy"))
    finally:
        release.set()
        t.join(10)
    assert store.documents(B, other) == [] and errors == ["pdf_time"]


def questions(e):
    ref = e.refs[A]
    with e.store._db(e.store.check(A, ref)) as db:
        state = input_state(e.store, e.store.check(A, ref), db, "questions", "")
        payload = {
            "kind": "questions",
            "document_ids": [e.docs[A]],
            "input_sources": [],
            "items": [
                {"id": "question1", "question": "What is Amber's generating capacity?"}
            ],
        }
        db.execute(
            "INSERT INTO analysis_results VALUES(?,?,?,?,?)",
            (
                "questions",
                "",
                state["revision"],
                state["fingerprint"],
                json.dumps(payload),
            ),
        )


def test_inline_backend_replay_isolation_and_stale_invalidation(env):
    e = env
    questions(e)
    thread = e.store.question_thread(A, e.refs[A], "", "question1", create=True)
    api = LocalAPI(e.store, A, e.refs[A], thread["chat"], thread["documents"])
    _, meta = api.parse_stream(
        api.query_stream(
            thread["question"], e.refs[A], thread["chat"], turn_id="inline1"
        )
    )
    assert meta["completed"] and e.store.question_answer(A, e.refs[A], "", "question1")
    assert (
        e.store.chats(A, e.refs[A]) == [e.chats[A]]
        and e.store.history(A, e.refs[A], e.chats[A]) == []
    )
    list(
        api.query_stream(
            thread["question"], e.refs[A], thread["chat"], turn_id="inline1"
        )
    )
    assert len(e.requests) == 2
    with pytest.raises(SessionError):
        e.store.question_answer(B, e.refs[B], "", "question1")
    with pytest.raises(SessionError):
        e.store.question_answer(B, e.refs[A], "", "question1")
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        e.store._clear_question_answers(db)
        db.execute("DELETE FROM analysis_results")
    with pytest.raises(SessionError):
        e.store.question_answer(A, e.refs[A], "", "question1")


def test_inline_ui_does_not_touch_chat_and_rerun_never_repeats(env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui

    e = env
    questions(e)
    monkeypatch.setattr(ui, "_fixture_store", e.store, raising=False)
    app = AppTest.from_string(
        f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store,'{A}')"
    ).run(timeout=25)
    chat = app.session_state["active_chat"]
    app.button(key="ask_question1").click().run(timeout=35)
    assert (
        not app.exception
        and app.session_state["active_chat"] == chat
        and app.session_state["messages"] == []
    )
    assert any(test_generation.ANSWER.split(" [1]")[0] in m.value for m in app.markdown)
    app.run(timeout=25)
    assert not app.exception and len(e.requests) == 2
    assert any(e.label.startswith("Sources") for e in app.expander)


def test_deleting_selected_upload_does_not_ingest_it_again_on_rerun(env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui

    e = env
    uploaded = BytesIO(b"%PDF-1.7\n" + A.encode())
    uploaded.file_id, uploaded.name = "selected-upload", "a.pdf"
    calls = []
    ingest = e.store.ingest_upload
    delete = e.store.delete_document

    def delete_fixture(capability, ref, doc_id):
        # The fixture's snapshot is only a placeholder. At deletion use the
        # real empty-index path; native snapshot removal is tested separately.
        with e.store._db(e.store.check(capability, ref)) as db:
            db.execute("DELETE FROM active_index")
        return delete(capability, ref, doc_id)

    def count_ingest(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise SessionError("upload_interrupted")
        return ingest(*args, **kwargs)

    monkeypatch.setattr(e.store, "ingest_upload", count_ingest)
    monkeypatch.setattr(e.store, "delete_document", delete_fixture)
    monkeypatch.setattr(ui.st, "file_uploader", lambda *a, **kw: uploaded)
    monkeypatch.setattr(ui, "_fixture_store", e.store, raising=False)
    app = AppTest.from_string(
        f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store,'{A}')"
    ).run(timeout=30)
    assert not app.exception and calls == [1]
    uploads_before = e.store._uploads
    app.button(key="delete_" + e.docs[A]).click().run(timeout=30)
    app.run(timeout=30)
    assert not app.exception
    assert e.store.documents(A, e.refs[A]) == []
    assert calls == [1] and e.store._uploads == uploads_before
    assert not app.success


def test_inline_interruption_rerun_and_explicit_retry(env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui

    e = env
    questions(e)
    remote, requests, _ = test_generation.provider(
        [
            test_generation.wire("Partial", done=False),
            test_generation.wire(test_generation.ANSWER),
            test_generation.wire(test_generation.FOLLOW),
        ]
    )
    e.store.generation.provider = remote
    monkeypatch.setattr(ui, "_fixture_store", e.store, raising=False)
    app = AppTest.from_string(
        f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store,'{A}')"
    ).run(timeout=25)
    app.button(key="ask_question1").click().run(timeout=35)
    assert (
        not app.exception
        and e.store.question_answer(A, e.refs[A], "", "question1") is None
    )
    app.run(timeout=25)
    assert len(requests) == 1
    app.button(key="ask_question1").click().run(timeout=35)
    assert (
        not app.exception
        and len(requests) == 3
        and e.store.question_answer(A, e.refs[A], "", "question1")
    )


def test_inline_topic_change_cancels_without_sending_or_touching_chat(env):
    from session_cloud.inline_answers import reconcile_inline_scope

    e = env
    questions(e)
    thread = e.store.question_thread(A, e.refs[A], "", "question1", create=True)
    state = {
        "pending_inline": {
            "scope": (e.refs[A], "", "question1"),
            "chat": thread["chat"],
            "id": "old",
        },
        "q_topic_input": "new topic",
        "active_chat": e.chats[A],
    }
    reconcile_inline_scope(e.store, A, e.refs[A], state)
    assert (
        "pending_inline" not in state
        and state["active_chat"] == e.chats[A]
        and not e.requests
    )
    assert (
        state["inline_failures"][(e.refs[A], "", "question1")]
        == "generation_interrupted"
    )


@pytest.mark.parametrize("action", ["logout", "expiry"])
def test_inline_reads_reject_revoked_sessions(env, action):
    e = env
    questions(e)
    if action == "logout":
        e.store.logout(A)
    else:
        e.now[0] += 3601
    with pytest.raises(SessionError):
        e.store.question_answer(A, e.refs[A], "", "question1")
    assert not e.requests


def test_upload_ui_identity_skips_reruns_and_keeps_failures_actionable(
    environment, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from session_cloud.upload_workflow import render_upload

    store, _, _ = environment
    ref = store.open(A)
    upload = BytesIO(b"%PDF-1.7 fixture")
    upload.file_id, upload.name = "widget-file-1", "fixture.pdf"
    state, messages, calls = {}, [], []

    @contextmanager
    def status(*args, **kwargs):
        messages.append(args[0])
        yield SimpleNamespace(update=lambda **kw: messages.append(kw.get("label")))

    def ingest(*args, **kwargs):
        calls.append(kwargs.get("retry"))
        raise SessionError("pdf_time")

    monkeypatch.setattr(store, "ingest_upload", ingest)
    ui = SimpleNamespace(
        status=status,
        warning=messages.append,
        success=messages.append,
        button=lambda *a, **kw: False,
        rerun=lambda: None,
    )
    for _ in range(3):
        render_upload(store, A, ref, upload, state, ui)
    assert (
        calls == [False] and messages and state["upload_feedback"]["code"] == "pdf_time"
    )
    ui.button = lambda *a, **kw: True
    render_upload(store, A, ref, upload, state, ui)
    assert calls == [False, True] and store.documents(A, ref) == []


def test_upload_ceiling_and_ledger_capacity_fail_closed(environment):
    store, _, _ = environment
    ref = store.open(A)
    store.limits = replace(store.limits, upload_bytes=10)
    with pytest.raises(SessionError):
        store.ingest_upload(A, ref, "large.pdf", BytesIO(b"%PDF-1.7 fixture"))
    assert store._uploads == 0 and not store.documents(A, ref)
    store.limits = replace(store.limits, upload_bytes=100)
    store._ingestions = {(ref, str(i)): {"status": "failed"} for i in range(64)}
    with pytest.raises(SessionError):
        store.ingest_upload(A, ref, "new.pdf", BytesIO(b"%PDF-1.7 new"))
    assert store._uploads == 0


def test_inline_bulk_read_authenticates_once_and_rejects_foreign_citation(
    env, monkeypatch
):
    e = env
    questions(e)
    thread = e.store.question_thread(A, e.refs[A], "", "question1", create=True)
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute(
            "INSERT INTO turns VALUES(?,?,?,?,?)",
            (
                thread["chat"],
                "corrupt",
                "q",
                "a",
                json.dumps(
                    {
                        "completed": True,
                        "document_ids": [e.docs[A]],
                        "citations": [{"doc_id": "foreign"}],
                    }
                ),
            ),
        )
    calls = []
    original = e.store.auth.authorize
    monkeypatch.setattr(
        e.store.auth, "authorize", lambda cap: (calls.append(cap), original(cap))[1]
    )
    with pytest.raises(SessionError):
        e.store.question_answers(A, e.refs[A], "")
    assert calls == [A] and not e.requests


def test_inline_thread_cap_preserves_manual_chat_allowance_and_ai_budgets(env):
    e = env
    questions(e)
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.executemany(
            "INSERT INTO chats VALUES(?)", [("question-" + str(i),) for i in range(10)]
        )
    with pytest.raises(SessionError) as error:
        e.store.question_thread(A, e.refs[A], "", "question1", create=True)
    assert error.value.code == "inline_limit" and not e.requests
    assert e.store.chats(A, e.refs[A]) == [e.chats[A]]
    assert e.store.new_chat(A, e.refs[A]) in e.store.chats(A, e.refs[A])
