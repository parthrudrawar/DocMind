"""Google -> Supabase PKCE broker. Tokens never enter component arguments/logs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode
from uuid import UUID

import httpx


class CloudError(Exception):
    MESSAGES = {
        "configuration": "The cloud demo is not configured. Please contact its owner.",
        "login": "Sign-in failed or expired. Please start Google sign-in again.",
        "expired": "Please sign in again. Your saved documents and chats are not deleted.",
        "unavailable": "The service is temporarily unavailable. Please try later.",
        "limit": "A demo usage limit was reached. Please try another day or contact the owner.",
        "document": "This document is unavailable in your account.",
        "busy": "An operation is still in progress. Please try again shortly.",
        "pdf": "Use a readable text PDF: 2 MB, 10 pages/file; 3 PDFs, 20 pages and 200 chunks/account.",
        "answer": "A verified, cited answer could not be prepared. Please try later.",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self.MESSAGES[code])


def startup_diagnostic(stage, reason, *, field=None, http_status=None):
    """Log only allowlisted labels, never exception text, values, URLs or headers."""
    stages = ("secrets", "supabase", "pinecone_index", "oauth", "service")
    reasons = ("missing_variable", "boolean_true_required", "nonempty_string_required",
               "cloud_name_format", "index_name_format", "index_host_format", "publishable_key_required",
               "secrets_parse_or_shape_error", "project_url_format", "write_key_format", "app_url_format",
               "describe_http_error", "describe_rate_limited", "describe_timeout", "describe_network_error",
               "describe_invalid_json", "describe_failed", "describe_shape_invalid", "dimension_mismatch",
               "metric_mismatch", "host_mismatch", "serverless_shape_invalid", "serverless_cloud_mismatch",
               "serverless_region_mismatch", "serverless_extra_fields", "index_not_ready", "unexpected_exception")
    fields = ("FREE_PLANS_CONFIRMED", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "APP_URL",
              "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "PINECONE_API_KEY",
              "PINECONE_INDEX", "PINECONE_HOST", "GROQ_API_KEY", "CLOUD_WRITE_KEY")
    safe_stage = stage if isinstance(stage, str) and stage in stages else "startup"
    safe_reason = reason if isinstance(reason, str) and reason in reasons else "unexpected_exception"
    safe_field = " field=" + field if isinstance(field, str) and field in fields else ""
    safe_status = " http_status=" + str(http_status) if type(http_status) is int and 100 <= http_status <= 599 else ""
    logging.getLogger("documind.cloud.startup").warning(
        "documind_startup stage=%s reason=%s%s%s", safe_stage, safe_reason, safe_field, safe_status)


def configuration_error(stage, reason, *, field=None):
    startup_diagnostic(stage, reason, field=field)
    raise CloudError("configuration") from None


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise CloudError("expired")
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(repr=False)
class Session:
    user_id: str
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    expires: float
    deadline: float


class Supabase:
    """Stateless HTTP requests: no cached client with mutable per-user headers."""

    def __init__(self, url, key, write_key, *, transport=None):
        if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
            configuration_error("supabase", "project_url_format", field="SUPABASE_URL")
        if not key:
            configuration_error("supabase", "nonempty_string_required", field="SUPABASE_PUBLISHABLE_KEY")
        if not re.fullmatch(r"[a-f0-9]{64}", write_key):
            configuration_error("supabase", "write_key_format", field="CLOUD_WRITE_KEY")
        self.url, self._key, self._write_key, self._transport = url, key, write_key, transport

    def call(self, path, *, token=None, payload=None, method="POST"):
        try:
            headers = {"apikey": self._key}
            if token:
                headers["Authorization"] = "Bearer " + token
            if path.startswith("/rest/"):
                headers["X-Documind-Write-Key"] = self._write_key
            with httpx.Client(timeout=30, follow_redirects=False, transport=self._transport) as client:
                response = client.request(method, self.url + path, json=payload,
                                          headers=headers)
            if response.status_code in (401, 403):
                raise CloudError("expired")
            if response.status_code == 429:
                raise CloudError("limit")
            if response.status_code >= 400:
                # Only recognize fixed application errors, never surface provider diagnostics.
                message = response.json().get("message", "")
                code = {"cloud_limit": "limit", "cloud_document": "document",
                        "cloud_busy": "busy"}.get(message, "unavailable")
                raise CloudError(code)
            return response.json() if response.content else None
        except CloudError:
            raise
        except Exception:
            raise CloudError("unavailable") from None

    def rpc(self, token, name, **values):
        return self.call("/rest/v1/rpc/cloud_" + name, token=token, payload=values)


class AuthBroker:
    """Per-tab opaque capabilities -> server-held Supabase sessions.

    Browser storage holds only an app capability and a short-lived login binding.
    Every operation validates the Supabase access token through /auth/v1/user.
    Refreshes are serialized and cannot change the verified account identity.
    A process restart revokes app capabilities; signing in again restores SQL data.
    """

    def __init__(self, db, app_url, *, clock=time.time):
        if not re.fullmatch(r"https://[a-z0-9-]+\.streamlit\.app/", app_url):
            configuration_error("oauth", "app_url_format", field="APP_URL")
        self.db, self.app_url, self.clock = db, app_url, clock
        self._lock = threading.RLock()
        self._flows, self._sessions = {}, {}

    def begin(self):
        with self._lock:
            now = self.clock()
            self._flows = {key: value for key, value in self._flows.items() if value[2] > now}
            self._sessions = {key: value for key, value in self._sessions.items() if value.deadline > now}
            if len(self._flows) >= 32 or len(self._sessions) >= 100:
                raise CloudError("busy")
            state, binding, verifier = (secrets.token_hex(32) for _ in range(3))
            self._flows[state] = (digest(binding), verifier, now + 300)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            redirect = self.app_url + "?oauth_state=" + state
            url = self.db.url + "/auth/v1/authorize?" + urlencode({
                "provider": "google", "redirect_to": redirect, "code_challenge": challenge,
                "code_challenge_method": "s256", "scopes": "email profile", "prompt": "select_account"})
            return {"state": state, "binding": binding, "url": url, "expires": now + 300}

    def _user(self, access):
        user = self.db.call("/auth/v1/user", token=access, method="GET")
        try:
            user_id = str(UUID(user["id"]))
            if (not user.get("email_confirmed_at") or not any(
                    identity.get("provider") == "google" for identity in user.get("identities", []))):
                raise ValueError
            return user_id
        except (KeyError, TypeError, ValueError):
            raise CloudError("login") from None

    def complete(self, state, binding, code):
        with self._lock:
            flow = self._flows.get(state) if isinstance(state, str) else None
            if (not flow or flow[2] <= self.clock() or not hmac.compare_digest(flow[0], digest(binding))
                    or not isinstance(code, str) or not 1 <= len(code) <= 2048):
                raise CloudError("login")
            del self._flows[state]  # one shot, including failed exchange
            result = self.db.call("/auth/v1/token?grant_type=pkce", payload={
                "auth_code": code, "code_verifier": flow[1]})
            user_id = self._user(result["access_token"])
            capability = secrets.token_hex(32)
            self._sessions[digest(capability)] = self._session(result, user_id, self.clock() + 8 * 3600)
            return capability

    def _session(self, result, user_id, deadline):
        try:
            lifetime = min(3600, max(1, int(result["expires_in"])))
            return Session(user_id, result["access_token"], result["refresh_token"],
                           self.clock() + lifetime, deadline)
        except (KeyError, TypeError, ValueError):
            raise CloudError("login") from None

    def authorize(self, capability):
        with self._lock:
            key = digest(capability)
            session = self._sessions.get(key)
            if session is None or session.deadline <= self.clock():
                self._sessions.pop(key, None)
                raise CloudError("expired")
            try:
                if session.expires <= self.clock() + 60:
                    result = self.db.call("/auth/v1/token?grant_type=refresh_token",
                                          payload={"refresh_token": session.refresh})
                    if self._user(result["access_token"]) != session.user_id:
                        raise CloudError("expired")
                    session = self._session(result, session.user_id, session.deadline)
                    self._sessions[key] = session
                if self._user(session.access) != session.user_id:
                    raise CloudError("expired")
            except CloudError:
                self._sessions.pop(key, None)
                raise
            return session

    def logout(self, capability):
        with self._lock:
            session = self._sessions.pop(digest(capability), None)
            if session:
                try:
                    self.db.call("/auth/v1/logout?scope=local", token=session.access)
                except CloudError:
                    # Local access is already revoked. Remote refresh expires normally if unavailable.
                    return False
            return True
