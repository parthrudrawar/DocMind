"""Phase 3C candidate: original-style conversational RAG and all seven analyses."""
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
from session_cloud.exploration_view import render_exploration, apply_question_action  # noqa: E402

TABS = ["💬  Chat", "📄  Documents", "⚡  Contradictions", "🔍  Knowledge Gaps",
        "💡  Smart Questions", "🔮  Insights", "📅  Timeline"]


@st.cache_resource(show_spinner=False)
def get_runtime(schema_version="phase3c"):
    config = AuthConfig.load(st.secrets)
    # Versioned ephemeral schema; do not migrate or reopen older preview roots.
    provider = GroqStream(GroqConfig.load(st.secrets)) if st.secrets.get("GROQ_API_KEY") else None
    return WorkspaceStore(AuthBroker(AuthOnlyClient(config), config.app_url), provider=provider)


def render_workspace(store, capability):
    ref = store.open(capability)
    chats = store.chats(capability, ref)
    if not chats:
        chats = [store.new_chat(capability, ref)]
    if st.session_state.get("active_chat") not in chats:
        st.session_state.active_chat = chats[-1]
    chat = st.session_state.active_chat
    bind_chat_state(st.session_state, ref, chat, store.history(capability, ref, chat))
    apply_question_action(store, capability, ref, chat, st.session_state)
    st.markdown(CSS, unsafe_allow_html=True)  # Original static CSS only.
    st.markdown('<div class="app-header">🧠 DocuMind</div>', unsafe_allow_html=True)
    st.caption("Temporary private workspace · original hybrid RAG · data expires after logout, expiry or restart")
    documents = store.documents(capability, ref)
    if st.session_state.get("pending_analysis", {}).get("workspace", ref) != ref:
        st.session_state.pop("pending_analysis", None)
    analysis_busy = bool(st.session_state.get("pending_analysis") or st.session_state.get("pending_turn"))
    with st.sidebar:
        st.subheader("🧠 DocuMind")
        st.caption("Google authenticated · temporary workspace")
        if st.button("New Chat", use_container_width=True):
            st.session_state.active_chat = store.new_chat(capability, ref)
            st.rerun()
        selected = st.selectbox("Conversation history", chats, index=chats.index(chat),
                                format_func=lambda item: f"Chat {chats.index(item) + 1}")
        if selected != chat:
            st.session_state.active_chat = selected
            st.rerun()
        st.caption("Up to 5 chats and 20 turns/chat. Data expires after one hour or logout; restart loses access.")
        if st.button("Log out", use_container_width=True):
            store.logout(capability)
            st.session_state.clear()
            st.session_state.cloud_clear = True
            st.rerun()
        st.divider()
        st.subheader("Upload PDFs")
        st.caption("Up to 100 MB/file accepted; 3 files, 128 MiB total workspace storage. "
                   "Acceptance does not mean parsing or indexing succeeded. Avoid sensitive documents.")
        st.caption("Preparation: up to 200 pages; 6 attempts/account/day, 20 shared. Failed attempts count. OCR is disabled.")
        upload = st.file_uploader("Choose a PDF", type=["pdf"], max_upload_size=100,
                                  key="temporary_upload_" + ref)
        if st.button("Store PDF temporarily", disabled=upload is None, use_container_width=True):
            upload.seek(0)
            result = store.upload(capability, ref, upload.name, upload)
            st.info("This PDF is already stored in this workspace; its processing status is shown below." if result["duplicate"]
                    else "PDF stored completely. Prepare it below; preparation does not create a searchable index yet.")
            documents = store.documents(capability, ref)
        st.subheader("Documents")
        for document in documents:
            with st.expander(document["filename"]):
                state = {"accepted": "stored, not prepared or indexed", "prepared": "prepared, not indexed",
                         "indexed": "indexed and searchable"}[document["status"]]
                st.caption(f"{document['size']:,} bytes · {state}")
                if document["status"] == "accepted" and st.button("Prepare PDF", key="prepare_" + document["id"]):
                    with st.status("Parsing all pages and tables under resource limits...", expanded=True):
                        result = store.prepare(capability, ref, document["id"])
                    st.session_state.preparation_notice = f"Prepared {result['pages']} pages completely. Index the PDF below to enable search."
                    st.rerun()
                if document["status"] in {"prepared", "indexed"}:
                    if st.button("Reindex PDF" if document["status"] == "indexed" else "Index PDF", key="index_" + document["id"]):
                        with st.status("Chunking, embedding and building a complete hybrid index...", expanded=True):
                            result = store.index_document(capability, ref, document["id"])
                        st.session_state.preparation_notice = f"Complete workspace snapshot published: {result['chunks']} searchable chunks."
                        st.rerun()
                guide = (store.get_source_guide(capability, ref, document["id"])
                         if document["status"] == "indexed" else None)
                if st.button("Regenerate Source Guide" if guide else "Generate Source Guide",
                             disabled=store.analyses is None or document["status"] != "indexed" or analysis_busy,
                             key="guide_" + document["id"]):
                    queue_analysis(ref, "guide", document["id"])
                if guide:
                    st.markdown(guide_html(guide), unsafe_allow_html=True)
                    provenance(guide)
                elif document["status"] == "indexed":
                    st.caption("Source guide not generated for this index. It uses the original first-800-character summary excerpt; it is not a full-document review.")
                if st.button("Delete", key="delete_" + document["id"]):
                    store.delete_document(capability, ref, document["id"])
                    st.rerun()
        st.caption("Collection Brief covers ALL indexed workspace PDFs, independent of the chat selection. Source guides are never generated automatically.")
        if st.button("Generate Collection Brief", disabled=store.analyses is None or analysis_busy
                     or not any(d["status"] == "indexed" for d in documents), use_container_width=True):
            queue_analysis(ref, "brief", "collection")
        brief = store.get_collection_brief(capability, ref)
        if brief:
            st.markdown(brief_html(brief["brief"]), unsafe_allow_html=True)
            if brief["missing_guide_ids"]:
                st.caption("Some indexed PDFs had no source guide; the original prompt marks those inputs as unavailable.")
            provenance(brief)
        if notice := st.session_state.pop("analysis_notice", None):
            st.info(notice)
        run_pending_analysis(store, capability, ref)

    if notice := st.session_state.pop("preparation_notice", None):
        st.info(notice)
    prepared = {doc["id"]: doc["filename"] for doc in documents if doc["status"] == "indexed"}
    selection_key = "selection_" + ref + "_" + chat
    if selection_key in st.session_state:
        st.session_state[selection_key] = [d for d in st.session_state[selection_key] if d in prepared]
    selection = st.multiselect("Searchable documents", list(prepared),
                   default=([d for d in st.session_state["messages"][-1].get("meta", {}).get("document_ids", []) if d in prepared]
                            if st.session_state["messages"] else list(prepared)),
                   format_func=lambda key: prepared[key], key=selection_key)
    api = LocalAPI(store, capability, ref, chat, selection)

    panels = st.tabs(TABS)
    with panels[0]:
        if store.generation is None:
            st.info("AI is not configured for this candidate. Evidence search remains available.")
        elif not selection:
            st.info("Index and select a PDF to start chatting. AI calls, including failed calls and follow-ups, use the shared free demo allowance.")
        else:
            st.caption("AI drafts stream immediately; only completed, source-validated answers enter chat history. No automatic retries.")
        render_chat_tab(api, enabled=store.generation is not None and bool(selection)
                        and not st.session_state.get("pending_analysis"))
        with st.form("retrieval_" + ref):
            query = st.text_input("Find supporting passages", max_chars=500)
            expanded_context = st.checkbox("Preview original reranking and expanded answer evidence (no AI request)")
            submitted = st.form_submit_button("Search documents", disabled=not selection)
        if submitted:
            with st.status("Preparing evidence from selected PDFs...", expanded=False):
                if expanded_context:
                    context = store.prepare_answer_context(capability, ref, query, selection)
                    hits = context["chunks"]
                else:
                    hits = store.retrieve(capability, ref, query, selection)
            score_label = "Cross-encoder signal" if expanded_context else "RRF score"
            st.caption(f"{len(hits)} passages · retrieval signals are not confidence probabilities")
            if expanded_context:
                st.caption("Neighbor and parent passages inherit relevance signals. No AI answer has been generated.")
            for number, hit in enumerate(hits, 1):
                pages = str(hit["page_number"])
                if hit["page_end"] > hit["page_number"]:
                    pages += "–" + str(hit["page_end"])
                with st.expander(f"Source {number} · {hit['filename']} · page {pages}"):
                    st.text(hit["text"])  # Never render document content as HTML.
                    st.caption(f"{score_label} {hit['score']:.5f} · {hit['chunk_type']}")
    with panels[1]:
        st.subheader("Your temporary documents")
        if documents:
            for document in documents:
                with st.container(border=True):
                    st.text(document["filename"])
                    st.caption(f"{document['size']:,} bytes · {document['status']}")
        else:
            st.info("No documents in this workspace.")
    for panel, kind in ((panels[2], "contradictions"), (panels[3], "gaps")):
        with panel:
            render_comparisons(store, capability, ref, documents, kind, busy=analysis_busy)
    for panel, kind in zip(panels[4:], ("questions", "insights", "timeline")):
        with panel:
            render_exploration(store, capability, ref, chat, documents, kind, busy=analysis_busy)


def main():
    st.set_page_config(page_title="DocuMind · temporary workspace preview", page_icon="🧠", layout="wide")
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
        st.error("The temporary workspace is unavailable. Please try later.")
