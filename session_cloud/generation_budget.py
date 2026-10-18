"""Process-local, atomic, pessimistic provider reservations. Never refunded on failure."""
from dataclasses import dataclass
import threading
import time

from session_cloud.runtime import SessionError


@dataclass(frozen=True)
class GenerationLimits:
    user_requests: int = 25
    global_requests: int = 60
    user_tokens: int = 120_000
    global_tokens: int = 180_000
    lifetime_requests: int = 200
    input_bound: int = 64_000
    context_bound: int = 131_072
    answer_tokens: int = 2048
    auxiliary_tokens: int = 256
    calls_per_turn: int = 6
    seconds: int = 180
    provider_seconds: int = 45
    answer_characters: int = 8000


def prompt_token_bound(messages):
    """UTF-8 byte bound + framing allowance, NOT a measured tokenizer count.

    Byte-level BPE cannot have more ordinary tokens than bytes. Generous fixed
    role/framing allowances cover the fixed chat schema (no tools or images).
    Reject oversized complete prompts; never slice private evidence or history.
    """
    if (not isinstance(messages, list) or not 1 <= len(messages) <= 8
            or any(not isinstance(m, dict) or set(m) != {"role", "content"}
                   or m["role"] not in {"system", "user", "assistant"}
                   or not isinstance(m["content"], str) for m in messages)):
        raise SessionError("generation_invalid")
    return 128 + sum(len(m["content"].encode("utf-8")) + 32 for m in messages)


class ProviderBudget:
    def __init__(self, limits=GenerationLimits(), *, clock=time.time):
        if any(type(v) is not int or v <= 0 for v in vars(limits).values()):
            raise ValueError("Invalid generation limits")
        self.limits, self.clock = limits, clock
        self._lock = threading.Lock()
        self.day, self.calls, self.tokens, self.lifetime = int(clock() // 86400), 0, 0, 0
        self.users = {}

    def reserve(self, owner, messages, output, *, charge=None):
        bound = prompt_token_bound(messages)
        if (type(output) is not int or output <= 0 or output > self.limits.answer_tokens
                or bound > self.limits.input_bound or bound + output > self.limits.context_bound):
            raise SessionError("generation_context")
        amount = bound + output
        with self._lock:
            day = int(self.clock() // 86400)
            if day != self.day:
                self.day, self.calls, self.tokens, self.users = day, 0, 0, {}
            calls, tokens = self.users.get(owner, (0, 0))
            if calls >= self.limits.user_requests:
                raise SessionError("daily_ai_limit")
            if (len(self.users) >= 100 and owner not in self.users
                    or self.calls >= self.limits.global_requests
                    or tokens + amount > self.limits.user_tokens
                    or self.tokens + amount > self.limits.global_tokens
                    or self.lifetime >= self.limits.lifetime_requests):
                raise SessionError("generation_budget")
            # Durable account reservation happens before provider I/O. A failed
            # or uncertain reservation blocks the call, with no local fallback.
            if charge is not None:
                charge()
            self.calls += 1
            self.lifetime += 1
            self.tokens += amount
            self.users[owner] = calls + 1, tokens + amount
        return amount
