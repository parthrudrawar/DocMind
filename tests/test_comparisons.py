"""Original source comparisons + isolated lifecycle tests. Groq is MOCKED."""
import ast
from dataclasses import replace
from html import escape
from io import BytesIO
import json
import logging
import os
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import test_guides

from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document
from session_cloud.comparison_service import parse_items
from session_cloud.comparison_view import claim_card, gap_card, topic_label
from session_cloud.generation_budget import GenerationLimits, ProviderBudget
from session_cloud.groq_stream import GroqConfig, GroqStream
from session_cloud.original_comparisons import comparison_inputs, contradiction_messages, gap_messages
from session_cloud.runtime import SessionError, WorkspaceStore
from test_guides import A, B, Frames, REFERENCE, idle

guide_env = test_guides.env

CASES = {
    "contradictory": ["The reactor operates at 20 degrees Celsius.", "The reactor operates at 80 degrees Celsius under the same conditions."],
    "consistent": ["The reactor operates at 20 degrees Celsius.", "Under the same conditions the operating temperature is 20 degrees Celsius."],
    "incomplete": ["The trial measures treatment efficacy.", "The trial evaluates short-term outcomes; long-term outcomes are not reported."],
    "unrelated": ["The reactor operates at 20 degrees Celsius.", "The medieval manuscript describes poetry and illuminated art."],
}
CONTRA = {"topic": "Operating temperature", "text_a": CASES["contradictory"][0],
          "text_b": CASES["contradictory"][1], "explanation": "The same operating condition has incompatible temperatures.", "confidence": 0.8}
GAP = {"topic": "Long-term outcomes", "description": "The overview does not report long-term treatment outcomes. Further evidence would help assess durability."}


def chunks_for(docs, texts):
    return [{"doc_id": doc["id"], "filename": doc["filename"], "chunk_id": doc["id"] + str(level),
             "level": level, "text": text, "page_number": 1, "page_end": 1,
             "section": "full_document" if level == 1 else "Introduction", "chunk_type": "summary" if level == 1 else "text"}
            for doc, text in zip(docs, texts) for level in (1, 3)]


@pytest.fixture
def comparison_env(guide_env, monkeypatch):
    env = guide_env
    env.texts = list(CASES["contradictory"])
    env.worker_calls = []

    def run_worker(request, folder, limits, active):
        active()
        assert request["action"] == "analysis_inputs"
        assert folder == env.store._folder(env.refs[A]) or folder == env.store._folder(env.refs[B])
        docs = request["documents"]
        env.worker_calls.append(request)
        return {"documents": comparison_inputs(chunks_for(docs, (env.texts * 3)[:len(docs)]), docs, request["kind"]),
                "metrics": {"fixture": True}}

    monkeypatch.setattr(env.store._index_service, "run", run_worker)
    return env


def run(e, kind="contradictions", *, rid="scan", token=A):
    events = list(e.store.analyses.request(token, e.refs[token], kind, "collection", rid))
    assert events[-1]["type"] == "done"
    return next(v["analysis_result"] for v in events if v["type"] == "metadata")


def original_functions(path, names, namespace):
    if not REFERENCE.exists():
        pytest.skip("Original read-only source unavailable")
    tree = ast.parse((REFERENCE / path).read_text(encoding="utf-8-sig"))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), "original-read-only-analysis", "exec"), namespace)
    return namespace, tree


@pytest.mark.parametrize("case", CASES)
def test_actual_original_prompts_pair_rules_and_output_match(case):
    from itertools import combinations
    docs = [{"id": A, "filename": "A.pdf"}, {"id": B, "filename": "B.pdf"}]
    inputs = comparison_inputs(chunks_for(docs, CASES[case]), docs, "contradictions")
    captured = []
    expected = [CONTRA] if case == "contradictory" else []

    def invoke(messages):
        captured.append([{"role": role, "content": m.content} for role, m in zip(("system", "user"), messages)])
        return SimpleNamespace(content=json.dumps(expected))

    namespace = {"ContradictionState": dict, "GapState": dict, "combinations": combinations,
        "json": json, "List": list, "SystemMessage": SimpleNamespace, "HumanMessage": SimpleNamespace,
        "_llm": SimpleNamespace(invoke=invoke), "UNTRUSTED_DOCUMENT_POLICY": UNTRUSTED_DOCUMENT_POLICY,
        "wrap_untrusted_document": wrap_untrusted_document, "logger": logging.getLogger("original-test")}
    ns, tree = original_functions("app/reasoning/contradiction_graph.py", {"compare_pairs"}, namespace)
    original = ns["compare_pairs"]({"doc_chunks": {d["id"]: {"filename": d["filename"],
        "chunks": [s["exact_quote"] for s in d["sources"]]} for d in inputs}, "errors": []})
    assert captured == [contradiction_messages(*inputs)]
    assert not original["errors"]
    candidate = parse_items(json.dumps(expected), "contradictions", tuple(inputs))
    for old, new in zip(original["contradictions"], candidate):
        assert all(new[k] == v for k, v in old.items())
    assert bool(candidate) == (case == "contradictory")
    assign = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_llm" for t in n.targets))
    assert {k.arg: ast.literal_eval(k.value) for k in assign.value.keywords if k.arg in {"temperature", "max_tokens"}} == {"temperature": 0, "max_tokens": 1024}
    captured.clear()
    inputs = comparison_inputs(chunks_for(docs, CASES[case]), docs, "gaps")
    ns, _ = original_functions("app/reasoning/gap_graph.py", {"identify_gaps"}, namespace)
    ns["identify_gaps"]({"doc_summaries": [{"filename": d["filename"], "summary": d["text"]} for d in inputs], "errors": []})
    assert captured == [gap_messages(inputs)]


@pytest.mark.parametrize("summary", [True, False])
def test_original_sql_input_sampling_limits_and_fallback_match(summary):
    docs = [{"id": A, "filename": "A.pdf"}, {"id": B, "filename": "B.pdf"}]
    chunks = chunks_for(docs, ["alpha " * 450, "beta " * 440])
    chunks += [dict(chunks[1], chunk_id=str(i), text=(str(i) + " sample ") * 250) for i in range(8)]
    if not summary:
        chunks = [c for c in chunks if c["level"] != 1]
    models = SimpleNamespace(Document="document", Chunk="chunk")

    class Query:
        def __init__(self, model):
            self.rows = [SimpleNamespace(doc_id=d["id"], filename=d["filename"]) for d in docs] if model == "document" else [SimpleNamespace(**c) for c in chunks]

        def filter_by(self, **values):
            self.rows = [r for r in self.rows if all(getattr(r, k, v) == v for k, v in values.items())]
            return self

        def limit(self, n):
            self.rows = self.rows[:n]
            return self

        def all(self):
            return self.rows

        def first(self):
            return next(iter(self.rows), None)

    ns = {"ContradictionState": dict, "GapState": dict, "List": list, "MAX_CHUNKS_PER_DOC": 6,
          "models": models, "SessionLocal": lambda: SimpleNamespace(query=Query, close=lambda: None)}
    original_functions("app/reasoning/contradiction_graph.py", {"load_chunks"}, ns)
    old = ns["load_chunks"]({"project_id": "fixture"})["doc_chunks"]
    candidate = comparison_inputs(chunks, docs, "contradictions")
    for d in candidate:
        assert d["text"] == "\n---\n".join(c[:300] for c in old[d["id"]]["chunks"][:5])
    original_functions("app/reasoning/gap_graph.py", {"summarise_docs"}, ns)
    old = ns["summarise_docs"]({"project_id": "fixture"})["doc_summaries"]
    candidate = comparison_inputs(chunks, docs, "gaps")
    assert [{"filename": d["filename"], "summary": d["text"]} for d in candidate] == old


@pytest.mark.parametrize("kind", ["contradictions", "gaps"])
def test_complete_result_scope_cache_replay_and_provider_settings(comparison_env, kind):
    e = comparison_env
    e.responses.append(Frames(json.dumps([CONTRA] if kind == "contradictions" else [GAP])))
    result = run(e, kind)
    assert result["document_ids"] == e.docs[A] and result["items"]
    assert result["provenance_only"] and all(s["doc_id"] in e.docs[A] for s in result["input_sources"])
    assert result["metrics"]["provider_calls"] == 1
    assert e.requests[0]["temperature"] == (0 if kind == "contradictions" else .3)
    assert e.requests[0]["max_completion_tokens"] == 1024
    getter = e.store.get_contradictions if kind == "contradictions" else e.store.get_gaps
    idle(e)
    assert run(e, kind) == result
    for _ in range(3):
        assert getter(A, e.refs[A]) == result
    assert len(e.requests) == 1 and e.store.analyses.budget is e.store.generation.budget
    assert getter(B, e.refs[B]) is None
    with pytest.raises(SessionError):
        getter(B, e.refs[A])
    with pytest.raises(SessionError):
        list(e.store.analyses.request(B, e.refs[A], kind, "collection", "foreign"))


def test_confidence_filter_sort_and_untrusted_ids_are_not_authority(comparison_env):
    e = comparison_env
    e.responses.append(Frames(json.dumps([dict(CONTRA, confidence=n, doc_id_1=e.docs[B][0]) for n in (.4, .9, .39)])))
    result = run(e)
    assert [i["confidence"] for i in result["items"]] == [.9, .4]
    assert all(i["doc_id_1"] == e.docs[A][0] and i["text_a_is_exact_excerpt"] for i in result["items"])
    assert not parse_items(json.dumps([dict(CONTRA, confidence=0)]), "contradictions", (None, None))


@pytest.mark.parametrize("bad", ["not json", "{}", '[{"topic":"missing fields"}]',
    json.dumps([dict(CONTRA, confidence=True)]), json.dumps([dict(CONTRA, confidence=float("nan"))]),
    json.dumps([dict(CONTRA, confidence=1.1)]), json.dumps([dict(CONTRA, explanation="invented 【999†L1-L2】")])])
def test_malformed_or_fabricated_citations_fail_closed(comparison_env, bad):
    e = comparison_env
    e.responses.append(Frames(bad))
    with pytest.raises(SessionError):
        run(e)
    assert e.store.get_contradictions(A, e.refs[A]) is None


@pytest.mark.parametrize("case", CASES)
def test_multidocument_fixtures_propagate_not_invent_outcomes(comparison_env, case):
    e = comparison_env
    e.texts = CASES[case]
    e.responses.append(Frames(json.dumps([CONTRA] if case == "contradictory" else [])))
    result = run(e)
    assert bool(result["items"]) == (case == "contradictory")
    assert all(t in e.requests[0]["messages"][1]["content"] for t in CASES[case])
    idle(e)
    e.responses.append(Frames(json.dumps([GAP] if case == "incomplete" else [])))
    result = run(e, "gaps", rid="gaps")
    assert bool(result["items"]) == (case == "incomplete")
    assert result["assessment"] == "potential_knowledge_gaps"  # not proof of full-document absence


def test_failed_rescan_keeps_old_cache_and_counts_budget(comparison_env):
    e = comparison_env
    e.responses.append(Frames(json.dumps([CONTRA])))
    prior = run(e)
    idle(e)
    e.responses.append(Frames('[{"topic":"incomplete"}', stop=False))
    with pytest.raises(SessionError):
        run(e, rid="failed")
    assert e.store.get_contradictions(A, e.refs[A]) == prior
    idle(e)
    with pytest.raises(SessionError):
        run(e, rid="failed")
    assert len(e.requests) == 2 and e.store.generation.budget.calls == 2


@pytest.mark.parametrize("response,code", [(429, "provider_rate"), (401, "provider_auth"),
    (httpx.ReadTimeout("dummy-private-diagnostic"), "provider_timeout")])
def test_provider_failures_are_sanitized_and_not_empty_success(comparison_env, response, code, caplog):
    e = comparison_env
    e.responses.append(response)
    with pytest.raises(SessionError) as exc:
        run(e, "gaps")
    assert exc.value.code == code and "dummy-private" not in str(exc.value) + caplog.text
    assert e.store.get_gaps(A, e.refs[A]) is None and len(e.requests) == 1


def test_concurrent_sessions_share_only_gate_not_private_results(comparison_env):
    e = comparison_env
    entered, release = threading.Event(), threading.Event()
    e.responses.append(Frames(json.dumps([CONTRA]), pause=(entered, release)))
    events = e.store.analyses.request(A, e.refs[A], "contradictions", "collection", "parallel")
    try:
        while next(events).get("stage") != "generating":
            pass
        assert entered.wait(2)
        assert e.store.get_gaps(B, e.refs[B]) is None  # reads remain independently authorized
        with pytest.raises(SessionError) as exc:
            run(e, "gaps", token=B)
        assert exc.value.code == "busy" and len(e.requests) == 1
        with pytest.raises(SessionError) as exc:
            e.store.delete_document(A, e.refs[A], e.docs[A][0])
        assert exc.value.code == "busy"
        release.set()
        assert any(v.get("completed") for v in events)
    finally:
        events.close()
        release.set()
    assert e.store.get_gaps(B, e.refs[B]) is None


def test_snapshot_race_and_resource_failure_preserve_previous_cache(comparison_env, monkeypatch):
    from session_cloud.index_contract import IndexFailure
    e = comparison_env
    e.responses.append(Frames(json.dumps([GAP])))
    prior = run(e, "gaps")
    idle(e)
    original = e.store._index_service.run

    def resource_failure(*args):
        raise IndexFailure("index_resource")

    monkeypatch.setattr(e.store._index_service, "run", resource_failure)
    with pytest.raises(SessionError) as exc:
        run(e, "gaps", rid="oom")
    assert exc.value.code == "index_resource" and e.store.get_gaps(A, e.refs[A]) == prior
    idle(e)

    def race(*args):
        result = original(*args)
        with e.store._db(e.store.check(A, e.refs[A])) as db:
            db.execute("UPDATE active_index SET checksum=?", ("f" * 64,))
        return result

    monkeypatch.setattr(e.store._index_service, "run", race)
    e.responses.append(Frames(json.dumps([GAP])))
    with pytest.raises(SessionError) as exc:
        run(e, "gaps", rid="race")
    assert exc.value.code == "analysis_stale" and e.store.get_gaps(A, e.refs[A]) is None


def test_paraphrases_are_not_presented_as_verified_quotes(comparison_env):
    e = comparison_env
    e.responses.append(Frames(json.dumps([dict(CONTRA, text_a="The reactor runs cool.")])))
    item = run(e)["items"][0]
    assert not item["text_a_is_exact_excerpt"] and item["text_b_is_exact_excerpt"]
    assert all(s["exact_quote"] != "The reactor runs cool." for s in e.store.get_contradictions(A, e.refs[A])["input_sources"])


def test_three_document_pair_order_and_count_are_original(comparison_env):
    e = comparison_env
    d = e.store.upload(A, e.refs[A], "third.pdf", BytesIO(b"%PDF-1.7\nthird"))["id"]
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute("UPDATE documents SET status='indexed' WHERE id=?", (d,))
    e.responses.extend(Frames(json.dumps([CONTRA])) for _ in range(3))
    result = run(e)
    a, b = e.docs[A]
    assert [(i["doc_id_1"], i["doc_id_2"]) for i in result["items"]] == [(a, b), (a, d), (b, d)]
    assert result["metrics"]["provider_calls"] == 3 and e.store.generation.budget.calls == 3


def test_original_persistence_threshold_and_gap_settings():
    captured = []
    db = SimpleNamespace(query=lambda _: SimpleNamespace(filter_by=lambda **kw: SimpleNamespace(delete=lambda: None)),
                         add=captured.append, commit=lambda: None, rollback=lambda: None, close=lambda: None)
    ns = {"ContradictionState": dict, "SessionLocal": lambda: db,
          "models": SimpleNamespace(Contradiction=lambda **kw: kw), "logger": logging.getLogger("original-test")}
    original_functions("app/reasoning/contradiction_graph.py", {"save_contradictions"}, ns)
    original = [dict(CONTRA, confidence=c, doc_id_1=A, doc_id_2=B) for c in (.39, .4, .9)]
    ns["save_contradictions"]({"project_id": "fixture", "errors": [], "contradictions": original})
    assert [i["confidence"] for i in captured] == [.4, .9]
    _, tree = original_functions("app/reasoning/gap_graph.py", set(), {})
    assign = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_llm" for t in n.targets))
    assert {k.arg: ast.literal_eval(k.value) for k in assign.value.keywords if k.arg in {"temperature", "max_tokens"}} == {"temperature": .3, "max_tokens": 1024}


def test_topic_labels_cannot_embed_remote_images_or_html():
    label = topic_label('![private](https://invalid.example/track)<img src=x>')
    assert r"\!\[private\]\(https://invalid\.example/track\)" in label and "<img" not in label


def test_pair_failure_is_atomic_and_uses_shared_quota(comparison_env):
    e = comparison_env
    d = e.store.upload(A, e.refs[A], "third.pdf", BytesIO(b"%PDF-1.7\nthird"))["id"]
    with e.store._db(e.store.check(A, e.refs[A])) as db:
        db.execute("UPDATE documents SET status='indexed' WHERE id=?", (d,))
    e.store.generation.budget = ProviderBudget(replace(GenerationLimits(), user_requests=1), clock=e.store.clock)
    e.responses.append(Frames(json.dumps([CONTRA])))
    with pytest.raises(SessionError) as exc:
        run(e)
    assert exc.value.code == "daily_ai_limit" and len(e.requests) == 1
    assert e.store.get_contradictions(A, e.refs[A]) is None


@pytest.mark.parametrize("mode", ["logout", "expiry", "cancel", "consumer_close"])
def test_interruption_expiry_logout_never_publishes_partial_result(comparison_env, mode):
    e = comparison_env
    entered, release = threading.Event(), threading.Event()
    e.responses.append(Frames(json.dumps([CONTRA]), pause=(entered, release)))
    events = e.store.analyses.request(A, e.refs[A], "contradictions", "collection", "interrupted")
    try:
        while next(events).get("stage") != "generating":
            pass
        assert entered.wait(2)
        if mode == "logout":
            e.store.logout(A)
        elif mode == "expiry":
            e.now[0] += 3601
        elif mode == "cancel":
            e.store.analyses.cancel(A, e.refs[A], "contradictions", "interrupted")
        if mode != "consumer_close":
            release.set()
            with pytest.raises(SessionError):
                list(events)
    finally:
        events.close()
        release.set()
    idle(e)
    if mode in {"cancel", "consumer_close"}:
        assert e.store.get_contradictions(A, e.refs[A]) is None


def test_foreign_source_missing_evidence_and_corrupt_index_do_not_mean_no_findings(comparison_env, monkeypatch):
    from session_cloud.index_contract import IndexFailure
    e = comparison_env
    original = e.store._index_service.run
    for mode in ("foreign", "missing", "corrupt"):
        def worker(*args):
            if mode == "corrupt":
                raise IndexFailure()
            result = original(*args)
            if mode == "foreign":
                result["documents"][0]["sources"][0]["doc_id"] = e.docs[B][0]
            else:
                result["documents"] = []
            return result
        monkeypatch.setattr(e.store._index_service, "run", worker)
        with pytest.raises(SessionError):
            run(e, rid=mode)
        idle(e)
    assert not e.requests and e.store.get_contradictions(A, e.refs[A]) is None


def test_original_html_styles_and_escaping_match():
    ns, tree = original_functions("frontend/app.py", set(), {})
    strings = [n.args[0] for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args
               and isinstance(n.func, ast.Attribute) and n.func.attr == "markdown" and isinstance(n.args[0], ast.JoinedStr)]
    ns.update(gap=GAP, i=1, contra={**CONTRA, "doc_a": "a.pdf", "doc_b": "b.pdf"}, _html=lambda s: escape(str(s), quote=True))
    expression = next(n for n in strings if 'gap[\'description\']' in ast.unparse(n))
    assert gap_card(GAP, 1) == eval(compile(ast.Expression(expression), "original-gap-card", "eval"), ns)
    expression = next(n for n in strings if 'contra[\'text_a\']' in ast.unparse(n))
    assert claim_card("a.pdf", CONTRA["text_a"]) == eval(compile(ast.Expression(expression), "original-claim-card", "eval"), ns)
    assert "<script>" not in claim_card("<script>", '<img src=x onerror="bad()">')
    assert "<img" not in gap_card({"topic": "<img>", "description": "<script>"}, 1)


def test_ui_explicit_triggers_reruns_and_two_sessions(comparison_env, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from session_cloud import streamlit_app as ui
    e = comparison_env
    monkeypatch.setattr(ui, "_fixture_store", e.store, raising=False)
    a = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{A}')").run(timeout=20)
    assert not a.exception and not e.requests
    e.responses.append(Frames(json.dumps([CONTRA])))
    a.button(key="run_contra").click().run(timeout=30)
    assert not a.exception and len(e.requests) == 1
    assert any("uncalibrated" in x.label for x in a.expander)
    a.run(timeout=20)
    assert not a.exception and len(e.requests) == 1
    e.responses.append(Frames(json.dumps([GAP])))
    a.button(key="run_gaps").click().run(timeout=30)
    assert not a.exception and len(e.requests) == 2
    b = AppTest.from_string(f"from session_cloud import streamlit_app as ui\nui.render_workspace(ui._fixture_store, '{B}')").run(timeout=20)
    assert not b.exception and b.button(key="run_contra").disabled
    assert not any(CONTRA["explanation"] in x.value or GAP["description"] in x.value for x in b.markdown)


@pytest.mark.parametrize("case", CASES)
def test_native_pdf_samples_cache_invalidation_and_corruption(tmp_path, case):
    cache = os.environ.get("DOCUMIND_TEST_MODEL_CACHE")
    if not cache:
        pytest.skip("Native public model cache required; no real Groq")
    import fitz
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        response = ([CONTRA] if case == "contradictory" else []) if body["temperature"] == 0 else ([GAP] if case == "incomplete" else [])
        return httpx.Response(200, stream=Frames(json.dumps(response)))

    store = WorkspaceStore(SimpleNamespace(authorize=lambda _: SimpleNamespace(user_id="test", deadline=time.time() + 10000)),
        base_dir=tmp_path, model_cache=cache,
        daily_quota=SimpleNamespace(reserve=lambda capability, owner: None),
        provider=GroqStream(GroqConfig("gsk_dummy_native_comparison_test"), transport=httpx.MockTransport(handler)))
    ref = store.open(A)
    documents = []
    try:
        for i, text in enumerate(CASES[case]):
            with fitz.open() as pdf:
                page = pdf.new_page()
                assert page.insert_textbox(fitz.Rect(40, 40, 550, 800), text, fontsize=9) >= 0
                data = pdf.tobytes()
            doc = store.upload(A, ref, f"{i}.pdf", BytesIO(data))["id"]
            store.prepare(A, ref, doc)
            store.index_document(A, ref, doc)
            documents.append(doc)
        results = []
        for kind in ("contradictions", "gaps"):
            events = list(store.analyses.request(A, ref, kind, "collection", kind))
            result = next(e["analysis_result"] for e in events if e["type"] == "metadata")
            assert result["document_ids"] == documents
            assert all(s["exact_quote"] in calls[-1]["messages"][-1]["content"] for s in result["input_sources"])
            results.append(result)
        print(json.dumps({"scope": "native two-PDF fixture; MOCKED Groq", "case": case,
                          "metrics": [r["metrics"] for r in results]}))
        store.index_document(A, ref, documents[0])
        assert store.get_contradictions(A, ref) is None and store.get_gaps(A, ref) is None
        with pytest.raises(SessionError) as exc:
            list(store.analyses.request(A, ref, "contradictions", "collection", "contradictions"))
        assert exc.value.code == "analysis_stale"
        list(store.analyses.request(A, ref, "gaps", "collection", "new"))
        store.delete_document(A, ref, documents[1])
        assert store.get_gaps(A, ref) is None
        with store._db(store.check(A, ref)) as db:
            snapshot, checksum = store._snapshot(db, ref)
        (snapshot / "chunks.json").write_text("[]")  # isolated pytest fixture only
        with pytest.raises(SessionError):
            list(store.analyses.request(A, ref, "gaps", "collection", "corrupt"))
        assert len(calls) == 3
    finally:
        store._index_service.close()
