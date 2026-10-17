"""Original reranked retrieval, fed only authenticated snapshot rows."""
from dataclasses import asdict
import math
import time
from types import SimpleNamespace

from tokenizers import Tokenizer

from session_cloud.bounded_pdf import peak_rss_bytes
from session_cloud.index_contract import IndexFailure
from session_cloud.index_engine import load, search
from session_cloud.original_context import assemble_context, expand_context
from session_cloud.context_packing import pack_passages


def rows_for(chunks, selected):
    """Replace the original project-scoped SQL JOIN, not its metadata mapping."""
    return [(SimpleNamespace(**chunk), SimpleNamespace(filename=chunk["filename"], title=None))
            for chunk in chunks if chunk["doc_id"] in set(selected)]


def actual_token_count(model, text):
    # The embedding tokenizer may truncate at the model input window. Clone
    # public tokenizer configuration so safety counting never mutates a shared
    # model or undercounts a complete prompt/context. No document token cache.
    tokenizer = getattr(getattr(model._model, "model", None), "tokenizer", None)
    if tokenizer is None:
        raise IndexFailure("index_model")
    counter = Tokenizer.from_str(tokenizer.to_str())
    counter.no_truncation()
    counter.no_padding()
    return len(counter.encode(text).ids)


def validate_context(state, model):
    evidence = state["aggregated_evidence"]
    if len(evidence) > 50_000:
        raise IndexFailure("context_limit")  # never let prompt wrapper silently slice evidence
    count = actual_token_count(model, evidence)
    if count > 8192:
        raise IndexFailure("context_limit")
    return count


def prepare_context(folder, checksum, query, selected, sub_questions, model, reranker, limits, top_k=6, progress=None,
                    include_summaries=False):
    started, cpu = time.monotonic(), time.process_time()
    chunks, _ = load(folder, checksum, limits)
    rows = rows_for(chunks, selected)
    seen, per_question = {}, []
    for sub_q in sub_questions:
        if progress:
            progress("searching", "active")
        # top-100 RRF then [:25] in the original is exactly the same prefix
        # as requesting top-25 here. Both underlying searches still use 30.
        hits = search(folder, checksum, sub_q, selected, model, limits, top_k=25)
        if progress:
            progress("searching", "complete", retrieved_passages=len(hits))
            progress("reranking", "active")
        candidates = assemble_context([(c["chunk_id"], c["score"]) for c in hits], rows)
        reranked = reranker.rerank(sub_q, [(c.chunk_id, c.text) for c in candidates], top_k=20)
        if any(not math.isfinite(score) for _, score in reranked):
            raise IndexFailure("context_invalid")
        score_map = dict(reranked)
        result = []
        # Original _retrieve_for_subq preserves candidate order here; the final
        # retriever stage sorts/deduplicates on cross-encoder scores, not RRF.
        for chunk in candidates:
            if chunk.chunk_id in score_map:
                chunk.score = score_map[chunk.chunk_id]
                result.append(chunk)
        for chunk in result:
            if chunk.chunk_id not in seen or seen[chunk.chunk_id].score < chunk.score:
                seen[chunk.chunk_id] = chunk
        per_question.append({"question": sub_q, "reranked": [asdict(c) for c in sorted(result, key=lambda c: c.score, reverse=True)]})
        if progress:
            progress("reranking", "complete", selected_passages=len(result))
    if progress:
        progress("evidence", "active")
    chosen = sorted(seen.values(), key=lambda c: c.score, reverse=True)[:min(top_k, 20)]
    expanded = expand_context(chosen, rows, neighbor_window=1, include_parents=True, token_budget=4500)
    expanded, state, packing = pack_passages(chosen, expanded,
        count_tokens=lambda text: actual_token_count(model, text))
    actual = validate_context(state, model)
    if any(c.doc_id not in selected for c in expanded):
        raise IndexFailure("context_invalid")
    if progress:
        progress("evidence", "complete", selected_passages=len(expanded))
    result = {"chunks": [asdict(c) for c in expanded], "selected_chunks": [asdict(c) for c in chosen],
            "aggregated_evidence": state["aggregated_evidence"], "sub_questions": list(sub_questions),
            "retrieval_details": per_question,
            "metrics": {"wall_seconds": time.monotonic() - started, "cpu_seconds": time.process_time() - cpu,
                        "peak_rss_bytes": peak_rss_bytes(), "actual_context_tokens": actual,
                        "context_packing": packing}}
    if include_summaries:
        # The original guide uses level=1, NOT a generic summary of retrieved
        # children. Read the already hash-verified, selected-owned L1 rows.
        ids = [(c["chunk_id"], 0.) for c in chunks if c["doc_id"] in selected and c["level"] == 1]
        summaries = assemble_context(ids, rows)
        if len(summaries) != len(selected) or {c.doc_id for c in summaries} != set(selected):
            raise IndexFailure("context_invalid")
        result["document_summaries"] = [asdict(c) for c in summaries]
    return result
