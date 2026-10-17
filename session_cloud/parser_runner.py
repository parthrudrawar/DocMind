"""Supervise one private parser process; timeout/revocation kills its descendants."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from session_cloud.parse_contract import ParseFailure, validate_result
from session_cloud.process_limits import memory_guard

WORKER = Path(__file__).with_name("parse_worker.py")


def run_parser(pdf_path, output_path, limits, is_active):
    request = json.dumps({"input": str(pdf_path), "output": str(output_path), "limits": asdict(limits)}).encode()
    if len(request) > 4096:
        raise ParseFailure("pdf_worker")
    # In particular, never forward Streamlit Secrets or provider environment keys.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "LANG", "LC_ALL"}}
    environment.update(TEMP=str(pdf_path.parent), TMP=str(pdf_path.parent), TMPDIR=str(pdf_path.parent),
                       OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    process, guard = None, None
    try:
        guard = memory_guard(limits.memory_bytes, limits.cpu_seconds)
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, "-I", "-B", str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=pdf_path.parent, env=environment, start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if guard:
            guard.attach(process.pid)
        # Child blocks on stdin until the parent has attached its Windows Job cap.
        process.stdin.write(request)
        process.stdin.close()
        while process.poll() is None:
            is_active()  # Registry/TTL/disk check only; no per-poll provider calls.
            if time.monotonic() - started > limits.seconds:
                raise ParseFailure("pdf_time")
            if output_path.exists() and output_path.stat().st_size > limits.output_bytes:
                raise ParseFailure("pdf_resource")
            time.sleep(0.05)
        is_active()
        if process.returncode != 0:
            raise ParseFailure("pdf_resource")
        if not output_path.is_file():
            raise ParseFailure("pdf_worker")
        if output_path.stat().st_size > limits.output_bytes:
            raise ParseFailure("pdf_resource")
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise ParseFailure("pdf_worker") from None
        return validate_result(result, limits)
    except ParseFailure:
        raise
    except OSError:
        raise ParseFailure("pdf_resource") from None
    finally:
        if guard:
            guard.close()  # Kills Windows parser and any OCR descendants.
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
