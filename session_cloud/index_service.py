"""Bounded, serialized supervisor; model weights shared only within this store."""
import atexit
import errno
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from session_cloud.index_contract import IndexFailure, safe_diagnostic
from session_cloud.disk_usage import model_cache_bytes
from session_cloud.process_limits import memory_guard

logger = logging.getLogger(__name__)
WORKER = Path(__file__).with_name("index_worker.py")


class IndexService:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.reranker_cache = self.cache.with_name(self.cache.name + "-reranker")
        self.process, self.guard = None, None
        self._memory_bytes = None
        self.lock = threading.Lock()
        atexit.register(self.close)

    def close(self):
        if self.guard:
            self.guard.close()
            self.guard = None
        if self.process:
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.kill()
                else:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.process.wait(timeout=5)
            self.process.stdin.close()
            self.process = None

    def _start(self, limits):
        if self.process and self.process.poll() is None and self._memory_bytes == limits.memory_bytes:
            return
        self.close()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.reranker_cache.mkdir(parents=True, exist_ok=True)
        environment = {k: v for k, v in os.environ.items()
                       if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "LANG", "LC_ALL"}}
        environment.update(TEMP=str(self.cache), TMP=str(self.cache), TMPDIR=str(self.cache),
                           OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                           TOKENIZERS_PARALLELISM="false", HF_HUB_DISABLE_TELEMETRY="1")
        # One model process per store, never one per user. Per-request wall
        # deadlines kill/recreate it; long cumulative CPU cap avoids idle leaks.
        self.guard = memory_guard(limits.memory_bytes, 86400)
        self._memory_bytes = limits.memory_bytes
        self.process = subprocess.Popen([sys.executable, "-I", "-B", str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=self.cache, env=environment, start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if self.guard:
            self.guard.attach(self.process.pid)
        self.process.stdin.write((json.dumps({"cache": str(self.cache), "reranker_cache": str(self.reranker_cache)}) + "\n").encode())
        self.process.stdin.flush()

    def run(self, request, folder, limits, active, on_stage=None):
        if not self.lock.acquire(blocking=False):
            logger.warning("documind_index stage=authorization reason=concurrent_request code=index_busy")
            raise IndexFailure("index_busy", reason="concurrent_request")
        source, output = Path(folder) / "index-request.json", Path(folder) / "index-response.json"
        emitted = 0
        started, rss, diagnostic = time.monotonic(), None, {}

        def report_progress():
            nonlocal emitted
            while on_stage and emitted < 20:
                progress = output.with_suffix(f".progress-{emitted + 1:02}")
                if not progress.exists():
                    return
                if progress.stat().st_size > 1000:
                    raise IndexFailure("context_invalid")
                event = json.loads(progress.read_text(encoding="utf-8"))
                if (not isinstance(event, dict) or event.get("stage") not in {"searching", "reranking", "evidence"}
                        or event.get("status") not in {"active", "complete"}
                        or not isinstance(event.get("counts"), dict)
                        or any(k not in {"retrieved_passages", "selected_passages"} or type(v) is not int or v < 0
                               for k, v in event["counts"].items())):
                    raise IndexFailure("context_invalid")
                on_stage(event)
                emitted += 1
        stage = "authorization"

        def check_caches():
            nonlocal stage
            stage = "model_cache_accounting"
            if any(model_cache_bytes(cache) > 160 * 1024**2 for cache in (self.cache, self.reranker_cache)):
                stage = "model_cache_capacity"
                raise IndexFailure("index_resource", reason="model_cache_capacity")

        try:
            stage = "workspace_accounting"
            active()
            stage = "request_encoding"
            request = {**request, "limits": asdict(limits)}
            data = json.dumps(request, ensure_ascii=False)
            if len(data.encode()) > 12 * 1024 * 1024:
                raise IndexFailure("index_resource", reason="request_capacity")
            source.write_text(data, encoding="utf-8")
            stage = "worker_start"
            self._start(limits)
            self.process.stdin.write((json.dumps({"input": str(source), "output": str(output)}) + "\n").encode())
            self.process.stdin.flush()
            stage = "worker_request"
            while not output.exists():
                stage = "workspace_accounting"
                active()
                stage = "worker_request"
                report_progress()
                if self.process.poll() is not None:
                    stage = "worker_exit"
                    raise IndexFailure("index_resource", reason="worker_exit")
                if time.monotonic() - started > limits.seconds:
                    raise IndexFailure("index_time", reason="wall_deadline")
                if os.name != "nt":
                    # Linux RLIMIT_RSS is not enforced. Monitor resident memory
                    # in addition to virtual-address/file caps inside the child.
                    status = Path(f"/proc/{self.process.pid}/status").read_text()
                    rss = next(int(s.split()[1]) * 1024 for s in status.splitlines() if s.startswith("VmRSS:"))
                    if rss > limits.memory_bytes:
                        stage = "resident_memory"
                        raise IndexFailure("index_resource", reason="resident_memory")
                check_caches()
                time.sleep(0.05)
            stage = "workspace_accounting"
            active()
            # A fast worker can finish between samples. Verify the final cache
            # size too; transient scan retries never waive publication budgets.
            check_caches()
            if time.monotonic() - started > limits.seconds:
                raise IndexFailure("index_time", reason="wall_deadline")
            report_progress()
            stage = "response_validation"
            if output.stat().st_size > 4 * 1024 * 1024:
                raise IndexFailure("index_resource", reason="response_capacity")
            result = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise IndexFailure(reason="invalid_response")
            if "error" in result:
                diagnostic = safe_diagnostic(result.get("diagnostic"))
                raise IndexFailure(result["error"], reason=diagnostic.get("reason", "guard"))
            return result
        except IndexFailure as error:
            # A killed worker cannot return JSON. Its small sidecar records the
            # last known phase without text, filenames or workspace identifiers.
            last = output.with_suffix(".diagnostic")
            if not diagnostic and last.is_file():
                try:
                    if last.stat().st_size <= 512:
                        diagnostic = safe_diagnostic(json.loads(last.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            logger.warning("documind_index stage=%s worker_stage=%s reason=%s code=%s exit=%s "
                           "rss_bytes=%s memory_limit_bytes=%s elapsed_ms=%s native_code=%s",
                           stage, diagnostic.get("stage", "unknown"), error.reason, error.code,
                           self.process.poll() if self.process else None, rss, limits.memory_bytes,
                           round((time.monotonic() - started) * 1000), diagnostic.get("native_code"))
            self.close()
            raise
        except (OSError, ValueError, StopIteration) as error:
            # Never interpolate exception text, requests, paths, headers or keys.
            reason = "filesystem_churn" if getattr(error, "errno", None) == errno.EAGAIN else "supervisor_io"
            logger.warning("documind_index stage=%s reason=%s code=index_resource native_code=%s", stage, reason,
                           getattr(error, "winerror", None) or getattr(error, "errno", None))
            self.close()
            raise IndexFailure("index_resource", reason=reason) from None
        except Exception:
            self.close()  # Never leave an expired/failed request running.
            raise
        finally:
            source.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            output.with_suffix(".part").unlink(missing_ok=True)
            for i in range(1, 21):
                output.with_suffix(f".progress-{i:02}").unlink(missing_ok=True)
            output.with_suffix(".progress-part").unlink(missing_ok=True)
            output.with_suffix(".diagnostic").unlink(missing_ok=True)
            output.with_suffix(".diagnostic-part").unlink(missing_ok=True)
            self.lock.release()
