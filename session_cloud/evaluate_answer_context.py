"""Offline Phase 2C1 original-source comparison; never an AI-answer benchmark."""
import argparse
import ast
from dataclasses import asdict
import hashlib
from io import BytesIO
import json
from pathlib import Path
import statistics
import sys
import time
from types import ModuleType, SimpleNamespace

from session_cloud.attribution import format_attribution
from session_cloud.context_engine import actual_token_count
from session_cloud.evaluation_corpus import DOCUMENTS, QUESTIONS
from session_cloud.evaluate_retrieval import pdf_bytes
from session_cloud.index_contract import IndexLimits
from session_cloud.index_engine import SHARD, load
from session_cloud.original_context import RetrievedChunk
from session_cloud.runtime import SessionError, WorkspaceStore


def source_module(path, name, **provided):
    """Only replace deployment imports/singletons; execute original bodies."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    tree.body = [n for n in tree.body
        if not (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app."))
        and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
            and t.id in {"reranker", "embedder", "vector_store", "bm25_store", "_llm"} for t in n.targets))]
    module = ModuleType(name)
    module.__dict__.update(provided)
    sys.modules[name] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


class Predicate:
    def __init__(self, test):
        self.test = test

    def __or__(self, other):
        return Predicate(lambda row: self.test(row) or other.test(row))


class Column:
    def __init__(self, side, key):
        self.side, self.key = side, key

    def value(self, row):
        return getattr(row[self.side], self.key)

    def in_(self, values):
        return Predicate(lambda row: self.value(row) in values)

    def __eq__(self, other):
        return Predicate(lambda row: self.value(row) == (other.value(row) if isinstance(other, Column) else other))


class FixtureQuery:
    """Test-only SQL row acquisition for unchanged original context functions."""
    def __init__(self, rows):
        self.rows = rows

    def query(self, *_):
        return self

    def join(self, _model, predicate):
        return self.filter(predicate)

    def filter(self, *predicates):
        return FixtureQuery([r for r in self.rows if all(p.test(r) for p in predicates)])

    def all(self):
        return self.rows

    def close(self):
        pass


def metric(results):
    recall, reciprocal = [], []
    for (_, relevant, _), hits in zip(QUESTIONS, results):
        if hits is None:
            continue
        recall.append(sum(any(code in c["text"] for c in hits[:6]) for code in relevant) / len(relevant))
        rank = next((i for i, c in enumerate(hits[:6], 1) if any(code in c["text"] for code in relevant)), None)
        reciprocal.append(1 / rank if rank else 0)
    return {"recall@6": statistics.mean(recall) if recall else None,
            "MRR@6": statistics.mean(reciprocal) if reciprocal else None, "evaluated": len(recall)}


def evaluate(reference, cache, output):
    output.mkdir(parents=True, exist_ok=False)
    token = "d" * 64
    auth = SimpleNamespace(authorize=lambda _: SimpleNamespace(
        user_id="33333333-3333-4333-8333-333333333333", deadline=time.time() + 3600))
    store = WorkspaceStore(auth, base_dir=output, model_cache=cache)
    ref = store.open(token)
    documents, candidate, blocked, times, timings = [], [], [], [], []
    try:
        for filename, records in DOCUMENTS:
            doc = store.upload(token, ref, filename, BytesIO(pdf_bytes(records)))["id"]
            store.prepare(token, ref, doc)
            store.index_document(token, ref, doc)
            documents.append(doc)
        for number, (question, _, _) in enumerate(QUESTIONS, 1):
            started = time.monotonic()
            try:
                result = store.prepare_answer_context(token, ref, question, documents)
                candidate.append(result)
                timings.append(result["metrics"])
                times.append(time.monotonic() - started)
            except SessionError as error:
                if error.code != "context_limit":
                    raise
                candidate.append(None)
                blocked.append({"question": number, "code": error.code})
        with store._db(store.check(token, ref)) as db:
            snapshot = store._snapshot(db, ref)
    finally:
        store._index_service.close()
    chunks, _ = load(snapshot[0], snapshot[1], IndexLimits())
    rows = [(SimpleNamespace(**c, project_id=SHARD), SimpleNamespace(
        doc_id=c["doc_id"], filename=c["filename"], title=None, project_id=SHARD)) for c in chunks]
    models = SimpleNamespace(Chunk=SimpleNamespace(**{n: Column(0, n) for n in (
        "chunk_id", "doc_id", "parent_chunk_id", "project_id")}),
        Document=SimpleNamespace(doc_id=Column(1, "doc_id"), project_id=Column(1, "project_id")))
    settings = SimpleNamespace(EMBEDDING_DIMENSION=384, EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2",
        INDEX_DIR=str(snapshot[0]), MAX_CHUNKS_RETRIEVED=20, NEIGHBOR_CHUNKS=1,
        INCLUDE_PARENT_CONTEXT=True, MAX_CONTEXT_TOKENS=4500)
    source_files = {name: reference / path for name, path in {
        "reranker": "app/retrieval/reranker.py", "context": "app/retrieval/context_assembler.py",
        "retriever": "app/reasoning/nodes/retriever_node.py", "hybrid": "app/retrieval/hybrid_search.py",
        "vector": "app/storage/vector_store.py", "bm25": "app/storage/bm25_store.py",
        "rrf": "app/retrieval/rank_fusion.py"}.items()}
    from contextlib import nullcontext
    from session_cloud.original_embedder import Embedder
    from session_cloud.original_reranker import Reranker
    embedding, ranked_model = Embedder(cache), Reranker(cache.with_name(cache.name + "-reranker"))
    modules = {name: source_module(source_files[name], "context_reference_" + name, settings=settings,
                                  ensure_project_id=lambda _: None) for name in ("vector", "bm25", "rrf", "reranker")}
    original_reranker = modules["reranker"].Reranker.__new__(modules["reranker"].Reranker)
    original_reranker._model = ranked_model._model.raw  # default original batch_size=64
    context = source_module(source_files["context"], "context_reference_context", models=models,
                            SessionLocal=lambda: FixtureQuery(rows))
    hybrid = source_module(source_files["hybrid"], "context_reference_hybrid", embedder=embedding,
        vector_store=modules["vector"].VectorStore(), bm25_store=modules["bm25"].BM25Store(),
        project_index_lock=lambda _: nullcontext(), _rrf=modules["rrf"].reciprocal_rank_fusion)
    original = source_module(source_files["retriever"], "context_reference_retriever", settings=settings,
        QAState=dict, RetrievedChunk=context.RetrievedChunk, assemble_context=context.assemble_context,
        expand_context=context.expand_context, hybrid_search=hybrid.hybrid_search, reranker=original_reranker)
    baseline, contexts, token_counts = [], [], []
    for question, _, _ in QUESTIONS:
        reranked = sorted(original._retrieve_for_subq(SHARD, question), key=lambda c: c.score, reverse=True)[:6]
        expanded = original.retriever_node({"project_id": SHARD, "sub_questions": [question], "top_k": 6})["retrieved_chunks"]
        baseline.append([asdict(c) for c in reranked])
        contexts.append([asdict(c) for c in expanded])
        from session_cloud.original_prompts import aggregator_node
        evidence = aggregator_node({"retrieved_chunks": expanded})["aggregated_evidence"]
        token_counts.append(actual_token_count(embedding, evidence))
    def normalized(hits):
        return [(c["doc_id"], c["page_number"], c["page_end"], c["text"]) for c in hits]
    valid_citations, checked_citations = 0, 0
    for item in candidate:
        if item is None:
            continue
        context_items = [RetrievedChunk(**c) for c in item["chunks"]]
        # Deterministic fixture, NOT a Groq answer. Measures source provenance
        # and exact quoted text only; no semantic-entailment claim.
        fixture_answer = context_items[0].text[:400] + " [1]"
        citation = format_attribution(fixture_answer, context_items)["citations"][0]
        checked_citations += 1
        source = context_items[0]
        valid_citations += int(citation["doc_id"] == source.doc_id and citation["filename"] == source.filename
            and citation["page_number"] == source.page_number and citation["page_end"] == source.page_end)
    report = {"scope": "Original-source reranking/expansion, no provider generation",
        "questions": len(QUESTIONS), "documents": 3, "pages": 18,
        "candidate": metric([c["selected_chunks"] if c else None for c in candidate]), "original": metric(baseline),
        "blocked": blocked, "original_actual_context_tokens": token_counts,
        "identical_selected_order": sum(c is not None and normalized(c["selected_chunks"]) == normalized(b)
                                         for c, b in zip(candidate, baseline)),
        "identical_expanded_order": sum(c is not None and normalized(c["chunks"]) == normalized(b)
                                         for c, b in zip(candidate, contexts)),
        "fixture_citation_provenance": {"valid": valid_citations, "checked": checked_citations},
        "metrics": timings, "context_request_seconds": {"first": times[0] if times else None,
            "median": statistics.median(times) if times else None, "max": max(times) if times else None},
        "reference_sha256": {name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in source_files.items()},
        "notes": ["Original source under matched Docker retrieval library versions on Windows/Python 3.12",
                  "Original SQL reads use a predicate-filtered fixture adapter over the identical verified snapshot",
                  "Candidate batch size 1 versus original default 64", "No LLM answer or streaming accuracy measured"]}
    (output / "answer-context-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"metrics", "reference_sha256"}}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.reference.resolve(), args.model_cache.resolve(), args.output.resolve())
