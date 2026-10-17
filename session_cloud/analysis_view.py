"""Original escaped guide/topic chips and green collection-brief sidebar card."""
from html import escape
from uuid import uuid4

import streamlit as st

from authenticated_cloud.auth import CloudError
from frontend.citations import render_source_card_html
from session_cloud.chat_state import STAGE_LABELS
from session_cloud.runtime import SessionError


def guide_html(guide):
    result = ""
    if guide.get("summary"):
        result += (
            f'<div style="color:#A0AEC0;font-size:0.82rem;margin-bottom:6px;">'
            f'{escape(guide["summary"], quote=True)}</div>'
        )
    if guide.get("key_topics"):
        result += " ".join(
            f'<span style="background:#1A2535;color:#00D4FF;'
            f'border-radius:4px;padding:2px 7px;font-size:0.72rem;">{escape(t, quote=True)}</span>'
            for t in guide["key_topics"]
        )
    return result


def brief_html(brief):
    return (
        f'<div style="background:#0D1A0D;border:1px solid #2D4A2D;border-left:4px solid #4ADE80;'
        f'border-radius:8px;padding:12px 16px;margin-top:8px;">'
        f'<div style="color:#4ADE80;font-weight:700;font-size:0.78rem;margin-bottom:6px;">📋 COLLECTION BRIEF</div>'
        f'<div style="color:#A0AEC0;font-size:0.83rem;line-height:1.6;">'
        f'{escape(brief, quote=True)}</div></div>'
    )


def provenance(result):
    if result.get("input_sources"):
        with st.expander("Source material", expanded=False):
            st.caption('Excerpts used for this analysis—not claim-by-claim citations.')
            for source in result["input_sources"]:
                st.markdown(render_source_card_html(source), unsafe_allow_html=True)


def queue_analysis(ref, kind, target):
    if any(st.session_state.get(k) for k in ('pending_analysis', 'pending_turn', 'pending_inline')):
        return
    st.session_state.pending_analysis = {"workspace": ref, "kind": kind, "target": target,
                                         "id": str(uuid4()), "status": "queued"}
    st.rerun()


def run_pending_analysis(store, capability, ref, *, kind=None, target=None):
    pending = st.session_state.get("pending_analysis")
    feedback = st.session_state.get('analysis_feedback', {})
    if feedback.get('workspace') == ref and feedback.get('kind') == kind and feedback.get('target') == target:
        st.warning(SessionError.MESSAGES.get(feedback['code'], SessionError.MESSAGES['analysis_invalid']))
        if st.button('Retry analysis', key='retry_analysis_' + kind + '_' + str(target)):
            st.session_state.pop('analysis_feedback', None)
            queue_analysis(ref, kind, target)
    if not pending:
        return
    if pending.get("workspace") != ref:
        st.session_state.pop("pending_analysis", None)
        return
    if kind is not None and pending['kind'] != kind:
        return
    if target is not None and pending['target'] != target:
        if store.analyses:
            store.analyses.cancel(capability, ref, pending['kind'], pending['id'])
        st.session_state.pop('pending_analysis', None)
        st.rerun()
    events = None
    try:
        if store.analyses is None:
            raise SessionError("phase")
        if pending.get("status") == "running":
            store.analyses.cancel(capability, ref, pending["kind"], pending["id"])
            raise SessionError("generation_interrupted")
        pending["status"] = "running"
        events = store.analyses.request(capability, ref, pending["kind"], pending["target"], pending["id"])
        done, completed = False, False
        labels = {"guide": "Preparing source guide...", "brief": "Preparing collection brief...",
                  "contradictions": "Comparing document evidence...", "gaps": "Reviewing document coverage...",
                  "questions": "Preparing smart questions...", "insights": "Mining document insights...",
                  "timeline": "Extracting chronological findings..."}
        with st.status(labels[pending["kind"]],
                       expanded=False) as status:
            if st.button("Cancel analysis", key="cancel_analysis"):
                store.analyses.cancel(capability, ref, pending["kind"], pending["id"])
                raise SessionError("generation_interrupted")
            for event in events:
                if event.get("type") == "stage" and event.get("stage") in STAGE_LABELS:
                    status.update(label=STAGE_LABELS[event["stage"]])
                elif event.get("type") == "metadata":
                    completed = event.get("completed") is True
                elif event.get("type") == "done":
                    done = True
            if not done or not completed:
                raise SessionError("generation_incomplete")
        st.session_state.pop('analysis_feedback', None)
    except CloudError as error:
        if error.code in {"expired", "login"}:
            raise
        st.session_state.analysis_feedback = dict(workspace=ref, kind=pending['kind'], target=pending['target'], code=error.code)
    except Exception:
        st.session_state.analysis_feedback = dict(workspace=ref, kind=pending['kind'], target=pending['target'], code='analysis_invalid')
    finally:
        if events is not None:
            events.close()
        st.session_state.pop("pending_analysis", None)
    st.rerun()
