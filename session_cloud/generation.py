"""One cancellable conversational request, scoped to a verified temporary workspace."""
import hashlib
import json
import queue
import re
import threading
import time

from authenticated_cloud.auth import CloudError, digest
from app.reasoning.citations import citation_numbers
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY
from session_cloud.attribution import AttributionError, format_attribution
from session_cloud.chat_state import merge_activity_event
from session_cloud.generation_budget import GenerationLimits, ProviderBudget, prompt_token_bound
from session_cloud.context_packing import pack_passages
from session_cloud.index_contract import IndexFailure
from session_cloud.groq_stream import string_array
from session_cloud.original_context import RetrievedChunk
from session_cloud.original_prompts import (_messages_for_state, _needs_decomposition,
    answer_confidence, decomposition_prompt, follow_up_prompt, quote_prompt)
from session_cloud.runtime import SessionError


class GenerationService:
    def __init__(self, store, provider, *, limits=GenerationLimits(), monotonic=time.monotonic):
        self.store, self.provider, self.limits, self.monotonic = store, provider, limits, monotonic
        self.budget = ProviderBudget(limits, clock=store.clock)
        self.gate, self.lock, self.running = threading.Lock(), threading.Lock(), {}

    def reserve(self, capability, owner, messages, maximum):
        if self.store.daily_quota is None:
            raise SessionError("quota_unavailable")
        return self.budget.reserve(owner, messages, maximum,
            charge=lambda: self.store.daily_quota.reserve(capability, owner))

    def cancel(self, capability, ref, chat, turn_id):
        self.store.check(capability, ref)
        with self.lock:
            entry = self.running.get((ref, chat, turn_id))
            if entry:
                entry.set()

    def _claim(self, capability, ref, chat, turn_id, question, selected):
        if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 500
                or not isinstance(turn_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", turn_id)
                or not isinstance(selected, list) or not 1 <= len(selected) <= 3
                or any(not isinstance(d, str) or not re.fullmatch(r"[a-f0-9]{64}", d) for d in selected)
                or len(set(selected)) != len(selected)):
            raise SessionError("document")
        fingerprint = hashlib.sha256(json.dumps([question, sorted(selected)]).encode()).hexdigest()
        with self.store._lock:
            lease = self.store.check(capability, ref)
            with self.store._db(lease) as db:
                if not db.execute("SELECT 1 FROM chats WHERE id=?", (chat,)).fetchone():
                    raise SessionError("document")
                owned = {r[0] for r in db.execute("SELECT id FROM documents WHERE status='indexed'")}
                if not set(selected) <= owned:
                    raise SessionError("document")
                existing = db.execute("SELECT fingerprint,status FROM generation_requests WHERE chat=? AND id=?",
                                      (chat, turn_id)).fetchone()
                if existing:
                    if existing[0] != fingerprint or existing[1] != "complete":
                        raise SessionError("generation_interrupted")
                    row = db.execute("SELECT answer,metadata FROM turns WHERE chat=? AND id=?", (chat, turn_id)).fetchone()
                    if row is None:
                        raise SessionError("generation_invalid")
                    return lease, (row[0], json.loads(row[1])), None
                if (db.execute("SELECT count(*) FROM turns WHERE chat=?", (chat,)).fetchone()[0] >= self.store.limits.turns
                        or db.execute("SELECT count(*) FROM generation_requests WHERE chat=?", (chat,)).fetchone()[0] >= 60):
                    raise SessionError("history")
                snapshot = self.store._snapshot(db, ref)
                if snapshot is None:
                    raise SessionError("document")
                db.execute("INSERT INTO generation_requests VALUES(?,?,?,'running')", (chat, turn_id, fingerprint))
            return lease, None, snapshot[1]

    def stream(self, capability, ref, chat, turn_id, question, selected):
        """Consumer close cancels producer; a rerun never automatically resends it."""
        lease, replay, snapshot = self._claim(capability, ref, chat, turn_id, question, selected)
        if replay:
            yield {"type": "token", "content": replay[0]}
            yield {"type": "metadata", **replay[1]}
            yield {"type": "done"}
            return
        if not self.gate.acquire(blocking=False):
            self._failed(lease, chat, turn_id)
            raise SessionError("busy")
        cancel, channel, ended = threading.Event(), queue.Queue(maxsize=16), threading.Event()
        key, started = (ref, chat, turn_id), self.monotonic()
        with self.lock:
            self.running[key] = cancel
        # No private prompt/query/result is retained by the service after this
        # bounded producer exits. A slow/abandoned consumer cannot grow buffers.
        def publish(event):
            while not cancel.is_set():
                try:
                    channel.put(event, timeout=.05)
                    return
                except queue.Full:
                    if self.monotonic() - started > self.limits.seconds:
                        break
            raise SessionError("generation_interrupted")

        def produce():
            try:
                self._execute(capability, ref, chat, turn_id, question, list(selected), lease,
                              snapshot, cancel, publish, started)
            except BaseException as error:
                self._failed(lease, chat, turn_id)
                # Only fixed, public-safe codes cross this thread boundary.
                code = error.code if isinstance(error, CloudError) else "generation_invalid"
                try:
                    publish({"type": "failure", "code": code})
                except SessionError:
                    pass
            finally:
                with self.lock:
                    self.running.pop(key, None)
                self.gate.release()
                ended.set()

        thread = threading.Thread(target=produce, name="documind-generation", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            with self.lock:
                self.running.pop(key, None)
            self.gate.release()
            self._failed(lease, chat, turn_id)
            raise SessionError("busy") from None
        try:
            while True:
                if self.monotonic() - started > self.limits.seconds + 1:
                    raise SessionError("provider_timeout")
                try:
                    event = channel.get(timeout=.1)
                except queue.Empty:
                    if ended.is_set():
                        raise SessionError("generation_incomplete")
                    continue
                if event["type"] == "failure":
                    raise SessionError(event["code"])
                yield event
                if event["type"] == "done":
                    return
        finally:
            cancel.set()
            # The gate remains held until the producer exits; no unbounded
            # replacement threads. Socket read timeout bounds blocked cancellation.
            thread.join(timeout=.2)

    def _failed(self, lease, chat, turn):
        with self.store._lock:
            if lease.ref not in self.store._known:
                return
            try:
                with self.store._db(lease) as db:
                    db.execute("UPDATE generation_requests SET status='failed' WHERE chat=? AND id=? AND status='running'",
                               (chat, turn))
            except CloudError:
                pass

    def _execute(self, capability, ref, chat, turn_id, question, selected, lease, snapshot, cancel, publish, started):
        last_auth, activity, calls, reservations, usages = [float('-inf')], [], [], [], []
        first_token = None

        def active(force=False):
            if cancel.is_set() or self.monotonic() - started > self.limits.seconds:
                raise SessionError("generation_interrupted")
            # Local revocation/TTL on EVERY chunk; Supabase revalidation at least
            # once/second while progressing, at each phase and before commit.
            with self.store._lock:
                if (self.store._leases.get(digest(capability)) is not lease
                        or lease.expires <= self.store.clock()):
                    raise SessionError("expired")
            if force or self.monotonic() - last_auth[0] >= 1:
                self.store.check(capability, ref)
                last_auth[0] = self.monotonic()

        def stage(name, status, **counts):
            active(True)
            event = {"type": "stage", "stage": name, "status": status, **counts}
            activity[:] = merge_activity_event(activity, event)
            publish(event)

        def call(messages, maximum, purpose, *, incremental=False):
            nonlocal first_token
            active(True)
            if len(calls) >= self.limits.calls_per_turn:
                raise SessionError("generation_budget")
            reserved = self.reserve(capability, lease.owner, messages, maximum)
            calls.append(purpose)
            reservations.append(reserved)
            pieces, usage, completed = [], None, False
            events = self.provider.events(messages, maximum, active,
                seconds=min(self.limits.provider_seconds, max(1, self.limits.seconds - (self.monotonic() - started))))
            try:
                size = 0
                for event in events:
                    active()
                    if event["type"] == "token":
                        piece = event["content"]
                        if not isinstance(piece, str):
                            raise SessionError("generation_invalid")
                        size += len(piece)
                        if size > (self.limits.answer_characters if incremental else 4000):
                            raise SessionError("generation_invalid")
                        pieces.append(piece)
                        if incremental:
                            if first_token is None:
                                first_token = self.monotonic() - started
                            publish({"type": "token", "content": piece})
                    elif event["type"] == "usage":
                        usage = event["usage"]
                        completed = True
                active(True)
                if not pieces or not completed:
                    raise SessionError("generation_incomplete")
                if usage and (usage["total_tokens"] > reserved or usage["completion_tokens"] > maximum):
                    raise SessionError("generation_invalid")
                usages.append({"purpose": purpose, "reported": usage})
                return "".join(pieces)
            finally:
                events.close()

        def auxiliary(prompt, purpose):
            return call([{"role": "system", "content": UNTRUSTED_DOCUMENT_POLICY},
                         {"role": "user", "content": prompt}], self.limits.auxiliary_tokens, purpose)

        sub_questions = [question]
        stage("searching", "active")
        if _needs_decomposition(question):
            # Preserve original malformed-JSON fallback, but never bypass a
            # provider error/budget by silently making another request.
            text = auxiliary(decomposition_prompt(question), "decomposition")
            try:
                sub_questions = string_array(text, 4)
            except SessionError:
                sub_questions = [question]
        active(True)
        context = self.store.prepare_answer_context(capability, ref, question, selected,
            sub_questions=sub_questions, on_stage=lambda e: stage(e["stage"], e["status"], **e.get("counts", {})),
            cancelled=cancel.is_set)
        chunks = [RetrievedChunk(**c) for c in context["chunks"]]
        if not chunks or len(chunks) > 80 or any(c.doc_id not in selected for c in chunks):
            raise SessionError("context_invalid")
        # Hold the same heavy-operation gate after retrieval: indexing/deletion
        # cannot mutate the selected evidence while the provider is answering.
        with self.store._operation(capability, ref):
            active(True)
            with self.store._lock, self.store._db(lease) as db:
                current = self.store._snapshot(db, ref)
                if current is None or current[1] != snapshot:
                    raise SessionError("context_invalid")
            history = self.store.history(capability, ref, chat)
            # Only completed turns with this exact document selection provide
            # conversation context. Changing documents cannot reintroduce old sources.
            conversation = []
            for i in range(0, len(history), 2):
                if set(history[i + 1].get("meta", {}).get("document_ids", [])) == set(selected):
                    conversation.extend({"role": m["role"], "content": m["content"]} for m in history[i:i + 2])
            state = {"user_query": question, "conversation_history": conversation,
                     "aggregated_evidence": context["aggregated_evidence"], "retrieved_chunks": chunks}
            if prompt_token_bound(_messages_for_state(state)) > self.limits.input_bound:
                available = self.limits.input_bound - prompt_token_bound(
                    _messages_for_state({**state, 'aggregated_evidence': ''})) - 256
                try:
                    chunks, packed, metrics = pack_passages(chunks, chunks, count_tokens=lambda _: 0,
                                                           byte_limit=max(0, available))
                except IndexFailure:
                    raise SessionError('generation_context') from None
                state.update(aggregated_evidence=packed['aggregated_evidence'], retrieved_chunks=chunks)
                context['metrics']['prompt_packing'] = metrics
            stage("generating", "active")
            answer = call(_messages_for_state(state), self.limits.answer_tokens, "answer", incremental=True)
            stage("generating", "complete")
            stage("citations", "active")
            if not citation_numbers(answer) and answer.strip() != "The provided evidence does not answer this question.":
                raise SessionError("citation_invalid")
            fallbacks = [0]

            def quote_fallback(passage, claim):
                fallbacks[0] += 1
                if fallbacks[0] > 3:
                    raise SessionError("generation_budget")
                return auxiliary(quote_prompt(passage, claim), "quote").strip()

            try:
                metadata = format_attribution(answer, chunks, fallback=quote_fallback)
            except AttributionError:
                raise SessionError("citation_invalid") from None
            if any(c["doc_id"] not in selected for c in metadata["citations"]):
                raise SessionError("citation_invalid")
            stage("citations", "complete", citation_count=len(metadata["citations"]))
            follow_ups, follow_up_status = [], "unavailable"
            try:
                follow_ups = string_array(auxiliary(follow_up_prompt(question, answer), "follow_up"), 3, length=160)
                follow_up_status = "complete"
            except CloudError as error:
                if error.code in {"expired", "login", "generation_interrupted"}:
                    raise
                follow_up_status = "unavailable"  # optional; no fake suggestions or retries
            active(True)
            metadata.update(completed=True, document_ids=selected, activity=activity,
                confidence=answer_confidence(state), follow_up_questions=follow_ups, follow_up_status=follow_up_status,
                metrics={"wall_seconds": self.monotonic() - started, "first_token_seconds": first_token,
                         "reserved_tokens_upper_bound": sum(reservations), "provider_calls": len(calls),
                         "provider_usage": usages, "evidence": context["metrics"]})
            with self.store._lock:
                active(True)
                # Both rows commit together; only this server validation path
                # labels a request complete. No browser-provided completion flag.
                with self.store._db(lease) as db:
                    if cancel.is_set():
                        raise SessionError("generation_interrupted")
                    payload = json.dumps(metadata)
                    if len(payload.encode()) > 20_000:
                        raise SessionError("history")
                    db.execute("INSERT INTO turns VALUES(?,?,?,?,?)", (chat, turn_id, question, answer, payload))
                    db.execute("UPDATE generation_requests SET status='complete' WHERE chat=? AND id=? AND status='running'",
                               (chat, turn_id))
                    self.store._capacity(lease)
            publish({"type": "metadata", **metadata})
            publish({"type": "done"})
