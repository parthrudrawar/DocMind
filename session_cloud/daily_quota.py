"""Durable preview account allowance; authenticated, fixed-origin RPC only."""
import httpx

from session_cloud.runtime import SessionError


class SupabaseDailyQuota:
    def __init__(self, auth, config, *, transport=None):
        self.auth, self.config, self.transport = auth, config, transport

    def reserve(self, capability, owner):
        session = self.auth.authorize(capability)
        if session.user_id != owner:
            raise SessionError("expired")
        try:
            # No owner/day/count supplied by the browser or this client. SQL
            # derives identity from the verified JWT and uses database UTC time.
            with httpx.Client(timeout=10, follow_redirects=False, transport=self.transport) as client:
                response = client.post(self.config.url + "/rest/v1/rpc/preview_reserve_ai",
                    headers={"apikey": self.config.key, "Authorization": "Bearer " + session.access},
                    json={})
            if response.status_code in (401, 403):
                raise SessionError("expired")
            response.raise_for_status()
            count = response.json()
            if type(count) is not int or not 0 <= count <= 25:
                raise ValueError
            if count == 0:
                raise SessionError("daily_ai_limit")
        except SessionError:
            raise
        except Exception:
            # Never retry an uncertain reservation or log response bodies,
            # tokens, headers, URLs, prompts or identifiers.
            raise SessionError("quota_unavailable") from None
