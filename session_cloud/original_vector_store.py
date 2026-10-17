"""Original Docker algorithm; only configuration, path injection and singleton lifecycle adapted."""
import json
import os
import uuid
import zipfile
from typing import List, Tuple

import faiss
import numpy as np

from types import SimpleNamespace
import re

settings = SimpleNamespace(EMBEDDING_DIMENSION=384, EMBEDDING_MODEL="sentence-transformers/all-MiniLM-L6-v2")


def ensure_project_id(project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise ValueError("Invalid private shard")


class VectorStore:
    """
    FAISS HNSW index, one shard per project_id.

    - Loaded lazily from disk on first access.
    - Written to disk after every batch of additions.
    - Only active project shards stay in RAM.

    Why HNSW?
      Approximate nearest-neighbour search in O(log n) time.
      Fast enough for millions of chunks without needing a GPU.
    """

    DIMENSION = settings.EMBEDDING_DIMENSION
    HNSW_M = 32              # number of connections per node (higher = more accurate, more RAM)
    EF_CONSTRUCTION = 200    # build-time accuracy
    EF_SEARCH = 50           # query-time accuracy

    def __init__(self, index_dir):
        self.index_dir = str(index_dir)
        self._indexes: dict[str, faiss.Index] = {}
        # maps position-in-FAISS-index → chunk_id  (per project)
        self._id_maps: dict[str, List[str]] = {}
        # tracks disk file mtime at last load — detects Celery worker updates
        self._mtimes: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        project_id: str,
        embeddings: np.ndarray,      # shape (N, 384)
        chunk_ids: List[str],
    ) -> List[int]:
        """
        Add embeddings to the project's index AND immediately save to disk.
        Use add_batch() + flush() instead when adding multiple batches to
        avoid redundant disk writes.
        Returns the FAISS positions assigned to each chunk.
        """
        self._validate_batch(embeddings, chunk_ids)
        self._load(project_id)

        start_pos = self._indexes[project_id].ntotal
        self._indexes[project_id].add(embeddings)
        self._id_maps[project_id].extend(chunk_ids)

        self._save(project_id)

        return list(range(start_pos, start_pos + len(chunk_ids)))

    def add_batch(
        self,
        project_id: str,
        embeddings: np.ndarray,
        chunk_ids: List[str],
    ) -> List[int]:
        """
        Add a batch of embeddings to the in-memory index WITHOUT saving to disk.
        Call flush(project_id) once after all batches are added.

        This avoids rewriting the full .index file after every 512-chunk batch
        (which adds ~0.5–1s per batch = 3–6s wasted for a 3000-chunk document).
        """
        self._validate_batch(embeddings, chunk_ids)
        self._load(project_id)

        start_pos = self._indexes[project_id].ntotal
        self._indexes[project_id].add(embeddings)
        self._id_maps[project_id].extend(chunk_ids)
        # No _save() here — caller must call flush() when done

        return list(range(start_pos, start_pos + len(chunk_ids)))

    def replace_project(
        self, project_id: str, embedding_batches: List[np.ndarray], chunk_ids: List[str]
    ) -> List[int]:
        """Build and atomically persist a fresh project index."""
        total_embeddings = 0
        for embeddings in embedding_batches:
            if embeddings.ndim != 2 or embeddings.shape[1] != self.DIMENSION:
                raise ValueError("Embedding batch has an invalid shape")
            if not np.isfinite(embeddings).all():
                raise ValueError("Embedding batch contains non-finite values")
            total_embeddings += len(embeddings)
        if total_embeddings != len(chunk_ids):
            raise ValueError("Embedding and chunk ID counts do not match")

        index = faiss.IndexHNSWFlat(
            self.DIMENSION, self.HNSW_M, faiss.METRIC_INNER_PRODUCT
        )
        index.hnsw.efConstruction = self.EF_CONSTRUCTION
        index.hnsw.efSearch = self.EF_SEARCH
        for embeddings in embedding_batches:
            if len(embeddings):
                index.add(embeddings)

        previous_index = self._indexes.get(project_id)
        previous_ids = self._id_maps.get(project_id)
        self._indexes[project_id] = index
        self._id_maps[project_id] = list(chunk_ids)
        try:
            self._save(project_id)
        except Exception:
            if previous_index is None:
                self._indexes.pop(project_id, None)
                self._id_maps.pop(project_id, None)
                self._mtimes.pop(project_id, None)
            else:
                self._indexes[project_id] = previous_index
                self._id_maps[project_id] = previous_ids
            raise
        return list(range(len(chunk_ids)))

    def flush(self, project_id: str):
        """
        Persist the current in-memory index to disk.
        Call this once after all add_batch() calls are complete.
        """
        if project_id in self._indexes:
            self._save(project_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search(
        self,
        project_id: str,
        query_embedding: np.ndarray,   # shape (1, 384)
        top_k: int = 50,
    ) -> List[Tuple[str, float]]:
        """
        Returns list of (chunk_id, score) sorted best-first.
        Score is cosine similarity (0–1, higher = more relevant).
        """
        self._load(project_id)
        index = self._indexes[project_id]

        if index.ntotal == 0:
            return []

        k = min(top_k, index.ntotal)
        scores, positions = index.search(query_embedding, k)

        results = []
        id_map = self._id_maps[project_id]
        for score, pos in zip(scores[0], positions[0]):
            if 0 <= pos < len(id_map):
                results.append((id_map[pos], float(score)))

        return results

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_project(self, project_id: str):
        """Remove all vectors for a project from memory and disk."""
        self._indexes.pop(project_id, None)
        self._id_maps.pop(project_id, None)
        self._mtimes.pop(project_id, None)

        for path in [
            self._archive_path(project_id),
            self._legacy_store_path(project_id),
            self._legacy_index_path(project_id),
            self._legacy_map_path(project_id),
        ]:
            if os.path.exists(path):
                os.remove(path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _project_dir(self, project_id: str) -> str:
        ensure_project_id(project_id)
        path = os.path.join(self.index_dir, project_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _legacy_index_path(self, project_id: str) -> str:
        return os.path.join(self._project_dir(project_id), "faiss.index")

    def _legacy_map_path(self, project_id: str) -> str:
        return os.path.join(self._project_dir(project_id), "chunk_ids.pkl")

    def _legacy_store_path(self, project_id: str) -> str:
        return os.path.join(self._project_dir(project_id), "faiss_store.pkl")

    def _archive_path(self, project_id: str) -> str:
        return os.path.join(self._project_dir(project_id), "faiss_store.zip")

    def _load(self, project_id: str):
        """
        Load index from disk into memory if not already loaded or if the
        on-disk file has been updated since last load.

        The Celery worker runs in a separate process and writes new indexes
        to disk after each ingest. Using the file's mtime lets the FastAPI
        process detect those writes and refresh its in-memory state without
        any cross-process signalling.
        """
        archive_path = self._archive_path(project_id)

        if os.path.exists(archive_path):
            current_mtime = os.path.getmtime(archive_path)
            # Re-load if: first time, or disk file is newer than what we have
            if (project_id not in self._indexes
                    or self._mtimes.get(project_id, -1) != current_mtime):
                with zipfile.ZipFile(archive_path, "r") as archive:
                    expected = {"index.bin", "chunk_ids.json", "metadata.json"}
                    if set(archive.namelist()) != expected:
                        raise RuntimeError("Invalid FAISS archive contents")
                    metadata = json.loads(archive.read("metadata.json"))
                    chunk_ids = json.loads(archive.read("chunk_ids.json"))
                    index_bytes = archive.read("index.bin")

                if metadata.get("dimension") != self.DIMENSION:
                    raise RuntimeError(
                        "Stored FAISS dimension does not match EMBEDDING_DIMENSION. "
                        "Rebuild project indexes before serving queries."
                    )
                if metadata.get("embedding_model") != settings.EMBEDDING_MODEL:
                    raise RuntimeError(
                        "Stored FAISS embedding model does not match configuration. "
                        "Rebuild project indexes before serving queries."
                    )
                if metadata.get("metric") != "inner_product":
                    raise RuntimeError(
                        "Stored FAISS similarity metric is incompatible. "
                        "Rebuild project indexes before serving queries."
                    )
                if not isinstance(chunk_ids, list) or not all(
                    isinstance(chunk_id, str) for chunk_id in chunk_ids
                ):
                    raise RuntimeError("Invalid FAISS chunk ID map")

                serialized = np.frombuffer(index_bytes, dtype=np.uint8)
                index = faiss.deserialize_index(serialized)
                if index.d != self.DIMENSION or index.ntotal != len(chunk_ids):
                    raise RuntimeError("FAISS index metadata is inconsistent")
                self._indexes[project_id] = index
                self._id_maps[project_id] = chunk_ids
                self._mtimes[project_id] = current_mtime
        elif any(
            os.path.exists(path)
            for path in (
                self._legacy_store_path(project_id),
                self._legacy_index_path(project_id),
                self._legacy_map_path(project_id),
            )
        ):
            raise RuntimeError(
                "Legacy pickle-based FAISS files were found. Run "
                "'python scripts/rebuild_indexes.py --project "
                f"{project_id}' from a trusted application checkout."
            )
        elif project_id not in self._indexes:
            # First time for this project — create a fresh HNSW index
            index = faiss.IndexHNSWFlat(
                self.DIMENSION, self.HNSW_M, faiss.METRIC_INNER_PRODUCT
            )
            index.hnsw.efConstruction = self.EF_CONSTRUCTION
            index.hnsw.efSearch = self.EF_SEARCH
            self._indexes[project_id] = index
            self._id_maps[project_id] = []
            self._mtimes[project_id] = -1

    def _save(self, project_id: str):
        """Persist index and ID map to disk."""
        archive_path = self._archive_path(project_id)
        temporary_path = f"{archive_path}.{uuid.uuid4().hex}.tmp"
        try:
            serialized = faiss.serialize_index(self._indexes[project_id]).tobytes()
            metadata = {
                "version": 1,
                "dimension": self.DIMENSION,
                "embedding_model": settings.EMBEDDING_MODEL,
                "metric": "inner_product",
            }
            with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("index.bin", serialized)
                archive.writestr(
                    "chunk_ids.json",
                    json.dumps(self._id_maps[project_id], separators=(",", ":")),
                )
                archive.writestr(
                    "metadata.json", json.dumps(metadata, separators=(",", ":"))
                )
            os.replace(temporary_path, archive_path)
            self._mtimes[project_id] = os.path.getmtime(archive_path)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    def _validate_batch(
        self, embeddings: np.ndarray, chunk_ids: List[str]
    ) -> None:
        if embeddings.ndim != 2 or embeddings.shape[1] != self.DIMENSION:
            raise ValueError("Embedding batch has an invalid shape")
        if embeddings.dtype != np.float32 or not embeddings.flags.c_contiguous:
            raise ValueError("Embedding batch must be contiguous float32")
        if len(embeddings) != len(chunk_ids):
            raise ValueError("Embedding and chunk ID counts do not match")
        if not all(isinstance(chunk_id, str) and chunk_id for chunk_id in chunk_ids):
            raise ValueError("Chunk IDs must be non-empty strings")
        if not np.isfinite(embeddings).all():
            raise ValueError("Embedding batch contains non-finite values")
