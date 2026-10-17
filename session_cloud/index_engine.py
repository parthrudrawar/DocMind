"""Original algorithms, with immutable complete snapshots instead of shared shards.

No authentication happens here: WorkspaceStore authorizes every call before and
after the supervised worker. Never pass browser paths or document claims here.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import faiss
import numpy as np

from app.ingestion.chunker import Chunker
from app.retrieval.rank_fusion import reciprocal_rank_fusion
from session_cloud.bounded_pdf import peak_rss_bytes
from session_cloud.index_contract import IndexFailure
from session_cloud.original_bm25_store import BM25Store
from session_cloud.original_vector_store import VectorStore

SHARD = "0" * 32
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
FILES = {"chunks.json", "vectors.npy", SHARD + "/faiss_store.zip", SHARD + "/bm25.json.gz"}


def _json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def seal(folder, limits):
    files = {path.relative_to(folder).as_posix(): path for path in folder.rglob("*") if path.is_file()}
    if set(files) != FILES or sum(p.stat().st_size for p in files.values()) > limits.snapshot_bytes:
        raise IndexFailure("index_resource")
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    _json(folder / "manifest.json", {"version": 1, "model": MODEL, "files": manifest})
    return hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest()


def load(folder, expected, limits):
    """Authenticate all native input bytes before FAISS deserialization; no pickle."""
    folder = Path(folder)
    files = {p.relative_to(folder).as_posix(): p for p in folder.rglob("*") if p.is_file()}
    if (set(files) != FILES | {"manifest.json"} or any(p.is_symlink() for p in folder.rglob("*"))
            or sum(p.stat().st_size for p in files.values()) > limits.snapshot_bytes + 2048):
        raise IndexFailure()
    if hashlib.sha256(files["manifest.json"].read_bytes()).hexdigest() != expected:
        raise IndexFailure()
    manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("model") != MODEL or set(manifest.get("files", {})) != FILES:
        raise IndexFailure()
    for name in FILES:
        if hashlib.sha256(files[name].read_bytes()).hexdigest() != manifest["files"][name]:
            raise IndexFailure()
    chunks = json.loads(files["chunks.json"].read_text(encoding="utf-8"))
    vectors = np.load(files["vectors.npy"], allow_pickle=False)
    children = [c for c in chunks if c["indexable"]]
    if (not 0 < len(children) <= limits.chunks or len(chunks) > limits.chunks * 3 + 3
            or vectors.shape != (len(children), 384) or vectors.dtype != np.float32
            or not np.isfinite(vectors).all() or len({c["chunk_id"] for c in chunks}) != len(chunks)):
        raise IndexFailure()
    return chunks, vectors


def publish(folder, chunks, vectors, limits):
    folder = Path(folder)
    children = [c for c in chunks if c["indexable"]]
    if not 0 < len(children) <= limits.chunks:
        raise IndexFailure("index_resource")
    ids = [c["chunk_id"] for c in children]
    # Exact original HNSW parameters and BM25 corpus/tokenizer/tie ordering.
    VectorStore(folder).replace_project(SHARD, [vectors], ids)
    BM25Store(folder).replace_project(SHARD, [c["text"] for c in children], ids)
    _json(folder / "chunks.json", chunks)
    np.save(folder / "vectors.npy", vectors, allow_pickle=False)
    return seal(folder, limits)


def build(documents, destination, embedder, limits):
    started, cpu = time.monotonic(), time.process_time()
    faiss.omp_set_num_threads(1)
    chunks, total = [], 0
    for document in documents:
        pages = [SimpleNamespace(**page) for page in document["pages"]]
        total += sum(len(p.text.encode("utf-8")) for p in pages)
        if total > limits.text_bytes:
            raise IndexFailure("index_resource")
        produced = Chunker(embedder.count_tokens, 220, 35, 800).chunk_document(document["id"], pages)
        # Original chunk boundaries unchanged. Replace only random UUIDs, so
        # citation targets and parent links survive a deterministic reindex.
        mapping = {c.chunk_id: hashlib.sha256(
            f"{document['id']}:{number}:{c.text}".encode()).hexdigest() for number, c in enumerate(produced)}
        for chunk in produced:
            row = asdict(chunk)
            row.update(chunk_id=mapping[chunk.chunk_id],
                       parent_chunk_id=mapping.get(chunk.parent_chunk_id), filename=document["filename"])
            chunks.append(row)
        if sum(c["indexable"] for c in chunks) > limits.chunks:
            raise IndexFailure("index_resource")
    children = [c for c in chunks if c["indexable"]]
    if not children:
        raise IndexFailure()
    # Same original embedder/model/normalization; schedule eight items rather
    # than Docker's 128 to bound ONNX activation memory. No text is dropped and
    # no batch becomes searchable independently. Parity is measured, not assumed.
    vectors = embedder.embed_texts([c["text"] for c in children], batch_size=8)
    checksum = publish(destination, chunks, vectors, limits)
    return {"checksum": checksum, "chunks": len(children), "metrics": {
        "wall_seconds": time.monotonic() - started, "cpu_seconds": time.process_time() - cpu,
        "peak_rss_bytes": peak_rss_bytes()}}


def search(folder, checksum, query, selected, embedder, limits, top_k=10):
    chunks, vectors = load(folder, checksum, limits)
    children = [c for c in chunks if c["indexable"]]
    allowed = set(selected)
    if not allowed or not allowed <= {c["doc_id"] for c in children}:
        raise IndexFailure()
    positions = [i for i, c in enumerate(children) if c["doc_id"] in allowed]
    rows = [children[i] for i in positions]
    ids = [c["chunk_id"] for c in rows]
    # Selection is a corpus restriction BEFORE either search, never a post-hoc
    # filter of top-30 candidates. No unselected document can affect scores.
    if len(rows) == len(children):
        dense = VectorStore(folder).search(SHARD, embedder.embed_query(query), top_k=30)
        sparse = BM25Store(folder).search(SHARD, query, top_k=30)
    else:
        index = faiss.IndexHNSWFlat(384, 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction, index.hnsw.efSearch = 200, 50
        index.add(np.ascontiguousarray(vectors[positions]))
        scores, places = index.search(embedder.embed_query(query), min(30, len(ids)))
        dense = [(ids[p], float(s)) for s, p in zip(scores[0], places[0]) if 0 <= p < len(ids)]
        from rank_bm25 import BM25Okapi
        bm25 = BM25Okapi([c["text"].lower().split() for c in rows])
        scores = bm25.get_scores(query.lower().split())
        sparse = [(ids[i], float(scores[i])) for i in sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)[:30]
                  if scores[i] > 0]
    lookup = {c["chunk_id"]: c for c in rows}
    return [{**lookup[cid], "score": score} for cid, score in reciprocal_rank_fusion(dense, sparse, top_k=top_k)]


def remove(folder, checksum, document, destination, limits):
    chunks, vectors = load(folder, checksum, limits)
    children = [c for c in chunks if c["indexable"]]
    keep = [i for i, c in enumerate(children) if c["doc_id"] != document]
    remaining = [c for c in chunks if c["doc_id"] != document]
    if not keep:
        return {"checksum": None, "chunks": 0}
    return {"checksum": publish(destination, remaining, np.ascontiguousarray(vectors[keep]), limits), "chunks": len(keep)}
