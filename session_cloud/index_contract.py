"""Hard ceilings for ephemeral, complete index snapshots (not AI quotas)."""
from dataclasses import asdict, dataclass


class IndexFailure(Exception):
    def __init__(self, code="index_invalid", *, reason="guard"):
        self.code = code if isinstance(code, str) and code in {"index_invalid", "index_resource", "index_time", "index_model",
                                    "index_busy", "context_limit", "context_invalid"} else "index_invalid"
        self.reason = reason if isinstance(reason, str) and reason in DIAGNOSTIC_REASONS else "guard"
        super().__init__(self.code)


# Closed vocabularies: neither exception messages nor document/provider data
# may enter diagnostics, including diagnostics received from the child process.
DIAGNOSTIC_STAGES = frozenset({
    "authorization", "request_encoding", "worker_start", "worker_request", "worker_import",
    "embedding_initialization", "reranker_initialization", "chunking", "embedding",
    "snapshot_publication", "searching", "reranking", "evidence", "analysis_inputs",
    "worker_exit", "resident_memory", "model_cache_capacity", "response_validation",
    "workspace_accounting", "model_cache_accounting",
})
DIAGNOSTIC_REASONS = frozenset({
    "active", "guard", "concurrent_request", "request_capacity", "response_capacity",
    "text_capacity", "chunk_capacity", "snapshot_capacity", "resident_memory",
    "model_cache_capacity", "wall_deadline", "worker_exit", "memory_error", "os_error",
    "import_error", "runtime_error", "value_error", "unexpected_error", "supervisor_io",
    "tokenizer_unavailable", "invalid_response", "invalid_snapshot", "filesystem_churn",
})


def safe_diagnostic(value):
    """Return only allowlisted labels and bounded numbers, never arbitrary text."""
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("stage"), str) and value["stage"] in DIAGNOSTIC_STAGES:
        result["stage"] = value["stage"]
    if isinstance(value.get("reason"), str) and value["reason"] in DIAGNOSTIC_REASONS:
        result["reason"] = value["reason"]
    for name, ceiling in (("native_code", 65536), ("rss_bytes", 64 * 1024**3)):
        number = value.get(name)
        if type(number) is int and 0 <= number <= ceiling:
            result[name] = number
    return result


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
