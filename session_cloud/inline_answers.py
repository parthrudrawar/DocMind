"""Smart Question streaming in its own card, never the active Chat conversation."""

from uuid import uuid4

import streamlit as st

from authenticated_cloud.auth import CloudError
from frontend.citations import render_answer_html
from session_cloud.adapter import LocalAPI
from session_cloud.chat_state import STAGE_LABELS
from session_cloud.chat_view import _render_completed_message
from session_cloud.runtime import SessionError


def reconcile_inline_scope(store, capability, ref, state):
    pending = state.get("pending_inline")
    if not pending:
        return
    scope = tuple(pending.get("scope", ()))
    if len(scope) != 3 or scope[0] != ref:
        state.pop("pending_inline", None)
    elif scope[1] != state.get("q_topic_input", "").strip():
        if pending.get("chat") and store.generation:
            store.generation.cancel(capability, ref, pending["chat"], pending["id"])
        state.setdefault("inline_failures", {})[scope] = "generation_interrupted"
        state.pop("pending_inline", None)


def render_inline_answer(
    store, capability, ref, topic, question_id, answers, *, busy=False
):
    scope = (ref, topic, question_id)
    # This snapshot is loaded server-side for this render, never session_state.
    completed = answers.get(question_id)
    if completed:
        if tuple(st.session_state.get("pending_inline", {}).get("scope", ())) == scope:
            st.session_state.pop("pending_inline", None)
        _render_completed_message(
            "inline_" + question_id, completed, allow_followups=False
        )
        return
    failures = st.session_state.setdefault("inline_failures", {})
    failure = failures.get(scope)
    if failure:
        st.warning(
            SessionError.MESSAGES.get(
                failure, SessionError.MESSAGES["generation_incomplete"]
            )
        )
    pending = st.session_state.get("pending_inline")
    current = bool(pending and tuple(pending["scope"]) == scope)
    if st.button(
        "Retry answer" if failure else "Ask →",
        key="ask_" + question_id,
        disabled=store.generation is None or busy or pending is not None,
    ):
        st.session_state.pending_inline = {
            "scope": scope,
            "id": str(uuid4()),
            "status": "queued",
        }
        failures.pop(scope, None)
        st.rerun()
    if not current:
        return
    try:
        thread = store.question_thread(capability, ref, topic, question_id, create=True)
    except CloudError as error:
        if error.code in {"expired", "login"}:
            raise
        failures[scope] = error.code
        st.session_state.pop("pending_inline", None)
        st.rerun()
    pending["chat"] = thread["chat"]
    api = LocalAPI(store, capability, ref, thread["chat"], thread["documents"])
    if (
        st.button("Cancel answer", key="cancel_inline_" + question_id)
        or pending["status"] == "running"
    ):
        api.cancel(pending["id"])
        st.session_state.pop("pending_inline", None)
        failures[scope] = "generation_interrupted"
        st.rerun()
    pending["status"] = "running"
    slot, events = st.empty(), None
    try:
        with st.status("Searching your sources…", expanded=False) as status:
            events = api.query_stream(
                thread["question"], ref, thread["chat"], turn_id=pending["id"]
            )
            _, meta = api.parse_stream(
                events,
                on_stage=lambda e: status.update(
                    label=STAGE_LABELS.get(e["stage"], "Preparing answer")
                ),
                on_token=lambda text: slot.markdown(
                    '<div class="assistant-bubble">'
                    + render_answer_html(text, [], streaming=True)
                    + "</div>",
                    unsafe_allow_html=True,
                ),
            )
            if not meta.get("completed"):
                failures[scope] = meta.get("stream_error", "generation_incomplete")
    except CloudError as error:
        if error.code in {"expired", "login"}:
            slot.empty()
            raise
        failures[scope] = error.code
    finally:
        if events is not None:
            events.close()
        st.session_state.pop("pending_inline", None)
    # SQLite is the authority, including success committed just before a lost
    # delivery. Never persist an unvalidated draft or resend on a rerun.
    if store.question_answer(capability, ref, topic, question_id):
        failures.pop(scope, None)
    st.rerun()
