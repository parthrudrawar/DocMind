"""Original frontend callbacks backed by server-owned streaming/turn storage."""
import time
from authenticated_cloud.auth import digest
from authenticated_cloud.auth import CloudError
from session_cloud.runtime import SessionError


def bind_chat_state(state, workspace, chat, messages):
    """Clear pending and rendered content whenever the server-owned scope changes."""
    if state.get("session_scope") != (workspace, chat):
        for key in ("pending_turn", "pending_input", "messages", "collection_brief", "failed_turn"):
            state.pop(key, None)
        state.update(session_scope=(workspace, chat), project_id=workspace, session_id=chat, messages=messages)
    elif not state.get("pending_turn"):
        state["messages"] = messages


class LocalAPI:
    def __init__(self, store, capability, workspace, chat, selected=None):
        self.store, self._capability, self._workspace, self._chat = store, capability, workspace, chat
        self.selected, self._last_auth = list(selected or []), float('-inf')

    def query_stream(self, question, project_id, session_id, *, turn_id=None, document_ids=None):
        self.store.check(self._capability, self._workspace)
        if (project_id, session_id) != (self._workspace, self._chat):
            raise SessionError("document")
        if self.store.generation is None:
            raise SessionError("phase")
        return self.store.generation.stream(self._capability, self._workspace, self._chat,
                                            turn_id, question, self.selected if document_ids is None else document_ids)

    def cancel(self, turn_id):
        if self.store.generation:
            self.store.generation.cancel(self._capability, self._workspace, self._chat, turn_id)

    def history(self):
        return self.store.history(self._capability, self._workspace, self._chat)

    def _active(self, force=False):
        # Immediate local logout/TTL check; bounded Supabase round trips during
        # progressive output instead of one network validation per token.
        if self.store.generation is None:
            return self.store.check(self._capability, self._workspace)
        with self.store._lock:
            lease = self.store._leases.get(digest(self._capability))
            if lease is None or lease.ref != self._workspace or lease.expires <= self.store.clock():
                raise SessionError("expired")
        if force or time.monotonic() - self._last_auth >= 1:
            self.store.check(self._capability, self._workspace)
            self._last_auth = time.monotonic()

    def parse_stream(self, events, on_token=None, on_stage=None):
        """Future local engine events retain the original renderer callback contract."""
        text, metadata, activity, done = "", {}, [], False
        try:
            for event in events:
                self._active()
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "token":
                    piece = event.get("content")
                    if not isinstance(piece, str) or len(text) + len(piece) > 8000:
                        raise SessionError("limit")
                    text += piece
                    if on_token:
                        on_token(text)
                elif event.get("type") == "stage":
                    activity.append(event)
                    if on_stage:
                        on_stage(event)
                elif event.get("type") == "metadata":
                    metadata = dict(event)
                elif event.get("type") == "done":
                    done = True
                    break
        except CloudError as error:
            # Never reveal buffered content after auth or ownership fails.
            if error.code in {"expired", "login"}:
                raise
            metadata = {"stream_error": error.code}
        except Exception:
            metadata = {"stream_error": "interrupted"}
        finally:
            close = getattr(events, "close", None)
            if close:
                close()
        self._active(True)
        metadata["activity"] = metadata.get("activity") or activity
        if not done or self.store.generation is not None and not metadata.get("completed"):
            metadata["stream_interrupted"] = True
        return text, metadata
