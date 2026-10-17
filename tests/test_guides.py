"""Phase 3A original-contract comparison; all provider credentials are dummy."""
import ast
from dataclasses import asdict, replace
from io import BytesIO
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from session_cloud.analysis_service import GUIDE_QUERY
from session_cloud.analysis_view import brief_html, guide_html
from session_cloud.generation_budget import GenerationLimits, ProviderBudget
from session_cloud.groq_stream import GroqConfig, GroqStream
from session_cloud.original_context import RetrievedChunk
from session_cloud.original_guides import collection_brief_prompt, collection_parts, parse_source_guide, source_guide_prompt
from session_cloud.original_prompts import aggregator_node
from session_cloud.runtime import SessionError, WorkspaceStore

A, B = "a" * 64, "b" * 64
TEXT = "The Amber solar farm produces twelve megawatts of electricity from sunlight. Battery storage provides power after sunset for the surrounding community and hospital."
SUMMARY = "The document describes Amber solar electricity. It explains renewable power storage."
GUIDE = {"summary": SUMMARY, "key_topics": ["Solar generation", "Battery storage", "Electricity"]}
BRIEF = "This collection covers renewable electricity. Its themes include solar generation and storage. It can answer questions about energy capacity."
REFERENCE = Path(r"C:\Users\rudra\Downloads\DocRag\MULTIPLE-PDF-RAG-FINAL")


class Frames(httpx.SyncByteStream):
    def __init__(self, text, *, stop=True, pause=None):
        self.text, self.stop, self.pause, self.closed = text, stop, pause, False

    def __iter__(self):
        for piece in [self.text[:12], self.text[12:]]:
            yield b"data: " + json.dumps({"choices": [{"delta": {"content": piece}, "finish_reason": None}]}).encode() + b"\n\n"
            if self.pause:
                entered, release = self.pause
                entered.set()
                release.wait(3)
        if self.stop:
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    now, users = [1000.], {A: "user-a", B: "user-b"}
    requests, responses, contexts, streams = [], [], [], []

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "openai/gpt-oss-120b"
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        requests.append(payload)
        item = responses.pop(0) if responses else (Frames(json.dumps(GUIDE)) if payload["temperature"] == 0 else Frames(BRIEF))
        if isinstance(item, Exception):
            raise item
        if type(item) is int:
            return httpx.Response(item, text="dummy-private-provider-diagnostic")
        streams.append(item)
        return httpx.Response(200, stream=item)

    def authorize(token):
        if token not in users:
            raise SessionError("expired")
        return SimpleNamespace(user_id=users[token], deadline=10000)

    provider = GroqStream(GroqConfig("gsk_dummy_analysis_test_only"), transport=httpx.MockTransport(handler))
    store = WorkspaceStore(SimpleNamespace(authorize=authorize, logout=lambda t: users.pop(t, None)),
                           clock=lambda: now[0], base_dir=tmp_path, provider=provider)
    refs, docs = {}, {}
    for token in (A, B):
        ref = refs[token] = store.open(token)
        docs[token] = []
        for i in range(2 if token == A else 1):
            doc = store.upload(token, ref, f"{token[0]}-{i}.pdf", BytesIO(b"%PDF-1.7\n" + (token + str(i)).encode()))["id"]
            docs[token].append(doc)
        destination = store._destination(ref)
        with store._db(store.check(token, ref)) as db:
            db.execute("UPDATE documents SET status='indexed'")
            db.execute("INSERT INTO active_index VALUES(1,?,?)", (destination.name, "c" * 64))

    def context(capability, ref, question, selected, *, include_summaries=False, on_stage=None, cancelled=None, **kwargs):
        store.check(capability, ref)
        assert include_summaries and set(selected) <= set(docs[capability])
        assert not cancelled()
        contexts.append((capability, question, selected))
        for stage in ("searching", "reranking", "evidence"):
            on_stage({"stage": stage, "status": "active", "counts": {}})
            on_stage({"stage": stage, "status": "complete", "counts": {"selected_passages": len(selected)}})
        summaries = []
        for doc in selected:
            i = docs[capability].index(doc)
            c = RetrievedChunk(chunk_id=doc + "-l1", doc_id=doc, filename=f"{capability[0]}-{i}.pdf",
                title="Amber", page_number=1, page_end=2, section="full_document", text=TEXT,
                score=0., token_count=30, chunk_type="summary")
            summaries.append(asdict(c))
        state = aggregator_node({"retrieved_chunks": [RetrievedChunk(**s) for s in summaries]})
        return {"chunks": summaries, "selected_chunks": summaries, "document_summaries": summaries,
                "aggregated_evidence": state["aggregated_evidence"], "metrics": {"fixture": True}}

    monkeypatch.setattr(store, "prepare_answer_context", context)
    yield SimpleNamespace(store=store, refs=refs, docs=docs, now=now, users=users, contexts=contexts,
                          requests=requests, responses=responses, streams=streams, context=context)
    store._index_service.close()


def run(env, kind="guide", target=None, token=A, request_id="request-1"):
    target = target or (env.docs[token][0] if kind == "guide" else "collection")
    events = list(env.store.analyses.request(token, env.refs[token], kind, target, request_id))
    return next(e["analysis_result"] for e in events if e["type"] == "metadata")


def idle(env):
    deadline = time.monotonic() + 3
    while env.store.generation.gate.locked() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not env.store.generation.gate.locked()


def test_original_prompts_parser_and_collection_assembly_match_actual_source():
    # Read-only AST extraction from actual original functions, not copied expected
    # strings. No original module import/settings/database/provider initialization.
    if not REFERENCE.exists():
        pytest.skip("Original read-only source unavailable")
    task = ast.parse((REFERENCE / "app/workers/tasks.py").read_text(encoding="utf-8-sig"))
    original = next(n for n in task.body if isinstance(n, ast.FunctionDef) and n.name == "run_source_guide")
    prompt_assign = next(n for n in ast.walk(original) if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "prompt" for t in n.targets))
    l1 = TEXT * 12
    namespace = {"l1_text": l1}
    exec(compile(ast.Module(body=[prompt_assign], type_ignores=[]), "original-guide-prompt", "exec"), namespace)
    assert source_guide_prompt(l1) == namespace["prompt"]
    assert l1[:800] in namespace["prompt"] and l1[:801] not in namespace["prompt"]
    tree = ast.parse((REFERENCE / "app/api/routes/brief.py").read_text(encoding="utf-8-sig"))
    original = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_brief")
    loop = next(n for n in ast.walk(original) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "doc")
    docs = [SimpleNamespace(doc_id="one", filename="one.pdf"), SimpleNamespace(doc_id="two", filename="two.pdf")]
    guides = {"one": SimpleNamespace(summary=SUMMARY, key_topics=json.dumps(GUIDE["key_topics"]))}

    class Query:
        def filter_by(self, **values):
            self.doc = values["doc_id"]
            return self

        def first(self):
            return guides.get(self.doc)

    state = {"docs": docs, "parts": [], "db": SimpleNamespace(query=lambda _: Query()),
             "models": SimpleNamespace(SourceGuide=None), "json": json}
    exec(compile(ast.Module(body=[loop], type_ignores=[]), "original-brief-input", "exec"), state)
    assert collection_parts(docs, guides) == state["parts"]
    assignments = [n for n in ast.walk(original) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id in {"summaries", "prompt"} for t in n.targets)]
    exec(compile(ast.Module(body=assignments, type_ignores=[]), "original-brief-prompt", "exec"), state)
    assert collection_brief_prompt(state["parts"]) == state["prompt"]
    assert parse_source_guide("```json\n" + json.dumps(GUIDE) + "\n```") == GUIDE


def test_guide_and_collection_original_structures_settings_sources_and_cache(env):
    first = run(env)
    assert first["summary"] == SUMMARY and first["key_topics"] == GUIDE["key_topics"]
    assert first["doc_id"] == env.docs[A][0] and first["created_at"]
    assert first["input_sources"][0]["exact_quote"] == TEXT[:800]
    assert first["input_sources"][0]["page_end"] == 2 and first["provenance_only"]
    assert env.contexts[0] == (A, GUIDE_QUERY, [env.docs[A][0]])
    assert env.requests[0]["messages"][-1]["content"] == source_guide_prompt(TEXT)
    assert env.requests[0]["temperature"] == 0 and env.requests[0]["max_completion_tokens"] == 512
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) == first
    idle(env)
    brief = run(env, kind="brief", request_id="brief-1")
    assert brief["brief"] == BRIEF and brief["document_ids"] == env.docs[A]
    assert brief["missing_guide_ids"] == [env.docs[A][1]]
    assert env.contexts[-1][2] == env.docs[A]  # whole indexed collection, not chat selection
    assert "a-1.pdf: (no guide available)" in env.requests[-1]["messages"][-1]["content"]
    assert env.requests[-1]["temperature"] == 0.3 and env.requests[-1]["max_completion_tokens"] == 300
    assert env.store.get_collection_brief(A, env.refs[A]) == brief
    assert env.store.generation.budget.calls == 2
    assert env.store.analyses.budget is env.store.generation.budget
    assert env.store.analyses.gate is env.store.generation.gate
    assert all(s.closed for s in env.streams)


def test_reads_reruns_and_idempotent_replays_never_consume_more_ai(env):
    guide = run(env)
    idle(env)
    assert run(env) == guide
    for _ in range(3):
        assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) == guide
    assert len(env.requests) == 1


def test_new_guide_invalidates_brief_even_if_index_unchanged(env):
    run(env)
    idle(env)
    run(env, kind="brief", request_id="brief-1")
    idle(env)
    run(env, target=env.docs[A][1], request_id="guide-2")
    assert env.store.get_collection_brief(A, env.refs[A]) is None
    idle(env)
    with pytest.raises(SessionError) as exc:
        run(env, kind="brief", request_id="brief-1")
    assert exc.value.code == "analysis_stale" and len(env.requests) == 3


@pytest.mark.parametrize("foreign", ["workspace", "document", "cached"])
def test_two_user_reads_generation_and_cached_results_are_isolated(env, foreign):
    run(env)

    def operation():
        if foreign == "workspace":
            return list(env.store.analyses.request(B, env.refs[A], "guide", env.docs[A][0], "foreign"))
        if foreign == "document":
            return run(env, token=B, target=env.docs[A][0], request_id="foreign")
        return env.store.get_source_guide(B, env.refs[B], env.docs[A][0])
    with pytest.raises(SessionError):
        operation()
    assert len(env.requests) == 1
    assert env.store.get_collection_brief(B, env.refs[B]) is None


def test_no_guides_or_short_summary_never_fabricates_a_generic_brief(env):
    with pytest.raises(SessionError) as exc:
        run(env, kind="brief")
    assert exc.value.code == "analysis_input" and not env.requests
    original = env.context

    def context(*args, **kwargs):
        result = original(*args, **kwargs)
        result["document_summaries"][0]["text"] = "Too short."
        return result

    env.store.prepare_answer_context = context
    with pytest.raises(SessionError) as exc:
        run(env)
    assert exc.value.code == "analysis_input" and not env.requests


@pytest.mark.parametrize("response,code", [(401, "provider_auth"), (429, "provider_rate"),
    (httpx.ReadTimeout("dummy-private-value"), "provider_timeout"),
    (Frames('{"summary": "partial"}', stop=False), "generation_incomplete"),
    (Frames("not-json"), "analysis_invalid"), (Frames(json.dumps({**GUIDE, "extra": "bad"})), "analysis_invalid"),
    (Frames(json.dumps({**GUIDE, "key_topics": ["one"]})), "analysis_invalid"),
    (Frames(json.dumps({**GUIDE, "summary": "Fabricated reference [999]"})), "analysis_invalid")])
def test_provider_errors_and_malformed_outputs_are_not_saved_or_retried(env, response, code, caplog):
    env.responses.append(response)
    with pytest.raises(SessionError) as exc:
        run(env)
    assert exc.value.code == code
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) is None
    assert len(env.requests) == 1 and env.store.generation.budget.calls == 1
    assert "dummy-private" not in caplog.text + str(exc.value)
    idle(env)
    with pytest.raises(SessionError) as exc:
        run(env)
    assert exc.value.code == "generation_interrupted" and len(env.requests) == 1


def test_analysis_shares_chat_budget_and_does_not_refund_failures(env):
    limits = replace(GenerationLimits(), user_requests=1)
    env.store.generation.budget = ProviderBudget(limits, clock=env.store.clock)
    env.responses.append(429)
    with pytest.raises(SessionError):
        run(env)
    idle(env)
    with pytest.raises(SessionError) as exc:
        run(env, request_id="new-request")
    assert exc.value.code == "generation_budget" and len(env.requests) == 1


def test_analysis_busy_uses_same_gate_as_chat_and_cannot_bypass_it(env):
    assert env.store.generation.gate.acquire(blocking=False)
    try:
        with pytest.raises(SessionError) as exc:
            run(env)
        assert exc.value.code == "busy" and not env.requests
    finally:
        env.store.generation.gate.release()


def test_logout_and_consumer_interruption_revoke_publication(env):
    entered, release = threading.Event(), threading.Event()
    env.responses.append(Frames(json.dumps(GUIDE), pause=(entered, release)))
    events = env.store.analyses.request(A, env.refs[A], "guide", env.docs[A][0], "cancel")
    while True:
        event = next(events)
        if event.get("stage") == "generating" and event.get("status") == "active":
            break
    assert entered.wait(1)
    env.store.logout(A)
    events.close()
    release.set()
    idle(env)
    assert env.store.documents(B, env.refs[B])
    with pytest.raises(SessionError):
        env.store.get_source_guide(A, env.refs[A], env.docs[A][0])


def test_input_revision_race_fails_before_provider_and_old_replay_is_rejected(env):
    run(env)
    idle(env)
    with env.store._db(env.store.check(A, env.refs[A])) as db:
        db.execute("UPDATE active_index SET checksum=?", ("e" * 64,))
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) is None
    with pytest.raises(SessionError) as exc:
        run(env)
    assert exc.value.code == "analysis_stale"
    original = env.context

    def context(*args, **kwargs):
        value = original(*args, **kwargs)
        with env.store._db(env.store.check(A, env.refs[A])) as db:
            db.execute("UPDATE active_index SET checksum=?", ("f" * 64,))
        return value

    env.store.prepare_answer_context = context
    with pytest.raises(SessionError) as exc:
        run(env, request_id="raced")
    assert exc.value.code == "analysis_stale" and len(env.requests) == 1


def test_original_html_styles_escape_summary_topics_and_brief():
    rendered = guide_html({"summary": '<img src=x onerror="alert(1)">', "key_topics": ["<script>evil()</script>"]})
    assert "<img" not in rendered and "<script>" not in rendered and "#00D4FF" in rendered
    rendered = brief_html("<iframe src=bad></iframe>")
    assert "<iframe" not in rendered and "#4ADE80" in rendered


def test_original_provider_parameters_and_sidebar_html_match_actual_source():
    from html import escape
    if not REFERENCE.exists():
        pytest.skip("Original source required")
    for path, expected in [("app/workers/tasks.py", {"temperature": 0, "max_tokens": 512}),
                           ("app/api/routes/brief.py", {"temperature": 0.3, "max_tokens": 300})]:
        tree = ast.parse((REFERENCE / path).read_text(encoding="utf-8-sig"))
        assign = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_llm" for t in n.targets))
        assert {k.arg: ast.literal_eval(k.value) for k in assign.value.keywords if k.arg in expected} == expected
    tree = ast.parse((REFERENCE / "frontend/app.py").read_text(encoding="utf-8-sig"))
    values = {"guide": GUIDE, "_html": lambda x: escape(str(x), quote=True),
              "st": SimpleNamespace(session_state={"collection_brief": BRIEF})}
    strings = [n.args[0] for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args
               and isinstance(n.func, ast.Attribute) and n.func.attr == "markdown"
               and isinstance(n.args[0], ast.JoinedStr)]
    original_summary = next(n for n in strings if "font-size:0.82rem;margin-bottom:6px;" in ast.unparse(n))
    original_brief = next(n for n in strings if "COLLECTION BRIEF" in ast.unparse(n))
    tags = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "tags" for t in n.targets) and 'guide' in ast.unparse(n))
    exec(compile(ast.Module(body=[tags], type_ignores=[]), "original-tags", "exec"), values)
    assert guide_html(GUIDE) == eval(compile(ast.Expression(original_summary), "original-summary", "eval"), values) + values["tags"]
    assert brief_html(BRIEF) == eval(compile(ast.Expression(original_brief), "original-brief", "eval"), values)


def test_expiry_foreign_evidence_and_large_prompts_fail_closed(env):
    original = env.context

    def context(*args, **kwargs):
        result = original(*args, **kwargs)
        result["document_summaries"][0]["doc_id"] = env.docs[B][0]
        return result

    env.store.prepare_answer_context = context
    with pytest.raises(SessionError) as exc:
        run(env)
    assert exc.value.code == "context_invalid" and not env.requests
    idle(env)
    env.store.prepare_answer_context = original
    env.store.generation.budget = ProviderBudget(replace(GenerationLimits(), input_bound=10), clock=env.store.clock)
    with pytest.raises(SessionError) as exc:
        run(env, request_id="oversize")
    assert exc.value.code == "generation_context" and not env.requests
    env.now[0] += 3601
    with pytest.raises(SessionError) as exc:
        env.store.get_collection_brief(A, env.refs[A])
    assert exc.value.code == "expired"


def test_failed_regeneration_keeps_previous_valid_guide_and_does_not_save_partial(env):
    prior = run(env)
    idle(env)
    env.responses.append(Frames("partial", stop=False))
    with pytest.raises(SessionError):
        run(env, request_id="failed-regeneration")
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) == prior


def test_consumer_cancel_keeps_workspace_but_never_stores_partial_and_blocks_parallel_analysis(env):
    entered, release = threading.Event(), threading.Event()
    env.responses.append(Frames(json.dumps(GUIDE), pause=(entered, release)))
    events = env.store.analyses.request(A, env.refs[A], "guide", env.docs[A][0], "cancel-live")
    while True:
        event = next(events)
        if event.get("stage") == "generating" and event.get("status") == "active":
            break
    assert entered.wait(1)
    with pytest.raises(SessionError) as exc:
        run(env, token=B, request_id="parallel-b")
    assert exc.value.code == "busy"
    events.close()
    release.set()
    idle(env)
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) is None
    assert env.store.get_source_guide(B, env.refs[B], env.docs[B][0]) is None
    assert len(env.requests) == 1 and env.streams[0].closed


def test_two_users_generate_independent_guides_and_never_share_results(env):
    first = run(env)
    idle(env)
    second_data = {"summary": "This document covers clinical monitoring. It discusses patient safety.",
                   "key_topics": ["Clinical care", "Patient safety", "Monitoring"]}
    env.responses.append(Frames(json.dumps(second_data)))
    second = run(env, token=B, request_id="b-guide")
    assert second["summary"] == second_data["summary"]
    assert second["input_sources"][0]["doc_id"] == env.docs[B][0]
    assert env.docs[A][0] not in json.dumps(second)
    assert env.store.get_source_guide(A, env.refs[A], env.docs[A][0]) == first
    assert env.store.generation.budget.users["user-a"][0] == 1
    assert env.store.generation.budget.users["user-b"][0] == 1


def test_ui_rerun_with_running_analysis_never_resends_and_logout_clears_private_state(env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui
    monkeypatch.setattr(ui, "_fixture_store", env.store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    a.session_state["pending_analysis"] = {"workspace": env.refs[A], "kind": "guide", "target": env.docs[A][0],
                                           "id": "already-running", "status": "running"}
    a.run(timeout=20)
    assert not a.exception and not env.requests
    assert "pending_analysis" not in a.session_state
    next(b for b in a.button if b.label == "Log out").click().run(timeout=20)
    # render_workspace is invoked directly by this test, not the outer login
    # wrapper. Logout itself is verified by revoked server authority.
    assert A not in env.users


def test_ui_explicit_clicks_only_cache_reads_and_collection_isolation(env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui
    monkeypatch.setattr(ui, "_fixture_store", env.store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    assert not a.exception and not env.requests
    button = next(b for b in a.button if b.key == "guide_" + env.docs[A][0])
    button.click().run(timeout=30)
    assert not a.exception and len(env.requests) == 1
    assert any(SUMMARY in m.value for m in a.markdown)
    a.run(timeout=20)
    assert not a.exception and len(env.requests) == 1
    next(b for b in a.button if b.label == "Generate Collection Brief").click().run(timeout=30)
    assert not a.exception and len(env.requests) == 2
    assert any(BRIEF in m.value for m in a.markdown)
    b = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{B}')").run(timeout=20)
    assert not b.exception and not any(SUMMARY in m.value or BRIEF in m.value for m in b.markdown)


def test_native_guides_then_brief_reindex_and_delete_are_atomic(tmp_path):
    cache = os.environ.get("DOCUMIND_TEST_MODEL_CACHE")
    if not cache or not Path(cache).with_name(Path(cache).name + "-reranker").exists():
        pytest.skip("Native model cache required; no live provider test")
    import fitz
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, stream=Frames(json.dumps(GUIDE) if body["temperature"] == 0 else BRIEF))

    store = WorkspaceStore(SimpleNamespace(authorize=lambda _: SimpleNamespace(user_id="a", deadline=time.time() + 10000)),
        base_dir=tmp_path, model_cache=cache,
        provider=GroqStream(GroqConfig("gsk_dummy_native_analysis_test"), transport=httpx.MockTransport(handler)))
    ref = store.open(A)
    try:
        with fitz.open() as pdf:
            page = pdf.new_page()
            assert page.insert_textbox(fitz.Rect(40, 40, 550, 800), TEXT, fontsize=9) >= 0
            data = pdf.tobytes()
        doc = store.upload(A, ref, "public-fixture.pdf", BytesIO(data))["id"]
        store.prepare(A, ref, doc)
        store.index_document(A, ref, doc)
        for kind, target, rid in [("guide", doc, "guide"), ("brief", "collection", "brief")]:
            events = list(store.analyses.request(A, ref, kind, target, rid))
            assert any(e.get("completed") for e in events)
        guide = store.get_source_guide(A, ref, doc)
        brief = store.get_collection_brief(A, ref)
        assert guide["input_sources"][0]["doc_id"] == doc
        assert guide["input_sources"][0]["exact_quote"] in requests[0]["messages"][-1]["content"]
        assert guide["metrics"]["evidence"]["peak_rss_bytes"] > 0
        assert brief
        print(json.dumps({"scope": "one-page native PDF, mocked Groq guide and brief",
            "guide_metrics": guide["metrics"], "brief_metrics": brief["metrics"]}))
        store.index_document(A, ref, doc)  # same content checksum, new snapshot revision
        assert store.get_source_guide(A, ref, doc) is None
        assert store.get_collection_brief(A, ref) is None
        list(store.analyses.request(A, ref, "guide", doc, "guide-new"))
        store.delete_document(A, ref, doc)
        assert store.get_collection_brief(A, ref) is None
        with pytest.raises(SessionError):
            store.get_source_guide(A, ref, doc)
    finally:
        store._index_service.close()
