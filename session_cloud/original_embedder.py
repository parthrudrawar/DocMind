"""Original Docker algorithm; only configuration, path injection and singleton lifecycle adapted."""
import logging
import re
from pathlib import Path
from typing import List

import numpy as np
from fastembed import TextEmbedding

from types import SimpleNamespace

settings = SimpleNamespace(EMBEDDING_DIMENSION=384, EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2", MODEL_THREADS=1)

logger = logging.getLogger(__name__)

# Keep ONNX-runtime threads low so concurrent model workloads stay bounded.
# Optimal batch size for all-MiniLM-L6-v2 on CPU. Larger = more RAM.
_SINGLE_BATCH = 128


def _l2_normalize(arr: np.ndarray) -> np.ndarray:
    """Make every row a unit vector so dot product == cosine similarity."""
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class Embedder:
    """
    Converts text into 384-dim float32 vectors.

    Uses fastembed (ONNX runtime) instead of sentence-transformers (PyTorch).
    The model is identical (all-MiniLM-L6-v2) but the runtime is ~10x lighter
    on RAM compared with the equivalent PyTorch runtime.

    Loaded once and reused — the model stays in memory across calls.
    """

    DIMENSION = settings.EMBEDDING_DIMENSION

    def __init__(self, cache_dir):
        # fastembed downloads a small quantized ONNX model on first use and
        # caches it; subsequent loads are instant.
        self._model = TextEmbedding(
            model_name=settings.EMBEDDING_MODEL,
            cache_dir=str(cache_dir),
            threads=settings.MODEL_THREADS,
            specific_model_path=str(cache_dir) if (Path(cache_dir) / "model.onnx").is_file() else None,
        )
        # Warm up the ONNX session at startup so the FIRST real embed call is
        # fast (otherwise the first PDF pays the one-time session-init cost).
        try:
            warmup = np.asarray(list(self._model.embed(["warmup"])))
            self._validate_dimension(warmup)
        except Exception as exc:
            raise RuntimeError(
                "Embedding model initialization or dimension validation failed"
            ) from exc

    def _validate_dimension(self, vectors: np.ndarray) -> None:
        if vectors.ndim != 2 or vectors.shape[1] != self.DIMENSION:
            actual = vectors.shape[1] if vectors.ndim == 2 else "invalid"
            raise RuntimeError(
                f"Embedding model produced dimension {actual}; configured "
                f"EMBEDDING_DIMENSION is {self.DIMENSION}. Reconfigure and rebuild indexes."
            )

    def embed_texts(
        self, texts: List[str], batch_size: int = _SINGLE_BATCH
    ) -> np.ndarray:
        """
        Embed a list of texts.
        Returns a float32 numpy array of shape (len(texts), 384), L2-normalised.
        """
        if not texts:
            return np.zeros((0, self.DIMENSION), dtype=np.float32)

        vecs = np.asarray(
            list(self._model.embed(texts, batch_size=batch_size)),
            dtype=np.float32,
        )
        self._validate_dimension(vecs)
        return _l2_normalize(vecs)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a single query string.
        Returns shape (1, 384) — ready to pass directly to FAISS search.
        """
        vec = np.asarray(list(self._model.embed([query])), dtype=np.float32)
        self._validate_dimension(vec)
        return _l2_normalize(vec)

    def count_tokens(self, text: str) -> int:
        """Count tokens with the same tokenizer used by the embedding model."""
        if not text:
            return 0

        token_count = getattr(self._model, "token_count", None)
        if callable(token_count):
            try:
                return int(token_count(text))
            except Exception:
                logger.debug("FastEmbed token_count helper failed", exc_info=True)

        # Compatibility fallback for older fastembed releases.
        model = getattr(self._model, "model", None)
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is not None:
            try:
                encoded = tokenizer.encode(text)
                return len(encoded.ids)
            except Exception:
                logger.debug("FastEmbed tokenizer fallback failed", exc_info=True)

        # Last-resort approximation. This is deliberately conservative for
        # punctuation-heavy, numeric, and technical text.
        return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))
