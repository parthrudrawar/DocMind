"""Authorized inline Q&A snapshots, separate from the active conversation."""

import hashlib
import json
from session_cloud.analysis_workspace import input_state, cached_result


class QuestionAnswers:
    @staticmethod
    def _clear_question_answers(db):
        for table in ("turns", "generation_requests"):
            db.execute(
                f"DELETE FROM {table} WHERE chat IN (SELECT id FROM chats WHERE id LIKE 'question-%')"
            )
        db.execute("DELETE FROM chats WHERE id LIKE 'question-%'")

    def _question_result(self, lease, db, topic):
        from session_cloud.runtime import SessionError

        state = input_state(self, lease, db, "questions", topic)
        if state is None:
            raise SessionError("analysis_stale")
        row = db.execute(
            "SELECT payload FROM analysis_results WHERE kind='questions' AND target=? AND fingerprint=?",
            (topic, state["fingerprint"]),
        ).fetchone()
        if row is None:
            raise SessionError("analysis_stale")
        return cached_result(row[0], state, "questions"), state

    @staticmethod
    def _question_chat(result, state, question_id):
        from session_cloud.runtime import SessionError

        item = next(
            (item for item in result["items"] if item["id"] == question_id), None
        )
        if item is None:
            raise SessionError("analysis_stale")
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    state["target"],
                    question_id,
                    item["question"],
                    result["document_ids"],
                    state["revision"],
                ]
            ).encode()
        ).hexdigest()
        return "question-" + fingerprint, item["question"]

    @staticmethod
    def _completed_question(db, chat, owned):
        from session_cloud.runtime import SessionError

        row = db.execute(
            "SELECT id,answer,metadata FROM turns WHERE chat=? ORDER BY rowid DESC LIMIT 1",
            (chat,),
        ).fetchone()
        if row:
            try:
                meta = json.loads(row["metadata"])
                citations = meta.get("citations", [])
                if (
                    not isinstance(meta, dict)
                    or set(meta.get("document_ids", [])) != set(owned)
                    or not isinstance(citations, list)
                    or any(
                        not isinstance(c, dict) or c.get("doc_id") not in owned
                        for c in citations
                    )
                ):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise SessionError("generation_invalid") from None
            if meta.get("completed"):
                return {
                    "role": "assistant",
                    "turn_id": row["id"],
                    "content": row["answer"],
                    "meta": meta,
                }
        return None

    def question_thread(self, capability, ref, topic, question_id, *, create=False):
        from session_cloud.runtime import SessionError

        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                result, state = self._question_result(lease, db, topic)
                chat, question = self._question_chat(result, state, question_id)
                exists = db.execute(
                    "SELECT 1 FROM chats WHERE id=?", (chat,)
                ).fetchone()
                if not exists and create:
                    if (
                        db.execute(
                            "SELECT count(*) FROM chats WHERE id LIKE 'question-%'"
                        ).fetchone()[0]
                        >= 10
                    ):
                        raise SessionError("inline_limit")
                    db.execute("INSERT INTO chats VALUES(?)", (chat,))
                    self._capacity(lease)
                return {
                    "chat": chat,
                    "question": question,
                    "documents": list(result["document_ids"]),
                    "exists": bool(exists or create),
                }

    def question_answers(self, capability, ref, topic):
        """One verified read per render, NOT a process/session-state answer cache."""
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                result, state = self._question_result(lease, db, topic)
                return {
                    item["id"]: self._completed_question(
                        db,
                        self._question_chat(result, state, item["id"])[0],
                        result["document_ids"],
                    )
                    for item in result["items"]
                }

    def question_answer(self, capability, ref, topic, question_id):
        from session_cloud.runtime import SessionError

        answers = self.question_answers(capability, ref, topic)
        if question_id not in answers:
            raise SessionError("analysis_stale")
        return answers[question_id]
