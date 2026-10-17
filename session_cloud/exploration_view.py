"""Original question, insight and chronological timeline cards; explicit actions."""
from html import escape

import streamlit as st

from session_cloud.analysis_view import provenance, queue_analysis, run_pending_analysis
from session_cloud.chat_view import render_question_card_html
from session_cloud.inline_answers import render_inline_answer

TYPE_META = {"pattern": ("🔄", "#6C63FF"), "trend": ("📈", "#4ADE80"), "anomaly": ("⚠️", "#FC8181")}
IMPORTANCE_COLORS = {"high": "#FC8181", "medium": "#FBBF24", "low": "#4A5568"}


def insight_card(insight):
    icon, color = TYPE_META.get(insight["insight_type"], ("💡", "#00D4FF"))
    supporting = escape(", ".join(insight.get("supporting_docs", [])), quote=True)
    return (f'<div class="citation-card" style="border-left-color:{color};margin-bottom:10px;">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
            f'  <span style="font-size:1.1rem;">{icon}</span>'
            f'  <span style="color:{color};font-weight:700;font-size:0.9rem;">{escape(insight["title"], quote=True)}</span>'
            f'  <span style="background:{color}20;color:{color};border-radius:10px;'
            f'    padding:1px 8px;font-size:0.7rem;">{escape(insight["insight_type"].upper(), quote=True)}</span>'
            f'</div>'
            f'<div style="color:#A0AEC0;font-size:0.85rem;line-height:1.5;">{escape(insight["description"], quote=True)}</div>'
            f'{"<div style=color:#4A5568;font-size:0.75rem;margin-top:6px;>📄 " + supporting + "</div>" if supporting else ""}'
            f'</div>')


def year_header(year):
    return (f'<div style="background:linear-gradient(90deg,#6C63FF20,transparent);'
            f'border-left:4px solid #6C63FF;padding:8px 16px;margin:20px 0 10px;'
            f'border-radius:0 8px 8px 0;">'
            f'<span style="color:#818CF8;font-weight:800;font-size:1.1rem;">{escape(year, quote=True)}</span></div>')


def timeline_card(event):
    color = IMPORTANCE_COLORS.get(event.get("importance", "medium"), "#4A5568")
    return (f'<div style="display:flex;gap:12px;margin:6px 0 6px 20px;">'
            f'  <div style="width:3px;background:{color};border-radius:2px;flex-shrink:0;"></div>'
            f'  <div class="citation-card" style="border-left:none;flex:1;margin:0;'
            f'       border-left-color:transparent;">'
            f'    <div style="color:#4A5568;font-size:0.73rem;margin-bottom:3px;">'
            f'      📄 {escape(event["filename"], quote=True)}    </div>'
            f'    <div style="color:#CBD5E0;font-size:0.88rem;">{escape(event["claim"], quote=True)}</div>'
            f'  </div></div>')


def apply_question_action(store, capability, ref, chat, state):
    """Discard old navigation intents; Ask now stays within its question card."""
    state.pop('analysis_ask', None)


def render_exploration(store, capability, ref, chat, documents, kind, *, busy=False):
    titles = {"questions": "### Smart Questions", "insights": "### Insights",
              "timeline": "### Timeline"}
    actions = {"questions": "✨ Generate Smart Questions", "insights": "🔮 Mine Insights", "timeline": "📅 Build Timeline"}
    st.markdown(titles[kind])
    st.caption('Explore ideas across all your indexed documents.')
    indexed = [d for d in documents if d["status"] == "indexed"]
    target = "collection"
    if kind in {"questions", "insights"}:
        target = st.text_input("Focus on a specific topic (optional)", max_chars=500,
            placeholder="e.g. key findings, methods, chapter 3…", key="q_topic_input" if kind == "questions" else "i_topic_input").strip()
    if not indexed:
        st.info("No indexed documents. Upload and index at least one PDF first.")
    if st.button(actions[kind], key="run_" + kind, disabled=not indexed or busy or store.analyses is None):
        queue_analysis(ref, kind, target)
    run_pending_analysis(store, capability, ref, kind=kind, target=target)
    result = (store.get_questions(capability, ref, target) if kind == "questions" else
              store.get_insights(capability, ref, target) if kind == "insights" else store.get_timeline(capability, ref))
    if result is None:
        st.info('Choose a topic or explore your whole collection to get started.')
        return
    if kind == "questions":
        answers = store.question_answers(capability, ref, target)
        if target:
            st.text("Questions about: " + target)
        with st.container(key="documind_smart_questions"):
            for i, item in enumerate(result["items"], 1):
                with st.container(key=f"documind_question_{i}"):
                    st.markdown(render_question_card_html(i, item["question"]), unsafe_allow_html=True)
                    render_inline_answer(store, capability, ref, target, item['id'], answers, busy=busy)
    elif kind == "insights":
        st.caption('Patterns and possibilities to explore—not verified conclusions.')
        for item in result["items"]:
            st.markdown(insight_card(item), unsafe_allow_html=True)
    else:
        st.caption('Suggested chronology. Dates may be inferred; check the sources.')
        current_year = None
        for item in result["items"]:
            if item["year"] != current_year:
                current_year = item["year"]
                st.markdown(year_header(current_year), unsafe_allow_html=True)
            st.markdown(timeline_card(item), unsafe_allow_html=True)
    if not result["items"]:
        st.info("The analysis completed with no items. This is not a guarantee of complete document coverage.")
    provenance(result)
