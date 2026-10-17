"""OFFLINE synthetic corpus through native RAG + dummy Groq SSE; no real AI claims."""
import argparse
import ast
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import re
import statistics
import time
from types import ModuleType, SimpleNamespace

import httpx

from app.reasoning.citations import citation_numbers, first_citation_position
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document
from session_cloud.adapter import LocalAPI
from session_cloud.bounded_pdf import peak_rss_bytes
from session_cloud.evaluation_corpus import DOCUMENTS, QUESTIONS
from session_cloud.evaluate_retrieval import pdf_bytes
from session_cloud.generation_budget import GenerationLimits
from session_cloud.groq_stream import GroqConfig, GroqStream
from session_cloud.original_context import RetrievedChunk
from session_cloud.original_evidence import assess_evidence_strength
from session_cloud.original_prompts import _messages_for_state
from session_cloud.runtime import WorkspaceStore

FOLLOW = '["What other evidence is available?", "Which source supports that?", "What remains unknown?"]'


class Frames(httpx.SyncByteStream):
    def __init__(self, text):
        self.text = text

    def __iter__(self):
        for i in range(0, len(self.text), 24):
            item = {"choices": [{"index": 0, "delta": {"content": self.text[i:i + 24]}, "finish_reason": None}]}
            yield b"data: " + json.dumps(item).encode() + b"\n\n"
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield b"data: [DONE]\n\n"


def original_module(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    tree.body = [n for n in tree.body if not (
        isinstance(n, ast.ImportFrom) and (n.module or "").startswith(("app.", "langchain"))
        or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_llm" for t in n.targets))]
    namespace = ModuleType("original_generation_fixture")
    namespace.__dict__.update(QAState=dict, UNTRUSTED_DOCUMENT_POLICY=UNTRUSTED_DOCUMENT_POLICY,
        wrap_untrusted_document=wrap_untrusted_document, citation_numbers=citation_numbers,
        first_citation_position=first_citation_position, assess_evidence_strength=assess_evidence_strength,
        _llm=SimpleNamespace(invoke=lambda _: SimpleNamespace(content=FOLLOW)))
    exec(compile(tree, str(path), "exec"), namespace.__dict__)
    return namespace


def evaluate(reference, cache, output):
    output.mkdir(parents=True, exist_ok=False)
    token = "d" * 64
    observed, current, contexts = [], {}, []
    facts = {code: (fact, filename, page) for filename, records in DOCUMENTS
             for page, (code, fact, _) in enumerate(records, 1)}

    def request(request):
        assert request.url.host == "api.groq.com"
        payload = json.loads(request.content)
        user_prompt = payload["messages"][-1]["content"]
        if "Suggest exactly 3" in user_prompt:
            return httpx.Response(200, stream=Frames(FOLLOW))
        # Test oracle ONLY: known factual strings, provided only if the actual
        # native retrieved evidence contains their code. This is NOT a model.
        sections = re.split(r"(?m)^\[(\d+)\] [^\n]+\n", user_prompt)
        numbered = [(int(sections[i]), sections[i + 1]) for i in range(1, len(sections) - 1, 2)]
        answer = []
        for code in current["relevant"]:
            number = next((n for n, text in numbered if code in text), None)
            if number is None:
                raise AssertionError("Known answer not retrieved")
            answer.append(f"{facts[code][0]} [{number}]")
        current["messages"] = payload["messages"]
        current["answer"] = " ".join(answer)
        return httpx.Response(200, stream=Frames(current["answer"]))

    # Larger TEST-ONLY reservations exercise all twelve offline requests. There
    # is no live transport/key and this does not change candidate default limits.
    test_limits = replace(GenerationLimits(), user_tokens=5_000_000, global_tokens=5_000_000,
                          user_requests=60)
    store = WorkspaceStore(SimpleNamespace(authorize=lambda _: SimpleNamespace(
        user_id="33333333-3333-4333-8333-333333333333", deadline=time.time() + 3600)),
        base_dir=output, model_cache=cache, generation_limits=test_limits,
        provider=GroqStream(GroqConfig("gsk_dummy_offline_evaluation_only"), transport=httpx.MockTransport(request)))
    ref, documents = store.open(token), []
    base_context = store.prepare_answer_context

    def context(*args, **kwargs):
        value = base_context(*args, **kwargs)
        contexts.append(value)
        return value

    store.prepare_answer_context = context
    original_answer = original_module(reference / "app/reasoning/nodes/reasoning_node.py")
    original_formatter = original_module(reference / "app/reasoning/nodes/formatter_node.py")
    # Capture exact original last-six-message and formatting bodies, not a
    # reimplementation of the reference prompts/citation selection.
    timings, prompt_matches, citation_matches, grounded = [], 0, 0, 0
    try:
        for filename, records in DOCUMENTS:
            doc = store.upload(token, ref, filename, BytesIO(pdf_bytes(records)))["id"]
            store.prepare(token, ref, doc)
            store.index_document(token, ref, doc)
            documents.append(doc)
        chat = store.new_chat(token, ref)
        for i, (question, relevant, kind) in enumerate(QUESTIONS):
            current.clear()
            current.update(relevant=relevant)
            api = LocalAPI(store, token, ref, chat, documents)
            pieces, started = [], time.monotonic()
            answer, meta = api.parse_stream(api.query_stream(question, ref, chat, turn_id=f"corpus-{i}"),
                                            on_token=pieces.append)
            if not meta.get("completed"):
                raise AssertionError("Offline completion failed: " + meta.get("stream_error", "unknown"))
            evidence = contexts[-1]
            state = {"user_query": question, "retrieved_chunks": [RetrievedChunk(**c) for c in evidence["chunks"]],
                     "aggregated_evidence": evidence["aggregated_evidence"],
                     "conversation_history": current["messages"][1:-1], "final_answer": answer}
            prompt_matches += int(_messages_for_state(state) == original_answer._messages_for_state(state)
                                  == current["messages"])
            baseline = original_formatter.formatter_node(state)
            citation_matches += int(meta["citations"] == baseline["citations"])
            exact = all(facts[code][0] in answer for code in relevant)
            source = all(any(c["filename"] == facts[code][1]
                             and c["page_number"] <= facts[code][2] <= c["page_end"]
                             for c in meta["citations"]) for code in relevant)
            grounded += int(exact and source)
            observed.append({"question": i + 1, "kind": kind, "completed": True,
                "oracle_facts_and_source_pages": exact and source, "incremental_updates": len(pieces),
                "citation_count": len(meta["citations"]), "metrics": meta["metrics"]})
            timings.append(time.monotonic() - started)
    finally:
        store._index_service.close()
    defaults, used, within = GenerationLimits(), 0, 0
    for row in observed:
        used += row["metrics"]["reserved_tokens_upper_bound"]
        if used <= min(defaults.user_tokens, defaults.global_tokens):
            within += 1
    report = {"scope": "MOCKED Groq SSE with real native ingestion/retrieval; NOT LLM answer correctness",
        "documents": 3, "pages": 18, "questions": len(QUESTIONS),
        "completed": len(observed), "literal_oracle_facts_and_source_pages": grounded,
        "identical_original_prompts": prompt_matches, "identical_original_citations": citation_matches,
        "provider": "httpx.MockTransport, dummy key, no network/provider usage",
        "token_measurement": "Conservative UTF-8/framing/output reservations, NOT actual Groq token counts",
        "reserved_tokens_upper_bound": used, "default_account_budget_would_fit_prefix": within,
        "worker_peak_rss_bytes": max(r["metrics"]["evidence"]["peak_rss_bytes"] for r in observed),
        "host_peak_rss_bytes": peak_rss_bytes(),
        "first_token_seconds": {"first": observed[0]["metrics"]["first_token_seconds"],
            "median": statistics.median(r["metrics"]["first_token_seconds"] for r in observed)},
        "completion_seconds": {"first": timings[0], "median": statistics.median(timings)},
        "results": observed,
        "limitations": ["Fixtures supply expected strings, not model intelligence/semantic entailment",
            "Offline evaluation-only raised token reservations; defaults not changed",
            "First reranker load cached; embeddings warm from indexing; no cold network download",
            "Windows Python 3.12; Linux/Streamlit Cloud runtime and real Google OAuth not tested"]}
    (output / "generation-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reference", type=Path, required=True)
    cli.add_argument("--model-cache", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    evaluate(args.reference.resolve(), args.model_cache.resolve(), args.output.resolve())
