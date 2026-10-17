"""Auth-only transport for the unchanged Supabase Google PKCE broker."""
from dataclasses import dataclass
import re

import httpx

from authenticated_cloud.auth import CloudError
from authenticated_cloud.diagnostics import configuration_error


@dataclass(frozen=True, repr=False)
class AuthConfig:
    url: str
    key: str
    app_url: str

    @classmethod
    def load(cls, values):
        if values.get("FREE_PLANS_CONFIRMED") is not True:
            configuration_error("secrets", "boolean_true_required", field="FREE_PLANS_CONFIRMED")
        url, key, app_url = (values.get(name) for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "APP_URL"))
        if not isinstance(url, str) or not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
            configuration_error("supabase", "project_url_format", field="SUPABASE_URL")
        if not isinstance(key, str) or not key.startswith("sb_publishable_"):
            configuration_error("secrets", "publishable_key_required", field="SUPABASE_PUBLISHABLE_KEY")
        if not isinstance(app_url, str) or not re.fullmatch(r"https://[a-z0-9-]+\.streamlit\.app/", app_url):
            configuration_error("oauth", "app_url_format", field="APP_URL")
        return cls(url, key, app_url)


class AuthOnlyClient:
    _ALLOWED = {
        ("GET", "/auth/v1/user"), ("POST", "/auth/v1/token?grant_type=pkce"),
        ("POST", "/auth/v1/token?grant_type=refresh_token"), ("POST", "/auth/v1/logout?scope=local"),
    }

    def __init__(self, config, *, transport=None):
        self.url, self._key, self._transport = config.url, config.key, transport

    def call(self, path, *, token=None, payload=None, method="POST"):
        if (method, path) not in self._ALLOWED:
            raise CloudError("unavailable")
        try:
            headers = {"apikey": self._key}
            if token:
                headers["Authorization"] = "Bearer " + token
            with httpx.Client(timeout=30, follow_redirects=False, transport=self._transport) as client:
                response = client.request(method, self.url + path, json=payload, headers=headers)
            if response.status_code in (401, 403):
                raise CloudError("expired")
            if response.status_code == 429:
                raise CloudError("limit")
            response.raise_for_status()
            return response.json() if response.content else None
        except CloudError:
            raise
        except Exception:
            raise CloudError("unavailable") from None
