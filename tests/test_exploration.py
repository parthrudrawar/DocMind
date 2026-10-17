"""Phase 3C original-source and all-feature integration. Providers are MOCKED."""
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
from io import BytesIO
import json
import logging
import os
import re
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import test_guides

from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document
from session_cloud.exploration_results import parse_exploration
from session_cloud.exploration_view import apply_question_action, insight_card, year_header, timeline_card
from session_cloud.generation_budget import GenerationLimits, ProviderBudget
from session_cloud.groq_stream import GroqConfig, GroqStream
from session_cloud.original_exploration import exploration_inputs, question_messages, insight_messages, timeline_messages, format_timeline
from session_cloud.runtime import SessionError, WorkspaceStore
from test_comparisons import original_functions, chunks_for
from test_guides import A, B, Frames, TEXT, GUIDE, BRIEF, idle

guide_env = test_guides.env
QUESTIONS = ["What produces electricity?", "How is solar energy stored?", "What powers the hospital?", "How do the documents compare?", "What evidence is missing?"]
INSIGHT = {"insight_type": "pattern", "title": "Shared renewable energy theme", "description": "The documents discuss solar power and storage.", "supporting_docs": ["a-0.pdf"]}


def timeline(filename, year="2021", claim="Solar storage supports the hospital."):
    return {"year": year, "filename": filename, "events": [{"claim": claim, "importance": "high"}]}


@pytest.fixture
def exploration_env(guide_env, monkeypatch):
    e = guide_env
    e.worker_calls = []

    def worker(request, folder, limits, active):
        active()
        docs = request["documents"]
        rows = chunks_for(docs, [TEXT] * len(docs))
        rows += [dict(c, level=2, chunk_id=c["chunk_id"] + "section", chunk_type="section") for c in rows if c["level"] == 1]
        e.worker_calls.append(request)
        return {"documents": exploration_inputs(rows, docs, request["kind"]), "metrics": {"fixture": True}}

    monkeypatch.setattr(e.store._index_service, "run", worker)
    return e


def respond(e, kind, *, token=A):
    if kind == "questions":
        e.responses.append(Frames(json.dumps(QUESTIONS)))
    elif kind == "insights":
        e.responses.append(Frames(json.dumps([dict(INSIGHT, supporting_docs=[f"{token[0]}-0.pdf"])])))
    else:
        for i, _ in enumerate(e.docs[token]):
            e.responses.append(Frames(json.dumps(timeline(f"{token[0]}-{i}.pdf", str(2021-i), f"Finding {i}."))))


def run(e, kind, *, topic="", rid="run", token=A):
    events = list(e.store.analyses.request(token, e.refs[token], kind, "collection" if kind == "timeline" else topic, rid))
    assert events[-1]["type"] == "done"
    return next(v["analysis_result"] for v in events if v["type"] == "metadata")


def get(e, kind, *, topic="", token=A):
    method = getattr(e.store, "get_" + kind)
    return method(token, e.refs[token]) if kind == "timeline" else method(token, e.refs[token], topic)


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("kind", ["questions", "insights", "timeline"])
def test_actual_original_loaders_prompts_parameters_and_outputs_match(kind, fallback):
    docs = [{"id": A, "filename": "A.pdf", "upload_date": "2026-10-01"},
            {"id": B, "filename": "B.pdf", "upload_date": "2026-10-02"}]
    rows = chunks_for(docs, ["alpha " * 500, "beta " * 500])
    rows += [dict(c, level=2, text=(str(i)+" section ")*100, chunk_id=c["chunk_id"]+str(i)) for c in rows if c["level"] == 3 for i in range(7)]
    if fallback:
        rows = [c for c in rows if c["level"] not in (1, 2)]
    document_model = SimpleNamespace(upload_date="upload_date")

    class Query:
        def __init__(self, model):
            self.rows = ([SimpleNamespace(doc_id=d["id"], filename=d["filename"], upload_date=datetime.fromisoformat(d["upload_date"])) for d in docs]
                         if model is document_model else [SimpleNamespace(**c) for c in rows])

        def filter_by(self, **values):
            self.rows = [r for r in self.rows if all(getattr(r, k, v) == v for k, v in values.items())]
            return self

        def order_by(self, *args):
            return self

        def limit(self, n):
            self.rows = self.rows[:n]
            return self

        def all(self):
            return self.rows

        def first(self):
            return next(iter(self.rows), None)

    captured, replies = [], []

    def invoke(messages):
        captured.append([{"role": r, "content": m.content} for r, m in zip(("system", "user"), messages)])
        return SimpleNamespace(content=json.dumps(replies.pop(0)))

    names = {"questions": ("load_context", "generate_questions", .5, 1024),
             "insights": ("collect_evidence", "mine_insights", .4, 1200),
             "timeline": ("extract_dates_and_claims", "order_timeline", .2, 1500)}
    loader, generate, temperature, maximum = names[kind]
    ns = {"QuestionsState": dict, "InsightsState": dict, "TimelineState": dict, "List": list, "json": json, "re": re,
          "SessionLocal": lambda: SimpleNamespace(query=Query, close=lambda: None),
          "models": SimpleNamespace(Document=document_model, Chunk="chunks"), "_llm": SimpleNamespace(invoke=invoke),
          "SystemMessage": SimpleNamespace, "HumanMessage": SimpleNamespace,
          "UNTRUSTED_DOCUMENT_POLICY": UNTRUSTED_DOCUMENT_POLICY, "wrap_untrusted_document": wrap_untrusted_document,
          "logger": logging.getLogger("original-test")}
    ns, tree = original_functions(f"app/reasoning/{kind}_graph.py", {loader, generate, "format_timeline"}, ns)
    state = ns[loader]({"project_id": "fixture", "topic": "solar energy", "errors": []})
    inputs = exploration_inputs(rows, docs, kind)
    if kind == "questions":
        assert state["context"] == "\n\n".join(f"[{d['filename']}] {d['text']}" for d in inputs)
        replies.append(QUESTIONS)
        original = ns[generate](state)
        assert captured == [question_messages(inputs, "solar energy")]
        assert parse_exploration(json.dumps(QUESTIONS), kind, inputs) == original["questions"]
    elif kind == "insights":
        assert state["evidence"] == "\n\n---\n\n".join(f"[{d['filename']}]\n{d['text']}" for d in inputs)
        replies.append([dict(INSIGHT, supporting_docs=["A.pdf"])])
        ns[generate](state)
        assert captured == [insight_messages(inputs, "solar energy")]
    else:
        assert state["doc_texts"] == [{k: d[k] for k in ("filename", "upload_date", "text")} for d in inputs]
        replies.extend([timeline("A.pdf", "2021"), timeline("B.pdf", "2019-2020")])
        generated = ns[generate](state)
        assert captured == [timeline_messages(d) for d in inputs]
        assert format_timeline(generated) == ns["format_timeline"](generated)
    assign = next(n for n in tree.body if getattr(n, "targets", None) and any(getattr(t, "id", "") == "_llm" for t in n.targets))
    import ast
    assert {k.arg: ast.literal_eval(k.value) for k in assign.value.keywords if k.arg in {"temperature", "max_tokens"}} == {"temperature": temperature, "max_tokens": maximum}


@pytest.mark.parametrize("kind", ["questions", "insights", "timeline"])
def test_owned_cache_replay_topic_scope_and_shared_budget(exploration_env, kind):
    e = exploration_env
    respond(e, kind)
    result = run(e, kind, topic="solar")
    assert set(result["document_ids"]) == set(e.docs[A]) and result["items"] and result["provenance_only"]
    assert all(s["doc_id"] in e.docs[A] for s in result["input_sources"])
    assert result["metrics"]["provider_calls"] == (2 if kind == "timeline" else 1)
    idle(e)
    assert get(e, kind, topic="solar") == result
    assert run(e, kind, topic="solar") == result
    assert get(e, kind, topic="solar", token=B) is None
    assert len(e.requests) == (2 if kind == "timeline" else 1)
    if kind != "timeline":
        assert get(e, kind, topic="other") is None
    with pytest.raises(SessionError):
        e.store._analysis_result(B, e.refs[A], kind, "collection" if kind == "timeline" else "solar")
    with pytest.raises(SessionError):
        list(e.store.analyses.request(B, e.refs[A], kind, "collection" if kind == "timeline" else "", "foreign"))


@pytest.mark.parametrize("kind,bad", [("questions", '["Invented 【9†L1-L2】"]'),
    ("questions", '[{"bad":"question"}]'), ("insights", json.dumps([dict(INSIGHT, supporting_docs=["foreign.pdf"])])),
    ("insights", json.dumps([dict(INSIGHT, insight_type="<script>")])),
    ("timeline", json.dumps(timeline("foreign.pdf"))), ("timeline", json.dumps(timeline("a-0.pdf", "nonsense"))),
    ("timeline", '{"filename":"a-0.pdf","year":"2021","events":"bad"}')])
def test_malformed_or_foreign_attribution_is_never_completed(exploration_env, kind, bad):
    e = exploration_env
    e.responses.append(Frames(bad))
    with pytest.raises(SessionError):
        run(e, kind)
    assert get(e, kind) is None and len(e.requests) == 1


@pytest.mark.parametrize("kind", ["questions", "insights", "timeline"])
@pytest.mark.parametrize("response,code", [(429, "provider_rate"), (401, "provider_auth"),
    (httpx.ReadTimeout("dummy-private-provider"), "provider_timeout"), (Frames("partial", stop=False), "generation_incomplete")])
def test_provider_errors_do_not_consume_unlimited_retries_or_leak(exploration_env, kind, response, code, caplog):
    e = exploration_env
    e.responses.append(response)
    with pytest.raises(SessionError) as exc:
        run(e, kind)
    assert exc.value.code == code and "dummy-private" not in str(exc.value) + caplog.text
    assert get(e, kind) is None
    idle(e)
    with pytest.raises(SessionError):
        run(e, kind)
    assert len(e.requests) == 1 and e.store.generation.budget.calls == 1


def test_timeline_later_document_failure_is_atomic_and_budget_shared(exploration_env):
    e = exploration_env
    respond(e, "timeline")
    prior = run(e, "timeline")
    idle(e)
    e.responses.extend([Frames(json.dumps(timeline("a-0.pdf"))), 429])
    with pytest.raises(SessionError):
        run(e, "timeline", rid="failed")
    assert get(e, "timeline") == prior
    idle(e)
    e.store.generation.budget = ProviderBudget(replace(GenerationLimits(), user_requests=1), clock=e.store.clock)
    e.responses.append(Frames(json.dumps(QUESTIONS)))
    run(e, "questions", rid="questions")
    idle(e)
    with pytest.raises(SessionError) as exc:
        run(e, "insights", rid="over-budget")
    assert exc.value.code == "generation_budget" and e.store.analyses.budget is e.store.generation.budget


@pytest.mark.parametrize("kind", ["questions", "insights", "timeline"])
@pytest.mark.parametrize("action", ["cancel", "logout", "expiry", "close"])
def test_cancellation_logout_expiry_and_rerun_close_do_not_publish(exploration_env, kind, action):
    e = exploration_env
    entered, release = threading.Event(), threading.Event()
    e.responses.append(Frames(json.dumps(QUESTIONS if kind == "questions" else [INSIGHT] if kind == "insights" else timeline("a-0.pdf")), pause=(entered, release)))
    events = e.store.analyses.request(A, e.refs[A], kind, "collection" if kind == "timeline" else "", "cancelled")
    try:
        while next(events).get("stage") != "generating":
            pass
        assert entered.wait(2)
        if action == "logout":
            e.store.logout(A)
        elif action == "expiry":
            e.now[0] += 3601
        elif action == "cancel":
            e.store.analyses.cancel(A, e.refs[A], kind, "cancelled")
        if action != "close":
            release.set()
            with pytest.raises(SessionError):
                list(events)
    finally:
        events.close()
        release.set()
    idle(e)
    if action in {"cancel", "close"}:
        assert get(e, kind) is None


def test_ask_uses_canonical_current_question_and_all_documents(exploration_env):
    e = exploration_env
    respond(e, "questions")
    result = run(e, "questions")
    state = {"messages": [], "analysis_ask": {"workspace": e.refs[A], "chat": "chat", "topic": "", "id": result["items"][0]["id"]}}
    apply_question_action(e.store, A, e.refs[A], "chat", state)
    assert 'pending_turn' not in state and state['messages'] == []
    thread = e.store.question_thread(A, e.refs[A], '', result['items'][0]['id'], create=True)
    assert thread['question'] == QUESTIONS[0] and thread['documents'] == e.docs[A]
    assert thread['chat'] not in e.store.chats(A, e.refs[A])
    assert len(e.requests) == 1
    with pytest.raises(SessionError):
        e.store.question_for_ask(B, e.refs[B], "", result["items"][0]["id"])
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute("UPDATE active_index SET checksum=?", ("d" * 64,))
    with pytest.raises(SessionError):
        e.store.question_for_ask(A, e.refs[A], "", result["items"][0]["id"])


def test_corrupt_cache_and_upload_dates_fail_closed(exploration_env):
    e = exploration_env
    respond(e, "questions")
    run(e, "questions")
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute("UPDATE analysis_results SET payload='not-json'")
    with pytest.raises(SessionError) as exc:
        get(e, "questions")
    assert exc.value.code == "analysis_invalid"
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute("UPDATE documents SET upload_date='not-a-date'")
    with pytest.raises(SessionError) as exc:
        run(e, "timeline", rid="corrupt-date")
    assert exc.value.code == "analysis_evidence"


def test_ui_all_seven_tabs_and_explicit_generation(exploration_env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui
    e = exploration_env
    monkeypatch.setattr(ui, "_fixture_store", e.store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    assert not a.exception and len(a.tabs) == 7 and not e.requests
    for kind in ("questions", "insights", "timeline"):
        assert not a.button(key="run_" + kind).disabled
        respond(e, kind)
        a.button(key="run_" + kind).click().run(timeout=30)
        assert not a.exception
    count = len(e.requests)
    a.run(timeout=20)
    assert not a.exception and len(e.requests) == count
    assert not any("scheduled" in m.value for m in a.markdown)
    b = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{B}')").run(timeout=20)
    assert not b.exception and not any(INSIGHT["title"] in m.value or QUESTIONS[0] in m.value for m in b.markdown)


def test_original_html_and_timeline_sort_dedup():
    import ast
    _, tree = original_functions("frontend/app.py", set(), {})
    strings = [n.args[0] for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args
               and isinstance(n.func, ast.Attribute) and n.func.attr == "markdown" and isinstance(n.args[0], ast.JoinedStr)]
    ns = {"insight": INSIGHT, "icon": "🔄", "color": "#6C63FF", "supporting": "a-0.pdf", "_html": lambda s: escape(str(s), quote=True)}
    expression = next(n for n in strings if "insight['description']" in ast.unparse(n))
    assert insight_card(INSIGHT) == eval(compile(ast.Expression(expression), "original-insight-card", "eval"), ns)
    event = {"filename": "a.pdf", "claim": "A finding.", "importance": "high", "year": "2021"}
    ns.update(event=event, color="#FC8181", year="2021")
    expression = next(n for n in strings if "event['claim']" in ast.unparse(n))
    assert timeline_card(event) == eval(compile(ast.Expression(expression), "original-timeline-card", "eval"), ns)
    expression = next(n for n in strings if "font-size:1.1rem;" in ast.unparse(n) and "_html(year)" in ast.unparse(n))
    assert year_header("2021") == eval(compile(ast.Expression(expression), "original-year-header", "eval"), ns)
    assert "<script>" not in insight_card(dict(INSIGHT, title="<script>", description="<img>"))
    raw = [timeline("a.pdf", "2022", "SAME CLAIM"), timeline("b.pdf", "2019-2021", "same claim"), timeline("c.pdf", "Unknown", "Other claim")]
    ns, _ = original_functions("app/reasoning/timeline_graph.py", {"format_timeline"}, {"TimelineState": dict, "List": list, "re": re})
    assert format_timeline({"raw_events": raw}) == ns["format_timeline"]({"raw_events": raw})


def test_native_all_seven_analyses_reindex_delete_expiry_and_corruption(tmp_path):
    cache = os.environ.get("DOCUMIND_TEST_MODEL_CACHE")
    if not cache:
        pytest.skip("Public native model cache required; no live provider test")
    import fitz
    calls, users, now = [], {A: "user-a", B: "user-b"}, [time.time()]

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        system = body["messages"][0]["content"]
        if body["temperature"] == .5:
            result = QUESTIONS
        elif body["temperature"] == .4:
            result = [dict(INSIGHT, supporting_docs=["energy-0.pdf", "energy-1.pdf"])]
        elif body["temperature"] == .2:
            filename = re.search(r"^Filename: (.+)$", body["messages"][-1]["content"], re.M)[1]
            result = timeline(filename, "2020" if "0.pdf" in filename else "2022", TEXT.split(".")[0] + ".")
        elif system.startswith("You are an expert fact-checker"):
            result = []  # Same solar findings at different dates are not contradictions.
        elif system.startswith("You are a research librarian"):
            result = [{"topic": "Grid reliability", "description": "The overview describes solar generation and storage but not long-term reliability metrics. Additional evidence could help evaluate hospital supply."}]
        elif body["temperature"] == 0:
            result = GUIDE
        else:
            result = BRIEF
        return httpx.Response(200, stream=Frames(json.dumps(result) if not isinstance(result, str) else result))

    def authorize(token):
        if token not in users:
            raise SessionError("expired")
        return SimpleNamespace(user_id=users[token], deadline=now[0]+10000)

    store = WorkspaceStore(SimpleNamespace(authorize=authorize, logout=lambda t: users.pop(t, None)),
        base_dir=tmp_path, model_cache=cache, clock=lambda: now[0],
        provider=GroqStream(GroqConfig("gsk_dummy_native_all_feature_test"), transport=httpx.MockTransport(handler)))
    ref, other = store.open(A), store.open(B)
    documents, indexing, results = [], [], {}
    try:
        for i in range(2):
            text = f"Study {2020+i*2}. " + TEXT
            with fitz.open() as pdf:
                page = pdf.new_page()
                assert page.insert_textbox(fitz.Rect(40, 40, 550, 800), text, fontsize=9) >= 0
                data = pdf.tobytes()
            doc = store.upload(A, ref, f"energy-{i}.pdf", BytesIO(data))["id"]
            store.prepare(A, ref, doc)
            indexing.append(store.index_document(A, ref, doc))
            documents.append(doc)
        for kind in ("guide", "brief", "contradictions", "gaps", "questions", "insights", "timeline"):
            target = documents[0] if kind == "guide" else "" if kind in {"questions", "insights"} else "collection"
            events = list(store.analyses.request(A, ref, kind, target, kind))
            results[kind] = next(e["analysis_result"] for e in events if e["type"] == "metadata")
            assert all(s["doc_id"] in documents for s in results[kind]["input_sources"])
        assert len(calls) == 8 and results["timeline"]["items"][0]["year"] == "2020"
        assert store.documents(B, other) == [] and store.get_questions(B, other) is None
        with pytest.raises(SessionError):
            store.get_timeline(B, ref)
        assert store.question_for_ask(A, ref, "", results["questions"]["items"][0]["id"])[1] == documents
        print(json.dumps({"scope": "native two PDFs, seven original analyses, MOCKED Groq, warm public caches",
            "indexing": indexing, "analysis_metrics": {k: v["metrics"] for k, v in results.items()},
            "utc_upload_date": datetime.fromtimestamp(now[0], timezone.utc).strftime("%Y-%m-%d")}))
        store.index_document(A, ref, documents[0])
        assert all(getattr(store, "get_"+kind)(A, ref) is None for kind in ("questions", "insights", "timeline"))
        list(store.analyses.request(A, ref, "questions", "", "questions-new"))
        store.delete_document(A, ref, documents[1])
        assert store.get_questions(A, ref) is None
        with store._db(store.check(A, ref)) as db:
            snapshot, _ = store._snapshot(db, ref)
        (snapshot / "chunks.json").write_text("[]")  # Newly generated test fixture only.
        with pytest.raises(SessionError):
            list(store.analyses.request(A, ref, "insights", "", "corrupt"))
        assert len(calls) == 9
        now[0] += 3601
        with pytest.raises(SessionError):
            store.get_timeline(A, ref)
    finally:
        store._index_service.close()
