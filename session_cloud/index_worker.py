"""One serialized model worker; retains weights, never request/index caches."""
from pathlib import Path
import contextlib
import gc
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def unix_file_limit(*, loading_model):
    import resource
    # The public MiniLM ONNX file is ~90 MB. Its bounded cache has a separate
    # 160 MiB allowance; applying the 16 MiB snapshot cap before downloading it
    # would make first-run Linux initialization impossible.
    resource.setrlimit(resource.RLIMIT_FSIZE, ((160 if loading_model else 16) * 1024**2, 160 * 1024**2))


def execute(request, model, reranker=None, progress=None):
    from session_cloud.index_contract import IndexLimits
    from session_cloud.index_engine import build, remove, search

    limits = IndexLimits(**request["limits"])
    if request["action"] == "build":
        return build(request["documents"], Path(request["destination"]), model, limits)
    if request["action"] == "search":
        return {"hits": search(Path(request["source"]), request["checksum"], request["query"],
                               request["selected"], model, limits, request["top_k"])}
    if request["action"] == "remove":
        return remove(Path(request["source"]), request["checksum"], request["document"],
                      Path(request["destination"]), limits)
    if request["action"] == "analysis_inputs":
        from session_cloud.bounded_pdf import peak_rss_bytes
        from session_cloud.index_engine import load
        from session_cloud.original_comparisons import comparison_inputs
        from session_cloud.original_exploration import exploration_inputs
        started = time.monotonic()
        chunks, _ = load(Path(request["source"]), request["checksum"], limits)
        project = exploration_inputs if request["kind"] in {"questions", "insights", "timeline"} else comparison_inputs
        return {"documents": project(chunks, request["documents"], request["kind"]),
                "metrics": {"wall_seconds": time.monotonic() - started, "peak_rss_bytes": peak_rss_bytes()}}
    if request["action"] == "context" and reranker is not None:
        from session_cloud.context_engine import prepare_context
        return prepare_context(Path(request["source"]), request["checksum"], request["query"],
            request["selected"], request["sub_questions"], model, reranker, limits, request["top_k"], progress,
            request.get("include_summaries", False))
    raise ValueError("Invalid operation")


def main():
    from session_cloud.index_contract import IndexFailure

    model, reranker = None, None
    # Parent attaches the Windows job BEFORE releasing this handshake.
    startup = json.loads(sys.stdin.buffer.readline(4096))
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
        unix_file_limit(loading_model=True)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    for line in sys.stdin.buffer:
        output, request, result = None, None, None
        try:
            paths = json.loads(line)
            output = Path(paths["output"])
            request = json.loads(Path(paths["input"]).read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(sys.stderr):
                embedding_init, reranker_init = 0., 0.
                if model is None and request["action"] != "analysis_inputs":
                    init_started = time.monotonic()
                    from session_cloud.original_embedder import Embedder
                    model = Embedder(startup["cache"])
                    if os.name != "nt":
                        unix_file_limit(loading_model=False)
                    # Do not silently use the original last-resort word-count
                    # approximation: this candidate requires the real tokenizer.
                    if not (callable(getattr(model._model, "token_count", None))
                            or getattr(getattr(model._model, "model", None), "tokenizer", None)):
                        raise IndexFailure("index_model")
                    embedding_init = time.monotonic() - init_started
                if request["action"] == "context" and reranker is None:
                    from session_cloud.original_reranker import Reranker
                    init_started = time.monotonic()
                    if os.name != "nt":
                        unix_file_limit(loading_model=True)
                    try:
                        reranker = Reranker(startup["reranker_cache"])
                    except Exception:
                        raise IndexFailure("index_model") from None
                    if os.name != "nt":
                        unix_file_limit(loading_model=False)
                    reranker_init = time.monotonic() - init_started
                stages = []

                def progress(stage, status, target=output, **counts):
                    stages.append({"stage": stage, "status": status, "counts": counts})
                    pending = target.with_suffix(".progress-part")
                    pending.write_text(json.dumps(stages[-1]), encoding="utf-8")
                    # Immutable numbered events: replacing a file while a Windows
                    # reader has it open can fail with a sharing violation.
                    pending.replace(target.with_suffix(f".progress-{len(stages):02}"))

                result = execute(request, model, reranker, progress if request["action"] == "context" else None)
                if request["action"] == "context":
                    result["metrics"].update(embedding_init_seconds=embedding_init, reranker_init_seconds=reranker_init)
        except IndexFailure as error:
            result = {"error": error.code}
        except MemoryError:
            result = {"error": "index_resource"}
        except Exception:
            result = {"error": "index_invalid"}
        if output is not None:
            temporary = output.with_suffix(".part")
            temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            temporary.replace(output)
        # No tracebacks, credentials, query/vector/result caches, or user objects
        # live across requests. Only the read-only model/tokenizer is reused.
        # Drop the callback's private output path too; only model state persists.
        progress = None
        stages = None
        paths = None
        temporary = None
        line = None
        del request, result, output
        gc.collect()


if __name__ == "__main__":
    main()
