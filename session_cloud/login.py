"""Reuse the existing PKCE browser handshake; auth broker is unchanged."""
import streamlit as st
from authenticated_cloud.auth import CloudError


def login(service, browser_auth):
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
