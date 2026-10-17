"""Fixed-origin Groq SSE transport: no SDK retries, secrets in server memory only."""
from dataclasses import dataclass
import json
import re
import time

import httpx

from session_cloud.runtime import SessionError

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


@dataclass(frozen=True, repr=False)
class GroqConfig:
    key: str
    model: str = "openai/gpt-oss-120b"

    @classmethod
    def load(cls, values):
        key, model = values.get("GROQ_API_KEY"), values.get("GROQ_MODEL", "openai/gpt-oss-120b")
        if (values.get("FREE_PLANS_CONFIRMED") is not True or not isinstance(key, str)
                or not key.startswith("gsk_") or not 16 <= len(key) <= 256
                or any(c.isspace() for c in key)
                or model != "openai/gpt-oss-120b"):
            # Explicit model is fixed to the original pipeline's reviewed model.
            # No automatic fallback, paid-tier selection or arbitrary provider URL.
            raise SessionError("configuration")
        return cls(key, model)


class GroqStream:
    def __init__(self, config, *, transport=None, monotonic=time.monotonic):
        self._config, self._transport, self.monotonic = config, transport, monotonic

    def events(self, messages, output_tokens, active, *, seconds=45, temperature=0):
        started, total, finished, usage, received = self.monotonic(), 0, False, None, False
        try:
            if type(temperature) not in (int, float) or temperature not in (0, 0.2, 0.3, 0.4, 0.5):
                raise SessionError("generation_invalid")
            active()
            with httpx.Client(transport=self._transport, follow_redirects=False, trust_env=False,
                              timeout=httpx.Timeout(10., connect=5., pool=5.)) as client:
                with client.stream("POST", ENDPOINT,
                    headers={"Authorization": "Bearer " + self._config.key},
                    json={"model": self._config.model, "temperature": temperature, "messages": messages,
                          "max_completion_tokens": output_tokens, "stream": True,
                          "reasoning_format": "hidden"}) as response:
                    if response.status_code != 200:
                        code = {429: "provider_rate", 401: "provider_auth", 403: "provider_auth",
                                404: "provider_model", 408: "provider_timeout"}.get(
                                    response.status_code, "provider_unavailable")
                        # Do not consume/log response bodies or secret-bearing request objects.
                        raise SessionError(code)
                    # Bounded raw framing: iter_lines() alone can buffer an unlimited line.
                    buffer = b""
                    for data in response.iter_bytes():
                        active()
                        if self.monotonic() - started > seconds:
                            raise SessionError("provider_timeout")
                        total += len(data)
                        if total > 128_000:
                            raise SessionError("generation_invalid")
                        buffer += data
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if len(line) > 32_000:
                                raise SessionError("generation_invalid")
                            line = line.strip()
                            if not line or line.startswith(b":"):
                                continue
                            if not line.startswith(b"data:"):
                                raise SessionError("generation_invalid")
                            raw = line[5:].strip()
                            if raw == b"[DONE]":
                                if not finished or not received:
                                    raise SessionError("generation_invalid")
                                yield {"type": "usage", "usage": usage}
                                return
                            item = json.loads(raw)
                            if not isinstance(item, dict) or "error" in item:
                                raise SessionError("generation_invalid")
                            observed = item.get("usage") or (item.get("x_groq") or {}).get("usage")
                            if observed is not None:
                                if (not isinstance(observed, dict) or any(type(observed.get(k)) is not int
                                        or observed[k] < 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens"))):
                                    raise SessionError("generation_invalid")
                                usage = {k: observed[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                            choices = item.get("choices", [])
                            if not isinstance(choices, list) or len(choices) > 1:
                                raise SessionError("generation_invalid")
                            for choice in choices:
                                if choice.get("index", 0) != 0:
                                    raise SessionError("generation_invalid")
                                delta = choice.get("delta") or {}
                                if not isinstance(delta, dict) or delta.get("tool_calls") or delta.get("function_call"):
                                    raise SessionError("generation_invalid")
                                text = delta.get("content")
                                if text is not None:
                                    if not isinstance(text, str) or finished and text:
                                        raise SessionError("generation_invalid")
                                    if text:
                                        received = True
                                        yield {"type": "token", "content": text}
                                reason = choice.get("finish_reason")
                                if reason is not None:
                                    if reason != "stop" or finished:
                                        raise SessionError("generation_incomplete")
                                    finished = True
                        if len(buffer) > 32_000:
                            raise SessionError("generation_invalid")
                    # A normal TCP EOF without the protocol terminator is interrupted.
                    raise SessionError("generation_incomplete")
        except SessionError:
            raise
        except httpx.TimeoutException:
            raise SessionError("provider_timeout") from None
        except (httpx.HTTPError, OSError):
            raise SessionError("provider_unavailable") from None
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise SessionError("generation_invalid") from None


def string_array(text, maximum, *, length=500):
    try:
        values = json.loads(text)
        if (not isinstance(values, list) or not 1 <= len(values) <= maximum
                or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= length
                       or re.search(r"[\x00-\x08\x0b-\x1f]", v) for v in values)):
            raise ValueError
        return list(dict.fromkeys(v.strip() for v in values))
    except (ValueError, TypeError):
        raise SessionError("generation_invalid") from None
