"""Authorized snapshot publication. All paths derive from the live registry."""
import hashlib
import json
import re
import secrets
import shutil

from authenticated_cloud.auth import digest
from session_cloud.index_contract import IndexFailure, IndexLimits


class IndexedWorkspace:
    def _index_active(self, capability, ref, lease):
        def active():
            with self._lock:
                if self._leases.get(digest(capability)) is not lease or lease.expires <= self.clock():
                    from session_cloud.runtime import SessionError
                    raise SessionError("expired")
                self._capacity(lease)
        return active

    def _snapshot(self, db, ref):
        row = db.execute("SELECT name,checksum FROM active_index WHERE singleton=1").fetchone()
        if not row:
            return None
        if not re.fullmatch(r"snapshot-[a-f0-9]{32}", row[0]) or not re.fullmatch(r"[a-f0-9]{64}", row[1]):
            raise IndexFailure()
        folder = self._folder(ref) / row[0]
        if not folder.is_dir():
            raise IndexFailure()
        return folder, row[1]

    def _destination(self, ref):
        destination = self._folder(ref) / ("snapshot-" + secrets.token_hex(16))
        destination.mkdir(mode=0o700)
        return destination

    def index_document(self, capability, ref, doc_id, *, limits=None):
        from session_cloud.runtime import SessionError
        limits = limits or IndexLimits()
        if not isinstance(limits, IndexLimits) or not isinstance(doc_id, str) or not re.fullmatch(r"[a-f0-9]{64}", doc_id):
            raise SessionError("document")
        with self._operation(capability, ref) as lease:
            destination, previous, committed = None, None, False
            try:
                with self._lock, self._db(lease) as db:
                    row = db.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()
                    if not row or row[0] not in {"prepared", "indexed"}:
                        raise SessionError("document")
                    count = self._user_indexing.get(lease.owner, 0)
                    if count >= 6 or self._indexing >= 20 or len(self._user_indexing) >= 100:
                        raise SessionError("limit")
                    self._indexing += 1
                    self._user_indexing[lease.owner] = count + 1
                    self._capacity(lease, limits.snapshot_bytes + 12 * 1024**2)
                    previous = self._snapshot(db, ref)
                    docs = [dict(r) for r in db.execute(
                        "SELECT id,filename FROM documents WHERE status='indexed' OR id=? ORDER BY rowid", (doc_id,))]
                    for document in docs:
                        document["pages"] = [json.loads(r[0]) for r in db.execute(
                            "SELECT payload FROM prepared_pages WHERE document_id=? ORDER BY number", (document["id"],))]
                destination = self._destination(ref)
                result = self._index_service.run({"action": "build", "documents": docs, "destination": str(destination)},
                    self._folder(ref), limits, self._index_active(capability, ref, lease))
                if (not re.fullmatch(r"[a-f0-9]{64}", result.get("checksum", ""))
                        or type(result.get("chunks")) is not int or not 0 < result["chunks"] <= limits.chunks
                        or hashlib.sha256((destination / "manifest.json").read_bytes()).hexdigest() != result["checksum"]):
                    raise IndexFailure()
                with self._lock:
                    self.check(capability, ref)
                    with self._db(lease) as db:
                        self._capacity(lease)
                        db.execute("INSERT OR REPLACE INTO active_index VALUES(1,?,?)", (destination.name, result["checksum"]))
                        db.execute("UPDATE documents SET status='indexed' WHERE id=?", (doc_id,))
                        db.execute("DELETE FROM analysis_results")
                    committed = True
                return {"status": "indexed", **result}
            except IndexFailure as error:
                raise SessionError(error.code) from None
            finally:
                if committed and previous:
                    shutil.rmtree(previous[0])
                if not committed and destination is not None:
                    shutil.rmtree(destination)

    def retrieve(self, capability, ref, query, selected, *, top_k=10, limits=None):
        from session_cloud.runtime import SessionError
        limits = limits or IndexLimits()
        if (not isinstance(limits, IndexLimits) or not isinstance(query, str) or not 1 <= len(query.strip()) <= 500
                or not isinstance(selected, list) or not 1 <= len(selected) <= 3
                or any(not isinstance(d, str) or not re.fullmatch(r"[a-f0-9]{64}", d) for d in selected)
                or len(set(selected)) != len(selected) or type(top_k) is not int or not 1 <= top_k <= 30):
            raise SessionError("document")
        with self._operation(capability, ref) as lease:
            try:
                with self._lock, self._db(lease) as db:
                    owned = {r[0] for r in db.execute("SELECT id FROM documents WHERE status='indexed'")}
                    if not set(selected) <= owned:
                        raise SessionError("document")
                    if self._queries >= 200 or self._user_queries.get(lease.owner, 0) >= 60:
                        raise SessionError("limit")
                    self._queries += 1
                    self._user_queries[lease.owner] = self._user_queries.get(lease.owner, 0) + 1
                    snapshot = self._snapshot(db, ref)
                    if snapshot is None:
                        raise SessionError("document")
                result = self._index_service.run({"action": "search", "source": str(snapshot[0]), "checksum": snapshot[1],
                    "query": query, "selected": selected, "top_k": top_k}, self._folder(ref), limits,
                    self._index_active(capability, ref, lease))
                self.check(capability, ref)
                if (not isinstance(result.get("hits"), list) or len(result["hits"]) > top_k
                        or any(h["doc_id"] not in selected for h in result["hits"])):
                    raise IndexFailure()
                return result["hits"]
            except IndexFailure as error:
                raise SessionError(error.code) from None

    def prepare_answer_context(self, capability, ref, query, selected, *, sub_questions=None, top_k=6,
                               on_stage=None, cancelled=None, include_summaries=False):
        """Verified 2C1 boundary; decomposition/provider/UI generation remain gated."""
        from session_cloud.runtime import SessionError
        sub_questions = [query] if sub_questions is None else sub_questions
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 500
                or not isinstance(selected, list) or not 1 <= len(selected) <= 3
                or any(not isinstance(d, str) or not re.fullmatch(r"[a-f0-9]{64}", d) for d in selected)
                or len(set(selected)) != len(selected) or type(top_k) is not int or not 1 <= top_k <= 20
                or type(include_summaries) is not bool
                or not isinstance(sub_questions, list) or not 1 <= len(sub_questions) <= 4
                or any(not isinstance(q, str) or not 1 <= len(q.strip()) <= 500 for q in sub_questions)):
            raise SessionError("document")
        with self._operation(capability, ref) as lease:
            try:
                with self._lock, self._db(lease) as db:
                    owned = {r[0] for r in db.execute("SELECT id FROM documents WHERE status='indexed'")}
                    if not set(selected) <= owned:
                        raise SessionError("document")
                    if self._queries >= 200 or self._user_queries.get(lease.owner, 0) >= 60:
                        raise SessionError("limit")
                    self._queries += 1
                    self._user_queries[lease.owner] = self._user_queries.get(lease.owner, 0) + 1
                    snapshot = self._snapshot(db, ref)
                    if snapshot is None:
                        raise SessionError("document")
                base_active = self._index_active(capability, ref, lease)

                def active():
                    if cancelled and cancelled():
                        raise SessionError("generation_interrupted")
                    base_active()

                request = {"action": "context", "source": str(snapshot[0]), "checksum": snapshot[1],
                    "query": query, "selected": selected, "sub_questions": sub_questions, "top_k": top_k}
                if include_summaries:
                    request["include_summaries"] = True
                # Preserve the original service call contract for plain previews.
                arguments = (request, self._folder(ref), IndexLimits(), active)
                result = (self._index_service.run(*arguments, on_stage=on_stage) if on_stage
                          else self._index_service.run(*arguments))
                self.check(capability, ref)
                if (not isinstance(result.get("chunks"), list) or not isinstance(result.get("aggregated_evidence"), str)
                        or any(c["doc_id"] not in selected for c in result["chunks"] + result["selected_chunks"])):
                    raise IndexFailure("context_invalid")
                if include_summaries and (not isinstance(result.get("document_summaries"), list)
                        or len(result["document_summaries"]) != len(selected)
                        or {c["doc_id"] for c in result["document_summaries"]} != set(selected)
                        or any(c.get("chunk_type") != "summary" for c in result["document_summaries"])):
                    raise IndexFailure("context_invalid")
                return result
            except IndexFailure as error:
                raise SessionError(error.code) from None

    def _delete_indexed(self, capability, ref, lease, db, doc_id):
        """One heavy operation; do not block logout behind native index work."""
        from session_cloud.runtime import SessionError
        with self._lock:
            previous = self._snapshot(db, ref)
        destination, committed = None, False
        try:
            if previous:
                limits = IndexLimits()
                with self._lock:
                    self.check(capability, ref)
                    self._capacity(lease, limits.snapshot_bytes + 128 * 1024)
                    destination = self._destination(ref)
                result = self._index_service.run({"action": "remove", "source": str(previous[0]), "checksum": previous[1],
                    "document": doc_id, "destination": str(destination)}, self._folder(ref), limits,
                    self._index_active(capability, ref, lease))
            with self._lock:
                self.check(capability, ref)
                if previous:
                    db.execute("DELETE FROM active_index")
                    if result["checksum"]:
                        db.execute("INSERT INTO active_index VALUES(1,?,?)", (destination.name, result["checksum"]))
                    else:
                        shutil.rmtree(destination)
                        destination = None
                db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                db.execute("DELETE FROM analysis_results")
                db.commit()
                committed = True
                (self._folder(ref) / (doc_id + ".pdf")).unlink(missing_ok=True)
        except IndexFailure as error:
            raise SessionError(error.code) from None
        finally:
            if committed and previous:
                shutil.rmtree(previous[0])
            if not committed and destination is not None:
                shutil.rmtree(destination)
