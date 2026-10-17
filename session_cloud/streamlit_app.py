"""Authenticated temporary DocuMind preview; no remote document stores."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
from authenticated_cloud.auth import AuthBroker, CloudError  # noqa: E402
from session_cloud.adapter import LocalAPI, bind_chat_state  # noqa: E402
from session_cloud.auth_client import AuthConfig, AuthOnlyClient  # noqa: E402
from session_cloud.chat_view import render_chat_tab  # noqa: E402
from session_cloud.login import login  # noqa: E402
from session_cloud.runtime import SessionError, WorkspaceStore  # noqa: E402
from session_cloud.theme import CSS  # noqa: E402
from session_cloud.groq_stream import GroqConfig, GroqStream  # noqa: E402
from session_cloud.analysis_view import brief_html, guide_html, provenance, queue_analysis, run_pending_analysis  # noqa: E402
from session_cloud.comparison_view import render_comparisons  # noqa: E402
from session_cloud.exploration_view import render_exploration  # noqa: E402
from session_cloud.upload_workflow import render_upload  # noqa: E402
from session_cloud.inline_answers import reconcile_inline_scope  # noqa: E402

TABS = ["Chat", "Documents", "Contradictions", "Knowledge Gaps",
        "Smart Questions", "Insights", "Timeline"]
TAGLINE = "Explore your documents. Ask smarter questions. Discover insights backed by your sources."


@st.cache_resource(show_spinner=False)
def get_runtime(schema_version="polished-preview-v1"):
    config = AuthConfig.load(st.secrets)
    provider = GroqStream(GroqConfig.load(st.secrets)) if st.secrets.get("GROQ_API_KEY") else None
    return WorkspaceStore(AuthBroker(AuthOnlyClient(config), config.app_url), provider=provider)


def render_help():
    with st.expander("Help & privacy"):
        st.markdown("Your files and chats are private to this sign-in and temporary. "
                    "They expire after one hour, logout, or an app restart. Avoid sensitive documents.")
        st.caption("Cleanup runs on app activity; inaccessible temporary bytes may remain until host recycling.")
        st.markdown("**PDF limits:** 100 MB/file accepted, 3 files, 128 MiB/workspace; "
                    "up to 200 pages, 1 MB extracted text and 1,200 retrieval chunks. "
                    "Large or scanned PDFs may fail processing. OCR is off. No partial index is published.")
        st.markdown("**Free demo allowance:** 6 upload, preparation and indexing attempts/account/day; "
                    "20 AI calls/account/day, 60 shared, including failures and analysis. "
                    "Token and provider limits may stop requests sooner. No automatic AI retries.")
        st.caption("Five conversations, twenty turns each; ten inline answer threads. "
                   "Counters are process-local, not durable across restarts.")
        st.markdown("**Analysis:** tools sample indexed excerpts; source material shows input provenance, "
                    "not claim-level proof. Questions may be unanswerable; timeline dates may be inferred. "
                    "Chat references point to the actual evidence supplied. Evidence signals are not probabilities.")


def render_workspace(store, capability):
    ref = store.open(capability)
    chats = store.chats(capability, ref) or [store.new_chat(capability, ref)]
    if st.session_state.get("workspace_scope") != ref:
        for key in ("pending_inline", "inline_failures", "upload_feedback", "pending_analysis", "analysis_feedback"):
            st.session_state.pop(key, None)
        st.session_state.workspace_scope = ref
    if st.session_state.get("active_chat") not in chats:
        st.session_state.active_chat = chats[-1]
    chat = st.session_state.active_chat
    bind_chat_state(st.session_state, ref, chat, store.history(capability, ref, chat))
    reconcile_inline_scope(store, capability, ref, st.session_state)
    st.markdown(CSS, unsafe_allow_html=True)
    with st.container(key="documind_app_header"):
        st.markdown('<div class="app-header">DocuMind</div>', unsafe_allow_html=True)
        st.caption(TAGLINE)
    busy = any(st.session_state.get(k) for k in ("pending_turn", "pending_analysis", "pending_inline"))
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">DocuMind</div>', unsafe_allow_html=True)
        with st.container(key="documind_sidebar_actions"):
            if st.button("New Chat", use_container_width=True):
                st.session_state.active_chat = store.new_chat(capability, ref)
                st.rerun()
        selected_chat = st.selectbox("Conversations", chats, index=chats.index(chat),
                                    format_func=lambda item: f"Chat {chats.index(item) + 1}")
        if selected_chat != chat:
            st.session_state.active_chat = selected_chat
            st.rerun()
        if st.button("Log out", use_container_width=True):
            store.logout(capability)
            st.session_state.clear()
            st.session_state.cloud_clear = True
            st.rerun()
        st.divider()
        st.subheader("Your documents")
        st.caption("Add a PDF. We’ll get it ready automatically.")
        upload = st.file_uploader("Choose a PDF", type=["pdf"], max_upload_size=100,
                                  disabled=busy, key="temporary_upload_" + ref)
        render_upload(store, capability, ref, upload, st.session_state, st)
        documents = store.documents(capability, ref)
        if not documents:
            st.caption("Your sources will appear here.")
        for doc in documents:
            with st.expander(doc["filename"]):
                st.caption({"accepted": "Waiting to process", "prepared": "Ready to finish indexing",
                            "indexed": "Ready to chat"}[doc["status"]])
                if doc["status"] == "indexed":
                    guide = store.get_source_guide(capability, ref, doc["id"])
                    if st.button("Refresh Source Guide" if guide else "Source Guide",
                                 disabled=busy or store.analyses is None, key="guide_" + doc["id"]):
                        queue_analysis(ref, "guide", doc["id"])
                    run_pending_analysis(store, capability, ref, kind="guide", target=doc["id"])
                    if guide:
                        st.markdown('<section class="analysis-result">' + guide_html(guide) + '</section>', unsafe_allow_html=True)
                        provenance(guide)
                if st.button("Delete", disabled=busy, key="delete_" + doc["id"]):
                    store.delete_document(capability, ref, doc["id"])
                    # Keep the handled widget identity: its selected bytes
                    # survive reruns. render_upload checks the registry before
                    # showing success, so deletion cannot silently re-upload it.
                    st.rerun()
                with st.expander("Advanced processing"):
                    if doc["status"] == "accepted" and st.button("Prepare PDF", disabled=busy, key="prepare_" + doc["id"]):
                        with st.status("Reading pages and tables…"):
                            store.prepare(capability, ref, doc["id"])
                        st.rerun()
                    if doc["status"] in {"prepared", "indexed"} and st.button(
                            "Reindex PDF" if doc["status"] == "indexed" else "Index PDF",
                            disabled=busy, key="index_" + doc["id"]):
                        with st.status("Building searchable evidence…"):
                            store.index_document(capability, ref, doc["id"])
                        st.rerun()
        ready = {d["id"]: d["filename"] for d in documents if d["status"] == "indexed"}
        selection_key = "selection_" + ref + "_" + chat
        if selection_key in st.session_state:
            st.session_state[selection_key] = [d for d in st.session_state[selection_key] if d in ready]
        selection = st.multiselect("Chat sources", list(ready), default=list(ready),
                                   format_func=lambda key: ready[key], key=selection_key, disabled=busy)
        render_help()
    api = LocalAPI(store, capability, ref, chat, selection)
    panels = st.tabs(TABS)
    with panels[0]:
        if store.generation is None:
            st.info("AI is unavailable. Please contact the demo owner.")
        render_chat_tab(api, enabled=store.generation is not None and bool(selection)
                        and not st.session_state.get("pending_analysis") and not st.session_state.get("pending_inline"))
    with panels[1]:
        st.subheader("Your collection")
        st.caption("Explore all your indexed documents together.")
        with st.container(border=True, key="documind_collection_brief"):
            if st.button("Generate Collection Brief", key="run_brief", disabled=busy or not ready or store.analyses is None):
                queue_analysis(ref, "brief", "collection")
            run_pending_analysis(store, capability, ref, kind="brief", target="collection")
            brief = store.get_collection_brief(capability, ref)
            if brief:
                st.markdown('<section class="analysis-result">' + brief_html(brief["brief"]) + '</section>', unsafe_allow_html=True)
                if brief["missing_guide_ids"]:
                    st.caption("Some sources have no Source Guide yet. Generate their guides for a fuller brief.")
                provenance(brief)
            else:
                st.caption("A concise overview of your collection, grounded in its Source Guides.")
        for doc in documents:
            with st.container(border=True):
                st.text(doc["filename"])
                st.caption("Ready" if doc["status"] == "indexed" else "Processing incomplete — see the sidebar")
    for panel, kind in ((panels[2], "contradictions"), (panels[3], "gaps")):
        with panel:
            render_comparisons(store, capability, ref, documents, kind, busy=busy)
    for panel, kind in zip(panels[4:], ("questions", "insights", "timeline")):
        with panel:
            render_exploration(store, capability, ref, chat, documents, kind, busy=busy)
    with st.sidebar.expander("Advanced source search"):
        with st.form("retrieval_" + ref):
            query = st.text_input("Find supporting passages", max_chars=500, key='source_search_query')
            expanded = st.checkbox("Include surrounding context", key='source_search_context')
            submitted = st.form_submit_button("Search documents", disabled=not selection or busy)
        if submitted and query.strip():
            hits = (store.prepare_answer_context(capability, ref, query, selection)["chunks"] if expanded
                    else store.retrieve(capability, ref, query, selection))
            for number, hit in enumerate(hits, 1):
                with st.expander(f"Source {number} · {hit['filename']} · page {hit['page_number']}"):
                    st.text(hit["text"])
        elif submitted:
            st.info('Enter a few words to search your sources.')


def main():
    st.set_page_config(page_title="DocuMind", page_icon="🧠", layout="wide")
    store = get_runtime()
    browser_auth = components.declare_component("documind_cloud_auth",
        path=str(ROOT / "authenticated_cloud/component"))
    capability = login(store, browser_auth)
    render_workspace(store, capability)


if __name__ == "__main__":
    try:
        main()
    except CloudError as error:
        if error.code in {"expired", "login"}:
            st.session_state.clear()
            st.session_state.cloud_clear = True
            st.warning(SessionError.MESSAGES["expired"])
        else:
            st.warning(str(error))
    except Exception:
        st.error("The workspace is unavailable. Please try later.")
