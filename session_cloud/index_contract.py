"""Hard ceilings for ephemeral, complete index snapshots (not AI quotas)."""
from dataclasses import asdict, dataclass


class IndexFailure(Exception):
    def __init__(self, code="index_invalid"):
        self.code = code if code in {"index_invalid", "index_resource", "index_time", "index_model",
                                    "context_limit", "context_invalid"} else "index_invalid"
        super().__init__(self.code)


@dataclass(frozen=True)
class IndexLimits:
    chunks: int = 1200  # retrieval children, across at most three documents
    text_bytes: int = 3_000_000
    snapshot_bytes: int = 16 * 1024 * 1024
    memory_bytes: int = 640 * 1024 * 1024
    seconds: int = 120

    def __post_init__(self):
        for value, ceiling in zip(asdict(self).values(), (1200, 3_000_000, 16 * 1024 * 1024, 640 * 1024 * 1024, 120)):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("Index limit is invalid")
