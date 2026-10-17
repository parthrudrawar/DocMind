"""Reproducible offline, real-model comparison with the read-only Docker source.

python -B -m session_cloud.evaluate_retrieval --reference <Docker checkout>
    --model-cache <public MiniLM cache> --output <new isolated report directory>
No .env, provider credentials, database, or Docker service is read or started.
"""
import argparse
import ast
from contextlib import nullcontext
import hashlib
from io import BytesIO
import json
from pathlib import Path
import statistics
import sys
import time
from types import ModuleType, SimpleNamespace

import fitz

from session_cloud.bounded_pdf import peak_rss_bytes
from session_cloud.evaluation_corpus import DOCUMENTS, QUESTIONS, paragraphs
from session_cloud.runtime import WorkspaceStore


def original_module(path, name, settings):
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    tree.body = [node for node in tree.body
                 if not (isinstance(node, ast.ImportFrom) and node.module in {"app.config", "app.project_ids"})
                 and not (isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id in {"embedder", "vector_store", "bm25_store"}
                     for target in node.targets))]
    module = ModuleType(name)
    module.__dict__.update(settings=settings, ensure_project_id=lambda project: None)
    sys.modules[name] = module
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def pdf_bytes(records):
    with fitz.open() as document:
        for text in paragraphs(records):
            page = document.new_page()
            if page.insert_textbox(fitz.Rect(35, 35, 560, 805), text, fontsize=9) < 0:
                raise RuntimeError("Synthetic fixture did not fit; never silently truncate")
        return document.tobytes()


def scores(results, k):
    recalls, reciprocal = [], []
    for (_, relevant, _), hits in zip(QUESTIONS, results):
        found = {code for code in relevant if any(code in h["text"] for h in hits[:k])}
        recalls.append(len(found) / len(relevant))
        rank = next((i for i, h in enumerate(hits, 1) if any(code in h["text"] for code in relevant)), None)
        reciprocal.append(1 / rank if rank else 0)
    return {f"recall@{k}": statistics.mean(recalls), "MRR@30": statistics.mean(reciprocal)}


def evaluate(reference, cache, output):
    output.mkdir(parents=True, exist_ok=False)  # never overwrite another run/data
    token = "d" * 64  # synthetic capability, never a provider token
    auth = SimpleNamespace(authorize=lambda _: SimpleNamespace(
        user_id="33333333-3333-4333-8333-333333333333", deadline=time.time() + 3600))
    store = WorkspaceStore(auth, base_dir=output, model_cache=cache)
    ref = store.open(token)
    documents, indexing = [], []
    candidate, latency = [], []
    try:
        for filename, records in DOCUMENTS:
            data = pdf_bytes(records)
            doc = store.upload(token, ref, filename, BytesIO(data))["id"]
            preparation = store.prepare(token, ref, doc)
            documents.append({"id": doc, "filename": filename, "pages": store.parsed_pages(token, ref, doc),
                              "preparation_metrics": preparation["metrics"]})
            indexing.append(store.index_document(token, ref, doc))
        selected = [d["id"] for d in documents]
        for question, _, _ in QUESTIONS:
            started = time.monotonic()
            candidate.append(store.retrieve(token, ref, question, selected, top_k=30))
            latency.append(time.monotonic() - started)
    finally:
        store._index_service.close()  # no second concurrently resident model for baseline

    settings = SimpleNamespace(EMBEDDING_DIMENSION=384, EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2",
                               INDEX_DIR=str(output / "original-baseline"), MODEL_THREADS=1)
    files = {name: reference / path for name, path in {
        "chunker": "app/ingestion/chunker.py", "embedder": "app/ingestion/embedder.py",
        "vector": "app/storage/vector_store.py", "bm25": "app/storage/bm25_store.py",
        "rrf": "app/retrieval/rank_fusion.py", "hybrid": "app/retrieval/hybrid_search.py"}.items()}
    modules = {name: original_module(path, "reference_" + name, settings)
               for name, path in files.items() if name != "hybrid"}
    from session_cloud.original_embedder import Embedder
    model = Embedder(cache)
    original_embedder = modules["embedder"].Embedder.__new__(modules["embedder"].Embedder)
    original_embedder._model = model._model
    chunker = modules["chunker"].Chunker(original_embedder.count_tokens, 220, 35, 800)
    chunks = [chunk for doc in documents for chunk in chunker.chunk_document(
        doc["id"], [SimpleNamespace(**page) for page in doc["pages"]])]
    children = [c for c in chunks if c.indexable]
    dense, sparse = modules["vector"].VectorStore(), modules["bm25"].BM25Store()
    shard = "reference"
    dense.replace_project(shard, [original_embedder.embed_texts([c.text for c in children])], [c.chunk_id for c in children])
    sparse.replace_project(shard, [c.text for c in children], [c.chunk_id for c in children])
    tree = ast.parse(files["hybrid"].read_text(encoding="utf-8-sig"))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    namespace = dict(embedder=original_embedder, vector_store=dense, bm25_store=sparse,
                     project_index_lock=lambda _: nullcontext(), _rrf=modules["rrf"].reciprocal_rank_fusion,
                     List=list, Tuple=tuple)
    exec(compile(tree, str(files["hybrid"]), "exec"), namespace)
    lookup = {c.chunk_id: c for c in children}
    baseline = [[{"doc_id": lookup[cid].doc_id, "page_number": lookup[cid].page_number,
                  "text": lookup[cid].text, "score": score}
                 for cid, score in namespace["hybrid_search"](shard, question, top_k=30)]
                for question, _, _ in QUESTIONS]
    def normalized(hits):
        return [(h["doc_id"], h["page_number"], h["text"]) for h in hits]
    report = {
        "scope": "Original pre-rerank hybrid retrieval, not full QA/cross-encoder parity",
        "documents": len(documents), "pages": sum(len(d["pages"]) for d in documents),
        "questions": len(QUESTIONS), "searchable_chunks": len(children),
        "candidate": {**scores(candidate, 1), **scores(candidate, 5), **scores(candidate, 10)},
        "original": {**scores(baseline, 1), **scores(baseline, 5), **scores(baseline, 10)},
        "identical_top30_order_queries": sum(normalized(a) == normalized(b) for a, b in zip(candidate, baseline)),
        "chunk_boundaries_match": { (c.doc_id, c.page_number, c.text) for c in children} == {
            (c["doc_id"], c["page_number"], c["text"]) for hits in candidate for c in hits},
        "indexing": indexing, "preparation": [d["preparation_metrics"] for d in documents],
        "retrieval_wall_seconds": {"median": statistics.median(latency), "max": max(latency)},
        "evaluation_host_peak_rss_bytes": peak_rss_bytes(),
        "source_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()},
        "questions_detail": [{"question": q, "expected": expected, "kind": kind,
                              "candidate_found_top5": [x for x in expected if any(x in h["text"] for h in hits[:5])]}
                             for (q, expected, kind), hits in zip(QUESTIONS, candidate)],
    }
    # Compare every stored child, not just those reached by the question sample.
    from session_cloud.index_engine import load
    from session_cloud.index_contract import IndexLimits
    with store._db(store.check(token, ref)) as db:
        snapshot = store._snapshot(db, ref)
    saved, _ = load(snapshot[0], snapshot[1], IndexLimits())
    report["chunk_boundaries_match"] = {(c.doc_id, c.page_number, c.text) for c in children} == {
        (c["doc_id"], c["page_number"], c["text"]) for c in saved if c["indexable"]}
    (output / "retrieval-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"indexing", "preparation", "source_sha256", "questions_detail"}}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.reference.resolve(), args.model_cache.resolve(), args.output.resolve())
