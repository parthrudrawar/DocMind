"""Original guide/brief analysis using the existing bounded generation lifecycle."""
from datetime import datetime, timezone
import json
import re
from types import SimpleNamespace

from authenticated_cloud.auth import CloudError, digest
from app.reasoning.citations import citation_numbers
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY
from session_cloud.analysis_workspace import input_state, cached_result
from session_cloud.generation import GenerationService
from session_cloud.original_guides import (collection_brief_prompt, collection_parts,
                                           parse_source_guide, source_guide_prompt)
from session_cloud.runtime import SessionError

GUIDE_QUERY = "What does this document cover and what key topics or concepts are discussed?"
BRIEF_QUERY = "What are the collection's overall subject, key themes or findings, and questions it can help answer?"


def source_input(chunk):
    """Actual L1 input excerpt, NOT a claim-level citation manufactured by a model."""
    text = chunk["text"][:800]
    return {"number": 1, "doc_id": chunk["doc_id"], "chunk_id": chunk["chunk_id"],
            "title": chunk["title"], "filename": chunk["filename"], "page_number": chunk["page_number"],
            "page_end": chunk["page_end"], "section": chunk["section"], "chunk_type": "summary",
            "exact_quote": text, "text_snippet": text[:250], "provenance_only": True}


class AnalysisService(GenerationService):
    """Reuse cancellation, bounded queue/thread, replay and failure infrastructure.

    Crucially, provider reservations, concurrency gate and running registry are
    the SAME objects as chat's, never a separate analysis allowance.
    """
    def __init__(self, store, generation):
        self.store, self.generation = store, generation
        self.limits, self.monotonic = generation.limits, generation.monotonic
        self.gate, self.lock, self.running = generation.gate, generation.lock, generation.running

    @property
    def budget(self):
        return self.generation.budget

    @property
    def provider(self):
        return self.generation.provider

    def request(self, capability, ref, kind, target, request_id):
        return self.stream(capability, ref, kind, request_id, target, [])

    def _claim(self, capability, ref, kind, request_id, target, selected):
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", request_id):
            raise SessionError("document")
        with self.store._lock:
            lease = self.store.check(capability, ref)
            with self.store._db(lease) as db:
                state = input_state(self.store, lease, db, kind, target)
                if state is None and kind in {"contradictions", "gaps", "questions", "insights", "timeline"}:
                    raise SessionError("analysis_evidence")
                if state is None or kind == "brief" and not state["guides"]:
                    raise SessionError("analysis_input")
                row = db.execute("SELECT kind,fingerprint,status,payload FROM analysis_requests WHERE id=?",
                                 (request_id,)).fetchone()
                if row:
                    if row[0] != kind or row[1] != state["fingerprint"]:
                        raise SessionError("analysis_stale")
                    if row[2] != "complete":
                        raise SessionError("generation_interrupted")
                    return lease, ("", {"completed": True, "analysis_result": cached_result(row[3], state, kind)}), None
                if db.execute("SELECT count(*) FROM analysis_requests").fetchone()[0] >= 60:
                    raise SessionError("generation_budget")
                db.execute("INSERT INTO analysis_requests VALUES(?,?,?,'running',NULL)",
                           (request_id, kind, state["fingerprint"]))
                return lease, None, state

    def _failed(self, lease, kind, request_id):
        with self.store._lock:
            if lease.ref not in self.store._known:
                return
            try:
                with self.store._db(lease) as db:
                    db.execute("UPDATE analysis_requests SET status='failed' WHERE id=? AND kind=? AND status='running'",
                               (request_id, kind))
            except CloudError:
                pass

    def _execute(self, capability, ref, kind, request_id, target, selected, lease, state, cancel, publish, started):
        last_auth = [float('-inf')]

        def active(force=False):
            if cancel.is_set() or self.monotonic() - started > self.limits.seconds:
                raise SessionError("generation_interrupted")
            with self.store._lock:
                if (self.store._leases.get(digest(capability)) is not lease or lease.expires <= self.store.clock()):
                    raise SessionError("expired")
            if force or self.monotonic() - last_auth[0] >= 1:
                self.store.check(capability, ref)
                last_auth[0] = self.monotonic()

        def verify_inputs(db):
            current = input_state(self.store, lease, db, kind, target)
            if current is None or current["fingerprint"] != state["fingerprint"]:
                raise SessionError("analysis_stale")

        active(True)
        if kind in {"contradictions", "gaps", "questions", "insights", "timeline"}:
            from session_cloud.comparison_service import execute_collection
            return execute_collection(self, capability, ref, kind, request_id, lease, state,
                                      cancel, publish, started, active, verify_inputs)
        documents = [d["id"] for d in state["documents"]]

        def progress(event):
            active(True)
            publish({"type": "stage", "stage": event["stage"], "status": event["status"], **event.get("counts", {})})
        # Keep ORIGINAL analysis inputs, not a generic summary of these hits.
        # The shared hybrid/reranker pipeline validates the owned snapshot and
        # supplies observable related evidence + the actual owned L1 chunks.
        context = self.store.prepare_answer_context(capability, ref,
            GUIDE_QUERY if kind == "guide" else BRIEF_QUERY, documents, include_summaries=True,
            cancelled=cancel.is_set, on_stage=progress)
        summaries = context.get("document_summaries", [])
        if (len(summaries) != len(documents) or {c["doc_id"] for c in summaries} != set(documents)
                or any(c.get("chunk_type") != "summary" or not isinstance(c.get("text"), str) for c in summaries)
                or any(c["doc_id"] not in documents for c in context["chunks"] + context["selected_chunks"])):
            raise SessionError("context_invalid")
        with self.store._operation(capability, ref):
            active(True)
            with self.store._lock, self.store._db(lease) as db:
                verify_inputs(db)
            if kind == "guide":
                summary = summaries[0]
                if len(summary["text"].split()) < 20:
                    raise SessionError("analysis_input")
                prompt, maximum, temperature = source_guide_prompt(summary["text"]), 512, 0
            else:
                docs = [SimpleNamespace(doc_id=d["id"], filename=d["filename"]) for d in state["documents"]]
                guides = {key: SimpleNamespace(summary=g["summary"], key_topics=json.dumps(g["key_topics"]))
                          for key, g in state["guides"].items()}
                prompt, maximum, temperature = collection_brief_prompt(collection_parts(docs, guides)), 300, 0.3
            messages = [{"role": "system", "content": UNTRUSTED_DOCUMENT_POLICY}, {"role": "user", "content": prompt}]
            reserved = self.budget.reserve(lease.owner, messages, maximum)
            active(True)
            publish({"type": "stage", "stage": "generating", "status": "active"})
            pieces, size, usage, completed = [], 0, None, False
            stream = self.provider.events(messages, maximum, active, temperature=temperature,
                seconds=min(self.limits.provider_seconds, max(1, self.limits.seconds - (self.monotonic() - started))))
            try:
                for event in stream:
                    active()
                    if event["type"] == "token":
                        piece = event["content"]
                        if not isinstance(piece, str):
                            raise SessionError("analysis_invalid")
                        size += len(piece)
                        if size > 4000:
                            raise SessionError("analysis_invalid")
                        pieces.append(piece)
                    elif event["type"] == "usage":
                        usage, completed = event["usage"], True
            finally:
                stream.close()
            active(True)
            if not completed or not pieces:
                raise SessionError("generation_incomplete")
            if usage and (usage["total_tokens"] > reserved or usage["completion_tokens"] > maximum):
                raise SessionError("analysis_invalid")
            text = "".join(pieces).strip()
            # Neither original prompt assigns citation numbers. Do not present
            # model-invented [n] references as supported PDF citations.
            if citation_numbers(text):
                raise SessionError("analysis_invalid")
            result = parse_source_guide(text) if kind == "guide" else {"brief": text}
            if kind == "brief" and not 1 <= len(text) <= 2400:
                raise SessionError("analysis_invalid")
            inputs = ([source_input(summaries[0])] if kind == "guide" else
                      [s for d in documents if d in state["guides"] for s in state["guides"][d]["input_sources"]])
            inputs = [dict(s, number=i) for i, s in enumerate(inputs, 1)]
            result.update(request_id=request_id, kind=kind, document_ids=documents,
                created_at=datetime.fromtimestamp(self.store.clock(), timezone.utc).isoformat(),
                input_sources=inputs, provenance_only=True,
                metrics={"wall_seconds": self.monotonic() - started, "provider_calls": 1,
                         "reserved_tokens_upper_bound": reserved, "provider_usage": usage,
                         "evidence": context["metrics"], "related_passages": len(context["selected_chunks"])})
            if kind == "guide":
                result["doc_id"] = target
            else:
                result["missing_guide_ids"] = [d for d in documents if d not in state["guides"]]
            publish({"type": "stage", "stage": "generating", "status": "complete"})
            with self.store._lock:
                active(True)
                with self.store._db(lease) as db:
                    verify_inputs(db)
                    if cancel.is_set():
                        raise SessionError("generation_interrupted")
                    payload = json.dumps(result)
                    if len(payload.encode()) > 24_000:
                        raise SessionError("analysis_invalid")
                    if kind == "guide":
                        # A regenerated guide changes the original brief inputs,
                        # even when the underlying index did not change.
                        db.execute("DELETE FROM analysis_results WHERE kind='brief'")
                    if kind == 'questions':
                        self.store._clear_question_answers(db)
                    db.execute("INSERT OR REPLACE INTO analysis_results VALUES(?,?,?,?,?)",
                               (kind, target, state["revision"], state["fingerprint"], payload))
                    db.execute("UPDATE analysis_requests SET status='complete',payload=? WHERE id=? AND status='running'",
                               (payload, request_id))
                    self.store._capacity(lease)
            publish({"type": "metadata", "completed": True, "analysis_result": result})
            publish({"type": "done"})
