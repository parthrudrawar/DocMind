"""Original cross-encoder wrapper; explicit cache/lifecycle and bounded batch scheduling."""
from typing import List, Tuple

from fastembed.rerank.cross_encoder import TextCrossEncoder

from pathlib import Path
import math
from session_cloud.index_contract import IndexFailure
from types import SimpleNamespace

settings = SimpleNamespace(RERANKER_MODEL="Xenova/ms-marco-MiniLM-L-6-v2", MODEL_THREADS=1)


class _BoundedModel:
    """Retain model/scoring behavior; bound ONNX activation batches to one."""
    def __init__(self, raw):
        self.raw = raw

    def rerank(self, query, docs):
        scores = list(self.raw.rerank(query, docs, batch_size=1))
        if len(scores) != len(docs) or any(not math.isfinite(float(s)) for s in scores):
            raise IndexFailure("context_invalid")
        return scores


# Keep ONNX-runtime threads low so concurrent model workloads stay bounded.
class Reranker:
    """
    Cross-encoder re-ranking of candidate chunks.

    Uses fastembed (ONNX runtime) instead of sentence-transformers (PyTorch)
    so it runs without the heavy torch dependency — the underlying model is the
    same ms-marco-MiniLM cross-encoder.

    Why cross-encoder after FAISS + BM25?
    ───────────────────────────────────────
    FAISS/BM25 score query and chunk separately (fast but approximate).
    The cross-encoder reads (query + chunk) TOGETHER in one pass — much more
    accurate. We only run it on ~25 candidates, so it stays fast.
    """

    def __init__(self, cache_dir):
        self._model = _BoundedModel(TextCrossEncoder(
            model_name=settings.RERANKER_MODEL,
            cache_dir=str(cache_dir),
            threads=settings.MODEL_THREADS,
            specific_model_path=str(cache_dir) if (Path(cache_dir) / "onnx/model.onnx").is_file() else None,
        ))
        # Warm up the ONNX session at startup so the FIRST real query is fast.
        try:
            list(self._model.rerank("warmup", ["warmup text"]))
        except Exception as exc:
            raise RuntimeError("Reranker model initialization failed") from exc

    def rerank(
        self,
        query: str,
        candidates: List[Tuple[str, str]],   # [(chunk_id, chunk_text), ...]
        top_k: int = 20,
    ) -> List[Tuple[str, float]]:
        """
        Score each (query, chunk_text) pair and return top_k sorted best-first.
        Returns list of (chunk_id, score).
        """
        if not candidates:
            return []

        docs   = [text for _, text in candidates]
        scores = list(self._model.rerank(query, docs))

        ranked = sorted(
            zip([cid for cid, _ in candidates], scores),
            key=lambda x: x[1],
            reverse=True,
        )

        return [(cid, float(score)) for cid, score in ranked[:top_k]]
