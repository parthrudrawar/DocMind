"""Original comparison expanders/two-column cards and yellow knowledge-gap cards."""
from html import escape
import re

import streamlit as st

from session_cloud.analysis_view import provenance, queue_analysis, run_pending_analysis


def topic_label(text):
    # Native labels parse Markdown too: prevent model/document image/link syntax.
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", escape(text, quote=True))


def claim_card(filename, text, *, second=False):
    border = ' style="border-left-color:#FC8181;"' if second else ""
    color = ' style="color:#FC8181;"' if second else ""
    return (f'<div class="citation-card"{border}>'
            f'<div class="citation-title"{color}>📄 {escape(filename, quote=True)}</div>'
            f'<div class="citation-text">"{escape(text, quote=True)}</div></div>')


def gap_card(item, number):
    return (f'<div class="citation-card" style="border-left-color:#FBBF24;">'
            f'<div class="citation-title" style="color:#FBBF24;">🔍 {number}. {escape(item["topic"], quote=True)}</div>'
            f'<div class="citation-text" style="color:#A0AEC0;margin-top:6px;">{escape(item["description"], quote=True)}</div></div>')


def render_comparisons(store, capability, ref, documents, kind, *, busy=False):
    contradictions = kind == "contradictions"
    st.markdown("### Contradictions" if contradictions else "### Knowledge Gaps")
    st.caption('Compare evidence across your indexed documents.')
    indexed = [d for d in documents if d["status"] == "indexed"]
    enough = len({d["filename"] for d in indexed}) >= 2 if contradictions else bool(indexed)
    if not enough:
        st.info("Need at least 2 different PDFs. Upload and index them first." if contradictions
                else "No indexed documents. Upload and index at least 1 PDF first.")
    with st.expander('About this analysis'):
        st.caption('Uses sampled excerpts, not every page. Different wording or missing information alone is not a contradiction. Signals are not probabilities. Suggested gaps are not proof of absence.')
    if st.button("🔄 Re-scan Contradictions" if contradictions else "🔄 Re-analyse Gaps",
                 key="run_contra" if contradictions else "run_gaps", disabled=not enough or busy or store.analyses is None):
        queue_analysis(ref, kind, "collection")
    run_pending_analysis(store, capability, ref, kind=kind, target='collection')
    result = (store.get_contradictions(capability, ref) if contradictions else store.get_gaps(capability, ref))
    if result is None:
        st.info('Run an analysis to explore your collection.')
        return
    items = result["items"]
    if not items:
        st.info('No potential conflicts found in the sampled evidence. This is not a full-document audit.'
                if contradictions else 'No potential gaps found in the sampled overview. Other gaps may still exist.')
    elif contradictions:
        st.caption(f"Found {len(items)} potential contradiction(s). Review the source evidence before relying on these findings.")
        for item in items:
            with st.expander(f"⚡ {topic_label(item['topic'])} — model signal {item['confidence']:.2f} (uncalibrated)", expanded=False):
                a, versus, b = st.columns([5, 1, 5])
                for col, suffix in ((a, "a"), (b, "b")):
                    with col:
                        st.markdown(claim_card(item["doc_" + suffix], item["text_" + suffix], second=suffix == "b"), unsafe_allow_html=True)
                        st.caption("Exact excerpt in the sampled input." if item["text_" + suffix + "_is_exact_excerpt"]
                                   else "Model paraphrase; not verified as an exact source quotation.")
                with versus:
                    st.markdown('<div style="text-align:center;padding-top:20px;color:#FC8181;font-weight:bold;">VS</div>', unsafe_allow_html=True)
                st.markdown(f'<p style="color:#718096;font-size:0.83rem;margin-top:8px;">💬 {escape(item["explanation"], quote=True)}</p>', unsafe_allow_html=True)
    else:
        for i, item in enumerate(items, 1):
            st.markdown(gap_card(item, i), unsafe_allow_html=True)
    provenance(result)
