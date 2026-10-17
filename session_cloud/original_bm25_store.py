"""Original Docker algorithm; only configuration, path injection and singleton lifecycle adapted."""
import gzip
import json
import os
import uuid
from typing import List, Tuple

from rank_bm25 import BM25Okapi

from types import SimpleNamespace
import re

settings = SimpleNamespace(EMBEDDING_DIMENSION=384, EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2")


def ensure_project_id(project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise ValueError("Invalid private shard")


class BM25Store:
    """
    Keyword-based search index (one per project_id).
    Stored as compressed JSON on disk alongside the FAISS index.

    Why BM25 alongside FAISS?
    ─────────────────────────
    FAISS works on meaning  → "medication side effects" matches "drug adverse reactions"
    BM25  works on words    → "FDA" only matches chunks that literally contain "FDA"

    BM25 is critical for:
      • Exact names     — person names, company names, drug names
      • Acronyms        — FDA, RCT, GDP, API, NLP
      • Specific codes  — ICD-10 codes, legal article numbers
      • Unique IDs      — contract numbers, case IDs

    Both indexes are searched on every query.
    Results are merged using Reciprocal Rank Fusion (done in hybrid_search.py).
    """

    def __init__(self, index_dir):
        self.index_dir = str(index_dir)
        # project_id → BM25Okapi index
        self._indexes: dict[str, BM25Okapi] = {}
        # project_id → list of chunk_ids (position matches BM25 corpus position)
        self._chunk_ids: dict[str, List[str]] = {}
        # project_id → tokenized corpus (needed to rebuild index on new additions)
        self._corpus: dict[str, List[List[str]]] = {}
        # tracks disk file mtime at last load — detects Celery worker updates
        self._mtimes: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        project_id: str,
        texts: List[str],
        chunk_ids: List[str],
    ):
        """
        Add a batch of chunks to the BM25 index for a project.
        Rebuilds the full index after adding (BM25Okapi is not incremental).

        Empty-tokenized chunks are excluded: they can never match a keyword
        query, and BM25Okapi raises ZeroDivisionError on an empty corpus.
        """
        if len(texts) != len(chunk_ids):
            raise ValueError("Text and chunk ID counts do not match")
        self._load(project_id)

        # Keep only chunks that produce at least one token after tokenization.
        # Chunks with no tokens (empty text, image-only pages) are useless for
        # keyword search and would cause BM25Okapi to crash with ZeroDivisionError.
        for text, cid in zip(texts, chunk_ids):
            tokens = self._tokenize(text)
            if tokens:
                self._corpus[project_id].append(tokens)
                self._chunk_ids[project_id].append(cid)

        # Only build the index when we have at least one non-empty document.
        if self._corpus[project_id]:
            self._indexes[project_id] = BM25Okapi(self._corpus[project_id])
        # else: index stays None — search() already handles None by returning []

        self._save(project_id)

    def replace_project(
        self, project_id: str, texts: List[str], chunk_ids: List[str]
    ) -> None:
        """Build and atomically persist a fresh project BM25 index."""
        if len(texts) != len(chunk_ids):
            raise ValueError("Text and chunk ID counts do not match")
        corpus = []
        kept_ids = []
        for text, chunk_id in zip(texts, chunk_ids):
            tokens = self._tokenize(text)
            if tokens:
                corpus.append(tokens)
                kept_ids.append(chunk_id)
        index = BM25Okapi(corpus) if corpus else None

        previous = (
            self._indexes.get(project_id),
            self._chunk_ids.get(project_id),
            self._corpus.get(project_id),
        )
        self._indexes[project_id] = index
        self._chunk_ids[project_id] = kept_ids
        self._corpus[project_id] = corpus
        try:
            self._save(project_id)
        except Exception:
            if previous[1] is None:
                self._indexes.pop(project_id, None)
                self._chunk_ids.pop(project_id, None)
                self._corpus.pop(project_id, None)
                self._mtimes.pop(project_id, None)
            else:
                self._indexes[project_id], self._chunk_ids[project_id], self._corpus[project_id] = previous
            raise

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(
        self,
        project_id: str,
        query: str,
        top_k: int = 50,
    ) -> List[Tuple[str, float]]:
        """
        Returns list of (chunk_id, score) sorted best-first.
        Only returns chunks with score > 0 (i.e. at least one keyword matched).
        """
        self._load(project_id)

        index = self._indexes.get(project_id)
        if index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = index.get_scores(tokenized_query)

        # sort indices by score descending, keep top_k
        ranked = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:top_k]

        chunk_ids = self._chunk_ids[project_id]
        return [
            (chunk_ids[i], float(scores[i]))
            for i in ranked
            if scores[i] > 0
        ]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_project(self, project_id: str):
        """Remove BM25 index for a project from memory and disk."""
        self._indexes.pop(project_id, None)
        self._chunk_ids.pop(project_id, None)
        self._corpus.pop(project_id, None)
        self._mtimes.pop(project_id, None)

        for path in [self._store_path(project_id), self._legacy_store_path(project_id)]:
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        """Simple whitespace tokenizer + lowercase."""
        return text.lower().split()

    def _store_path(self, project_id: str) -> str:
        ensure_project_id(project_id)
        dir_path = os.path.join(self.index_dir, project_id)
        os.makedirs(dir_path, exist_ok=True)
        return os.path.join(dir_path, "bm25.json.gz")

    def _legacy_store_path(self, project_id: str) -> str:
        ensure_project_id(project_id)
        dir_path = os.path.join(self.index_dir, project_id)
        os.makedirs(dir_path, exist_ok=True)
        return os.path.join(dir_path, "bm25.pkl")

    def _load(self, project_id: str):
        """
        Load from disk if not already in memory or if the on-disk file has
        been updated since last load.

        The Celery worker runs in a separate process and writes new data to
        disk after each ingest. Checking the file's mtime lets the FastAPI
        process detect those writes and refresh its in-memory state.
        """
        path = self._store_path(project_id)
        if os.path.exists(path):
            current_mtime = os.path.getmtime(path)
            if (project_id not in self._indexes
                    or self._mtimes.get(project_id, -1) != current_mtime):
                with gzip.open(path, "rt", encoding="utf-8") as stored:
                    data = json.load(stored)
                chunk_ids = data.get("chunk_ids")
                corpus = data.get("corpus")
                if (
                    data.get("version") != 1
                    or not isinstance(chunk_ids, list)
                    or not isinstance(corpus, list)
                    or len(chunk_ids) != len(corpus)
                    or not all(isinstance(chunk_id, str) for chunk_id in chunk_ids)
                    or not all(
                        isinstance(tokens, list)
                        and all(isinstance(token, str) for token in tokens)
                        for tokens in corpus
                    )
                ):
                    raise RuntimeError("Invalid BM25 index archive")
                self._indexes[project_id] = BM25Okapi(corpus) if corpus else None
                self._chunk_ids[project_id] = chunk_ids
                self._corpus[project_id] = corpus
                self._mtimes[project_id]    = current_mtime
        elif os.path.exists(self._legacy_store_path(project_id)):
            raise RuntimeError(
                "Legacy pickle-based BM25 files were found. Run "
                "'python scripts/rebuild_indexes.py --project "
                f"{project_id}' from a trusted application checkout."
            )
        elif project_id not in self._indexes:
            # First time — start empty
            self._indexes[project_id]   = None
            self._chunk_ids[project_id] = []
            self._corpus[project_id]    = []
            self._mtimes[project_id]    = -1

    def _save(self, project_id: str):
        """Persist index, chunk IDs, and corpus to disk."""
        path = self._store_path(project_id)
        temporary = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            with gzip.open(temporary, "wt", encoding="utf-8") as stored:
                json.dump(
                    {
                        "version": 1,
                        "chunk_ids": self._chunk_ids[project_id],
                        "corpus": self._corpus[project_id],
                    },
                    stored,
                    separators=(",", ":"),
                )
            os.replace(temporary, path)
            self._mtimes[project_id] = os.path.getmtime(path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
