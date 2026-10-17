from copy import deepcopy
from typing import Any
from uuid import uuid4

STAGE_LABELS = {
    "connecting": "Starting request",
    "searching": "Searching documents",
    "reranking": "Reranking passages",
    "evidence": "Selecting evidence",
    "generating": "Generating answer",
    "citations": "Preparing citations",
}
_VALID_STATUSES = {"active", "complete", "error"}
_COUNT_FIELDS = {"retrieved_passages", "selected_passages", "citation_count"}


def enqueue_turn(state: Any, question: str, turn_id: str | None = None) -> bool:
    """Queue one user turn exactly once across Streamlit reruns."""
    question = str(question or "").strip()
    if not question or state.get("pending_turn"):
        return False

    turn_id = turn_id or str(uuid4())
    messages = state.setdefault("messages", [])
    if any(message.get("turn_id") == turn_id for message in messages):
        return False

    messages.append({"role": "user", "content": question, "turn_id": turn_id})
    state["pending_turn"] = {
        "turn_id": turn_id,
        "question": question,
        "activity": [
            {"stage": "connecting", "status": "active", "label": STAGE_LABELS["connecting"]}
        ],
    }
    return True


def _clean_stage_event(event: Any) -> dict | None:
    if not isinstance(event, dict):
        return None
    stage = str(event.get("stage") or "")
    if stage not in STAGE_LABELS:
        return None
    status = str(event.get("status") or "active")
    if status not in _VALID_STATUSES:
        status = "active"
    cleaned = {"stage": stage, "status": status, "label": STAGE_LABELS[stage]}
    for field in _COUNT_FIELDS:
        value = event.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            cleaned[field] = value
    return cleaned


def merge_activity_event(activity: list, event: Any) -> list[dict]:
    """Merge an allow-listed backend stage event without exposing internals."""
    cleaned = _clean_stage_event(event)
    current = [item for item in activity if isinstance(item, dict)]
    if cleaned is None:
        return current

    merged = []
    replaced = False
    for item in current:
        if item.get("stage") == cleaned["stage"]:
            merged.append(cleaned)
            replaced = True
        elif item.get("stage") != "connecting" or cleaned["stage"] == "connecting":
            merged.append(item)
    if not replaced:
        merged.append(cleaned)
    return merged


def record_activity(state: Any, event: Any) -> list[dict]:
    pending = state.get("pending_turn")
    if not isinstance(pending, dict):
        return []
    pending["activity"] = merge_activity_event(pending.get("activity", []), event)
    return pending["activity"]


def complete_turn(state: Any, answer: str, metadata: Any) -> bool:
    """Commit one assistant reply after a pending turn and clear the pending state."""
    pending = state.get("pending_turn")
    if not isinstance(pending, dict):
        return False

    turn_id = pending.get("turn_id")
    messages = state.setdefault("messages", [])
    if any(
        message.get("turn_id") == turn_id and message.get("role") == "assistant"
        for message in messages
    ):
        state.pop("pending_turn", None)
        return False

    clean_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    clean_metadata["activity"] = deepcopy(
        clean_metadata.get("activity") or pending.get("activity") or []
    )
    clean_metadata["request_question"] = pending.get("question", "")
    messages.append(
        {
            "role": "assistant",
            "content": str(answer or ""),
            "meta": clean_metadata,
            "turn_id": turn_id,
        }
    )
    state.pop("pending_turn", None)
    return True


def current_activity_label(pending: Any) -> str:
    if not isinstance(pending, dict):
        return STAGE_LABELS["connecting"]
    activity = pending.get("activity") or []
    active = [item for item in activity if item.get("status") == "active"]
    item = active[-1] if active else (activity[-1] if activity else {})
    return str(item.get("label") or STAGE_LABELS["connecting"])


def activity_rows(metadata: Any, citation_count: int = 0) -> list[dict]:
    """Return safe, factual rows for the completed-answer activity panel."""
    metadata = metadata if isinstance(metadata, dict) else {}
    rows = []
    for event in metadata.get("activity") or []:
        cleaned = _clean_stage_event(event)
        if cleaned is not None:
            rows.append(cleaned)

    if not rows and citation_count:
        rows.append(
            {
                "stage": "citations",
                "status": "complete",
                "label": STAGE_LABELS["citations"],
                "citation_count": citation_count,
            }
        )
    return rows
