from html import escape

import streamlit as st
from session_cloud.chat_state import (
    activity_rows,
    current_activity_label,
    enqueue_turn,
    record_activity,
)
from authenticated_cloud.auth import CloudError
from session_cloud.runtime import SessionError
from frontend.citations import normalize_citations, render_answer_html, render_source_card_html


def _html(value) -> str:
    return escape(str(value), quote=True)


def render_question_card_html(index: int, question: str) -> str:
    """A full-width, wrapping card; model/document text is always escaped."""
    return (
        '<div class="smart-question-card">'
        f'<span class="smart-question-number">{_html(index)}.</span>'
        f'<div class="smart-question-text">{_html(question)}</div>'
        '</div>'
    )


def _render_processing(slot, label: str) -> None:
    slot.markdown(
        '<div class="processing-indicator" role="status" aria-live="polite">'
        '<span class="processing-dots" aria-hidden="true">'
        '<span></span><span></span><span></span></span>'
        f'<span>{_html(label)}</span></div>',
        unsafe_allow_html=True,
    )


def _activity_detail(row: dict) -> str:
    details = []
    if "retrieved_passages" in row:
        details.append(f'{row["retrieved_passages"]} passages retrieved')
    if "selected_passages" in row:
        details.append(f'{row["selected_passages"]} passages considered')
    if "citation_count" in row:
        details.append(f'{row["citation_count"]} citations returned')
    return " · ".join(details)


def _render_completed_message(index: int, message: dict) -> None:
    metadata = message.get("meta", {})
    citations = normalize_citations(metadata.get("citations", []))
    answer_html = render_answer_html(message.get("content", ""), citations)
    st.markdown(
        f'<div class="assistant-bubble">{answer_html}</div>',
        unsafe_allow_html=True,
    )

    evidence = metadata.get("evidence_strength") or {}
    if not evidence and citations:
        evidence = {
            "level": "moderate",
            "label": "Cited evidence available",
            "explanation": (
                "This older response includes source references; "
                "no calibrated probability is available."
            ),
        }
    if evidence:
        level = evidence.get("level", "limited")
        if level not in {"strong", "moderate", "weak", "limited", "unrated", "none"}:
            level = "limited"
        st.markdown(
            f'<span class="evidence-badge evidence-{level}" '
            f'title="{_html(evidence.get("explanation", ""))}">'
            f'Evidence: {_html(evidence.get("label", "available"))}</span>',
            unsafe_allow_html=True,
        )

    rows = activity_rows(metadata, len(citations))
    if rows:
        with st.expander("How this answer was prepared", expanded=False):
            for row in rows:
                icon = {"complete": "✓", "active": "…", "error": "!"}.get(
                    row.get("status"), "·"
                )
                detail = _activity_detail(row)
                detail_html = (
                    f'<span class="chat-activity-detail">{_html(detail)}</span>'
                    if detail
                    else ""
                )
                st.markdown(
                    '<div class="chat-activity-row">'
                    f'<span>{icon}</span><span><strong>{_html(row["label"])}</strong>'
                    f"{detail_html}</span></div>",
                    unsafe_allow_html=True,
                )

    if citations:
        with st.expander(f"Sources ({len(citations)}) · click to view", expanded=False):
            for citation in citations:
                st.markdown(render_source_card_html(citation), unsafe_allow_html=True)

    if metadata.get("stream_interrupted") or metadata.get("stream_error"):
        st.warning("This response was interrupted. The partial answer was kept.")
        retry_question = metadata.get("request_question")
        if retry_question and st.button("Retry", key=f"retry_{index}"):
            st.session_state["pending_input"] = retry_question
            st.rerun()

    follow_ups = metadata.get("follow_up_questions", [])
    if follow_ups:
        st.caption("Follow-up suggestions")
        columns = st.columns(len(follow_ups))
        for suggestion_index, (column, suggestion) in enumerate(
            zip(columns, follow_ups)
        ):
            with column:
                if st.button(
                    f"↗ {suggestion}", key=f"fu_{index}_{suggestion_index}"
                ):
                    st.session_state["pending_input"] = suggestion
                    st.rerun()

    st.markdown('<hr class="custom-divider">', unsafe_allow_html=True)


def _render_history() -> tuple[object | None, object | None]:
    pending = st.session_state.get("pending_turn")
    processing_slot = None
    streaming_slot = None

    # Native scrolling is the fallback; keyed CSS sizes this middle grid row
    # to the available screen space without moving the header or composer.
    viewport = st.container(height=460, border=False, key="documind_chat_viewport")
    with viewport:
        if not st.session_state["messages"] and not pending:
            st.markdown(
                """
                <div class="empty-state">
                    <div class="empty-icon">🧠</div>
                    <h3 style="color:#3D4A62;margin:12px 0 6px">DocuMind is ready</h3>
                    <p style="color:#2D3748;font-size:0.85rem">
                        Upload PDFs in the sidebar, then ask anything.<br>
                        Answers include exact page citations.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        for index, message in enumerate(st.session_state["messages"]):
            if message.get("role") == "user":
                st.markdown(
                    f'<div class="user-bubble">{_html(message.get("content", ""))}</div>',
                    unsafe_allow_html=True,
                )
            else:
                _render_completed_message(index, message)

        if pending:
            processing_slot = st.empty()
            streaming_slot = st.empty()
            _render_processing(processing_slot, current_activity_label(pending))

        failed = st.session_state.get("failed_turn")
        if failed:
            st.warning(SessionError.MESSAGES.get(failed.get("code"), SessionError.MESSAGES["generation_incomplete"]))
            if failed.get("draft"):
                with st.expander("Unverified interrupted draft (not saved)", expanded=False):
                    # Do not render unvalidated markers as clickable citations.
                    st.text(failed["draft"])
            if st.button("Retry request", key="retry_failed_turn", disabled=bool(pending)):
                st.session_state["pending_input"] = failed["question"]
                st.session_state.pop("failed_turn", None)
                st.rerun()

    return processing_slot, streaming_slot


def _run_pending_turn(api, processing_slot, streaming_slot) -> None:
    pending = st.session_state.get("pending_turn")
    if not pending:
        return
    if pending.get("status") == "running":
        api.cancel(pending["turn_id"])
        history = api.history()
        if not any(m.get("role") == "assistant" and m.get("turn_id") == pending["turn_id"]
                   and m.get("meta", {}).get("completed") for m in history):
            st.session_state.failed_turn = {"code": "generation_interrupted", "question": pending["question"]}
        st.session_state.pop("pending_turn", None)
        st.session_state.messages = history
        st.rerun()
    pending["status"] = "running"

    def on_stage(event):
        record_activity(st.session_state, event)
        _render_processing(
            processing_slot,
            current_activity_label(st.session_state.get("pending_turn")),
        )

    def on_token(text):
        rendered = render_answer_html(text, [], streaming=True)
        if rendered:
            streaming_slot.markdown(
                f'<div class="assistant-bubble">{rendered}'
                '<span class="stream-cursor" aria-hidden="true"></span></div>',
                unsafe_allow_html=True,
            )

    try:
        response = api.query_stream(
            pending["question"],
            st.session_state["project_id"],
            st.session_state["session_id"],
            turn_id=pending["turn_id"],
            document_ids=pending.get("document_ids", api.selected),
        )
        answer, metadata = api.parse_stream(
            response, on_token=on_token, on_stage=on_stage
        )
    except CloudError as error:
        if error.code in {"expired", "login"}:
            processing_slot.empty()
            streaming_slot.empty()
            raise
        answer, metadata = "", {"stream_error": error.code}
    except Exception:  # noqa: BLE001 - never surface provider details
        record_activity(
            st.session_state, {"stage": "connecting", "status": "error"}
        )
        answer, metadata = "", {"stream_error": "provider_unavailable"}

    if metadata.get("stream_interrupted"):
        pending_activity = st.session_state.get("pending_turn", {}).get("activity", [])
        stage = (
            pending_activity[-1].get("stage", "generating")
            if pending_activity
            else "generating"
        )
        record_activity(st.session_state, {"stage": stage, "status": "error"})
        metadata["activity"] = st.session_state.get("pending_turn", {}).get(
            "activity", []
        )

    processing_slot.empty()
    history = api.history()
    # Delivery can fail after a successful server transaction. Recover that
    # exact validated turn instead of offering a duplicate charged request.
    completed = next((m for m in history if m.get("role") == "assistant"
                      and m.get("turn_id") == pending["turn_id"] and m.get("meta", {}).get("completed")), None)
    if completed:
        metadata = completed["meta"]
    if not metadata.get("completed") or metadata.get("stream_error") or metadata.get("stream_interrupted"):
        st.session_state.failed_turn = {"question": pending["question"], "draft": answer,
                                       "code": metadata.get("stream_error", "generation_incomplete")}
    else:
        st.session_state.pop("failed_turn", None)
    # The SQLite turn transaction, not local UI flags, is the completion authority.
    st.session_state.pop("pending_turn", None)
    st.session_state.messages = history
    st.rerun()


def render_chat_tab(api, *, enabled=True) -> None:
    """Render stable history, composer, and pending output in that visual order."""
    prefill = st.session_state.pop("pending_input", "")
    pending = st.session_state.get("pending_turn")
    # Stay inside the Chat tab: a global fixed footer would also cover tools
    # such as Smart Questions. Each keyed native container gets its own row.
    with st.container(key="documind_chat_panel"):
        with st.container(key="documind_chat_header"):
            st.markdown(
                '<div class="chat-panel-header">'
                '<span class="chat-panel-title">Chat</span>'
                '<span class="chat-panel-subtitle">Your document conversation</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        processing_slot, streaming_slot = _render_history()
        with st.container(key="documind_chat_composer"):
            if pending and st.button("Cancel response", key="cancel_response"):
                api.cancel(pending["turn_id"])
                st.session_state.failed_turn = {"code": "generation_interrupted", "question": pending["question"]}
                st.session_state.pop("pending_turn", None)
                st.session_state.messages = api.history()
                st.rerun()
            user_input = st.chat_input(
                "Ask anything about your documents…",
                disabled=bool(pending) or not enabled,
                key="documind_chat_input",
            )
    question = user_input or prefill
    if enabled and question and not pending and enqueue_turn(st.session_state, question):
        st.session_state["pending_turn"]["document_ids"] = list(api.selected)
        st.rerun()

    if enabled and pending and processing_slot is not None and streaming_slot is not None:
        _run_pending_turn(api, processing_slot, streaming_slot)
