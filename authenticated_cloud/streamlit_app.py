"""Deploy this separate entrypoint with Python 3.12. No Docker/backend imports."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from authenticated_cloud.auth import AuthBroker, CloudError, Supabase  # noqa: E402
from authenticated_cloud.diagnostics import startup_diagnostic  # noqa: E402
from authenticated_cloud.providers import Config, RemoteStore  # noqa: E402
from authenticated_cloud.service import CloudService  # noqa: E402
from frontend.citations import render_answer_html  # noqa: E402

browser_auth = components.declare_component("documind_cloud_auth", path=str(Path(__file__).parent / "component"))


@st.cache_resource(show_spinner=False)
def get_service():
    stage = "secrets"
    try:
        config = Config.load(st.secrets)
        stage = "supabase"
        db = Supabase(config.supabase_url, config.supabase_key, config.write_key)
        stage = "pinecone_index"
        store = RemoteStore(config)
        store.validate_index()  # never create/upgrade an index automatically
        stage = "oauth"
        auth = AuthBroker(db, config.app_url)
        stage = "service"
        return CloudService(auth, store, config.groq_key)
    except CloudError:
        raise  # expected validation failures already logged safe labels at their source
    except Exception:
        startup_diagnostic(stage, "unexpected_exception")
        raise


def show_answer(answer, citations):
    st.markdown(render_answer_html(answer, citations), unsafe_allow_html=True)
    for cite in citations:
        with st.expander(f"Source {cite['number']}"):
            st.text(f"{cite['filename']} - pages {cite['page_number']}-{cite['page_end']}")
            st.text(cite["exact_quote"])


def login(service):
    auth = service.auth
    if st.session_state.get("cloud_clear"):
        event = browser_auth(clear=True, key="cloud_auth")
        if isinstance(event, dict) and event.get("cleared"):
            for key in ("cloud_clear", "cloud_capability", "cloud_flow"):
                st.session_state.pop(key, None)
            st.rerun()
        st.stop()
    if pending := st.session_state.get("cloud_save"):
        event = browser_auth(save=pending, key="cloud_auth")
        if isinstance(event, dict) and event.get("session") == pending:
            st.session_state.pop("cloud_save", None)
            st.rerun()
        st.stop()
    state, code = st.query_params.get("oauth_state"), st.query_params.get("code")
    if bool(state) != bool(code):
        st.query_params.clear()
        st.session_state.cloud_clear = True
        st.rerun()
    if st.query_params.get("error"):
        st.query_params.clear()
        st.warning(CloudError.MESSAGES["login"])
    if "cloud_flow" not in st.session_state and not state:
        st.session_state.cloud_flow = auth.begin()
    event = browser_auth(flow=st.session_state.get("cloud_flow"), callback=state,
        auth_origin=auth.db.url, key="cloud_auth")
    if not isinstance(event, dict):
        st.stop()
    if event.get("error"):
        st.query_params.clear()
        st.warning(CloudError.MESSAGES["login"])
        if st.button("Restart sign-in"):
            st.session_state.cloud_clear = True
            st.rerun()
        st.stop()
    capability = st.session_state.get("cloud_capability") or event.get("session")
    if state and code and event.get("state") == state:
        try:
            capability = auth.complete(state, event.get("binding"), code)
        except CloudError:
            st.session_state.cloud_clear = True
            st.session_state.cloud_notice = CloudError.MESSAGES["login"]
            st.rerun()
        finally:
            st.query_params.clear()
        st.session_state.cloud_capability = capability
        st.session_state.cloud_save = capability
        st.session_state.pop("cloud_flow", None)
        st.rerun()
    if not capability:
        st.caption("Sign in with your Google account. No application password or email signup is used.")
        st.stop()
    try:
        auth.authorize(capability)
    except CloudError:
        st.session_state.cloud_clear = True
        st.session_state.pop("cloud_capability", None)
        st.rerun()
    st.session_state.cloud_capability = capability
    return capability


def main():
    st.set_page_config(page_title="DocuMind Cloud", page_icon="📚", layout="wide")
    st.title("DocuMind")
    st.caption("Your PDFs, grounded answers and saved conversations")
    service = get_service()
    capability = login(service)
    snapshot = service.snapshot(capability)
    with st.sidebar:
        st.subheader("Your library")
        st.caption("3 text PDFs/account, 2 MB/file, 10 pages/file. Avoid sensitive documents. "
                   "PDFs go to Cloudinary; extracted text to Pinecone and selected evidence to Groq.")
        st.caption("5 AI requests/account/day; 40 shared/day, one/minute. Failed calls count.")
        if st.button("Log out"):
            revoked = service.auth.logout(capability)
            st.session_state.clear()
            st.session_state.cloud_clear = True
            if not revoked:
                st.session_state.cloud_notice = "Signed out locally; remote sign-out could not be confirmed."
            st.rerun()
        if notice := st.session_state.pop("cloud_notice", None):
            st.info(notice)
        with st.form("cloud_upload", clear_on_submit=True):
            upload = st.file_uploader("Add a PDF", type=["pdf"], max_upload_size=2)
            accepted = st.form_submit_button("Upload & index")
        if accepted and upload:
            with st.spinner("Extracting text and indexing your PDF..."):
                result = service.ingest(capability, upload.name, upload.getvalue())
            st.session_state.cloud_notice = "Already indexed in your account." if result["duplicate"] else "PDF indexed."
            st.rerun()
        for document in snapshot["documents"]:
            st.text(f"{document['filename']} ({document['state']})")
            if document["state"] != "ready":
                st.caption("Partial operations remain tracked. Wait ten minutes, then retry deletion.")
            with st.popover("Document actions", help="Actions apply only to your document"):
                st.caption("Deletion also removes chats that cite this PDF.")
                if st.button("Delete / retry cleanup", key="delete_" + document["id"]):
                    service.delete(capability, document["id"])
                    st.rerun()
                st.caption("Original PDFs remain private. This demo does not expose media download URLs.")
    docs = {row["id"]: row["filename"] for row in snapshot["documents"] if row["state"] == "ready"}
    selected = st.multiselect("Ask about these documents", list(docs), default=list(docs), format_func=lambda key: docs[key])
    history = st.container()
    with history:
        for turn in snapshot["turns"]:
            with st.chat_message("user"):
                st.markdown(turn["question"])
            with st.chat_message("assistant"):
                show_answer(turn["answer"], turn["citations"])
    if question := st.chat_input("Ask a question about your PDFs", disabled=not selected, max_chars=500):
        with history:
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("Retrieving evidence and preparing citations..."):
                    result = service.ask(capability, question, selected)
                show_answer(result["text"], result["citations"])


if __name__ == "__main__":
    try:
        main()
    except CloudError as exc:
        st.warning(str(exc))
    except Exception:
        st.error("The cloud demo is temporarily unavailable. Please contact its owner.")
