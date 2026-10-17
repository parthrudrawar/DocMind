"""Private parser worker: -I isolated interpreter, no credentials, no document logs."""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from session_cloud.parse_contract import ParseFailure, ParseLimits  # noqa: E402
from session_cloud.process_limits import unix_limits  # noqa: E402


def main():
    request = json.loads(sys.stdin.read(4096))
    limits = ParseLimits(**request["limits"])
    destination = Path(request["output"])
    if destination.parent != Path(request["input"]).parent:
        return 2
    try:
        if os.name != "nt":
            unix_limits(limits.memory_bytes, max(limits.output_bytes, 16 * 1024 * 1024), limits.cpu_seconds)
        from session_cloud.bounded_pdf import prepare_pdf
        result = prepare_pdf(request["input"], limits)
    except ParseFailure as error:
        result = {"version": 1, "error": error.code}
    except MemoryError:
        result = {"version": 1, "error": "pdf_resource"}
    except Exception:
        result = {"version": 1, "error": "pdf_worker"}
    data = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(data) > limits.output_bytes:
        data = b'{"version":1,"error":"pdf_text"}'
    with destination.open("xb") as output:
        output.write(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
