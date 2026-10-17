"""Workspace-scoped result reads; no provider call is allowed on a cache read."""
import hashlib
import json
import re
from datetime import datetime, timezone


def cached_result(payload, state, kind):
    from session_cloud.runtime import SessionError
    try:
        result = json.loads(payload)
        owned = {d["id"] for d in state["documents"]}
        if (not isinstance(result, dict) or result.get("kind") != kind
                or set(result["document_ids"]) != owned or not isinstance(result["input_sources"], list)
                or any(s["doc_id"] not in owned for s in result["input_sources"])):
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, RecursionError):
        raise SessionError("analysis_invalid") from None


def input_state(store, lease, db, kind, target):
    from session_cloud.runtime import SessionError
    if kind not in {"guide", "brief", "contradictions", "gaps", "questions", "insights", "timeline"} or not isinstance(target, str):
        raise SessionError("document")
    docs = [dict(r) for r in db.execute("SELECT id,filename FROM documents WHERE status='indexed' ORDER BY rowid")]
    if kind == "guide":
        if not re.fullmatch(r"[a-f0-9]{64}", target):
            raise SessionError("document")
        docs = [d for d in docs if d["id"] == target]
        if not docs:
            raise SessionError("document")
    elif kind in {"questions", "insights"}:
        if len(target) > 500 or re.search(r"[\x00-\x1f\x7f]", target):
            raise SessionError("document")
    elif target != "collection":
        raise SessionError("document")
    if not docs:
        return None
    if kind == "timeline":
        dates = dict(db.execute("SELECT id,upload_date FROM documents WHERE status='indexed'"))
        if any(d["id"] not in dates for d in docs):
            raise SessionError("analysis_evidence")
        try:
            if any(datetime.fromisoformat(dates[d["id"]]).utcoffset() != timezone.utc.utcoffset(None) for d in docs):
                raise ValueError
        except (ValueError, TypeError):
            raise SessionError("analysis_evidence") from None
        docs.sort(key=lambda d: dates[d["id"]])
        docs = [dict(d, upload_date=dates[d["id"]][:10]) for d in docs]
    snapshot = store._snapshot(db, lease.ref)
    if snapshot is None:
        raise SessionError("document")
    revision = snapshot[0].name + ":" + snapshot[1]
    guides = {}
    if kind == "brief":
        for d in docs:
            row = db.execute("SELECT payload FROM analysis_results WHERE kind='guide' AND target=? AND revision=?",
                             (d["id"], revision)).fetchone()
            if row:
                guides[d["id"]] = json.loads(row[0])
    fingerprint = hashlib.sha256(json.dumps([kind, target, revision, docs, guides], sort_keys=True).encode()).hexdigest()
    return {"documents": docs, "revision": revision, "fingerprint": fingerprint, "guides": guides, "target": target}


class AnalysisWorkspace:
    def _analysis_result(self, capability, ref, kind, target):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                state = input_state(self, lease, db, kind, target)
                if state is None:
                    return None
                row = db.execute("SELECT payload FROM analysis_results WHERE kind=? AND target=? AND fingerprint=?",
                                 (kind, target, state["fingerprint"])).fetchone()
                return cached_result(row[0], state, kind) if row else None

    def get_source_guide(self, capability, ref, doc_id):
        return self._analysis_result(capability, ref, "guide", doc_id)

    def get_collection_brief(self, capability, ref):
        # Original brief covers ALL indexed workspace documents, not chat selection.
        return self._analysis_result(capability, ref, "brief", "collection")

    def get_contradictions(self, capability, ref):
        return self._analysis_result(capability, ref, "contradictions", "collection")

    def get_gaps(self, capability, ref):
        return self._analysis_result(capability, ref, "gaps", "collection")

    def get_questions(self, capability, ref, topic=""):
        return self._analysis_result(capability, ref, "questions", topic)

    def get_insights(self, capability, ref, topic=""):
        return self._analysis_result(capability, ref, "insights", topic)

    def get_timeline(self, capability, ref):
        return self._analysis_result(capability, ref, "timeline", "collection")

    def question_for_ask(self, capability, ref, topic, question_id):
        from session_cloud.runtime import SessionError
        result = self.get_questions(capability, ref, topic)
        if result:
            for item in result["items"]:
                if item["id"] == question_id:
                    return item["question"], list(result["document_ids"])
        raise SessionError("analysis_stale")

    def _comparison_inputs(self, capability, ref, lease, documents, kind, active):
        """Called only inside the analysis heavy-operation gate; no browser paths."""
        from session_cloud.index_contract import IndexFailure, IndexLimits
        from session_cloud.runtime import SessionError
        if kind == "contradictions" and len(documents) < 2:
            raise SessionError("analysis_evidence")
        try:
            with self._lock, self._db(lease) as db:
                snapshot = self._snapshot(db, ref)
                if snapshot is None:
                    raise SessionError("analysis_evidence")
            result = self._index_service.run({"action": "analysis_inputs", "source": str(snapshot[0]),
                "checksum": snapshot[1], "documents": documents, "kind": kind}, self._folder(ref),
                IndexLimits(), active)
            self.check(capability, ref)
            expected = {d["id"]: d["filename"] for d in documents}
            rows = result.get("documents")
            if (not isinstance(rows, list) or len(rows) > len(documents)
                    or len({d["id"] for d in rows}) != len(rows)
                    or any(d["id"] not in expected or d["filename"] != expected[d["id"]]
                           or not d.get("sources") or not isinstance(d.get("text"), str)
                           or len(d["text"]) > (2004 if kind == "insights" else 1520)
                           or kind == "timeline" and d.get("upload_date") != next(v["upload_date"] for v in documents if v["id"] == d["id"])
                           or any(s["doc_id"] != d["id"] or s["filename"] != d["filename"]
                                  or not isinstance(s.get("exact_quote"), str)
                                  or s["exact_quote"] not in d["text"] for s in d["sources"]) for d in rows)):
                raise SessionError("context_invalid")
            return result
        except IndexFailure as error:
            raise SessionError(error.code) from None
