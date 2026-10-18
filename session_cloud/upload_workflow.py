"""Authorized, content-idempotent automatic ingestion; failures require a click."""

import hashlib
import logging
import time

from authenticated_cloud.auth import CloudError

logger = logging.getLogger(__name__)


class UploadWorkflow:
    def ingest_upload(
        self, capability, ref, name, stream, *, retry=False, progress=lambda _: None
    ):
        from session_cloud.runtime import SessionError

        self.check(capability, ref)
        digest, size, started = hashlib.sha256(), 0, time.monotonic()
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            size += len(block)
            if (
                size > self.limits.upload_bytes
                or time.monotonic() - started > self.limits.operation_seconds
            ):
                raise SessionError("upload")
            digest.update(block)
        key = (ref, digest.hexdigest())
        with self._lock:
            self.check(capability, ref)
            documents = {d["id"]: d for d in self.documents(capability, ref)}
            if key[1] in documents and documents[key[1]]["status"] == "indexed":
                return {"status": "indexed", "id": key[1], "duplicate": True}
            old = self._ingestions.get(key)
            if old and old["status"] == "running":
                raise SessionError("busy")
            if old and not retry:
                raise SessionError(old.get("code", "generation_interrupted"))
            if len([k for k in self._ingestions if k[0] == ref]) >= 64 and old is None:
                raise SessionError("limit")
            self._ingestions[key] = {"status": "running"}
        succeeded = False
        code = "upload_interrupted"
        stage = "store"
        try:
            doc = documents.get(key[1])
            if doc is None:
                progress("Storing PDF")
                stream.seek(0)
                self.upload(capability, ref, name, stream)
                doc = next(
                    d for d in self.documents(capability, ref) if d["id"] == key[1]
                )
            if doc["status"] == "accepted":
                stage = "prepare"
                progress("Reading pages and tables")
                self.prepare(capability, ref, key[1])
            stage = "index"
            progress("Building searchable evidence")
            self.index_document(capability, ref, key[1])
            self.check(capability, ref)
            succeeded = True
            return {"status": "indexed", "id": key[1], "duplicate": False}
        except CloudError as error:
            code = error.code
            logger.warning("documind_ingestion stage=%s code=%s", stage,
                           code if code in SessionError.MESSAGES else "unknown")
            raise
        except Exception:
            code = "index_invalid" if stage == "index" else "pdf_worker"
            logger.warning("documind_ingestion stage=%s code=%s reason=unexpected_error", stage, code)
            raise SessionError(code) from None
        finally:
            with self._lock:
                if ref in self._known:
                    self._ingestions[key] = {
                        "status": "complete" if succeeded else "failed",
                        "code": code,
                    }


def render_upload(store, capability, ref, uploaded, state, ui):
    """Widget state avoids rehashing on each rerun; server ledger is authoritative."""
    if uploaded is None:
        return
    identity = (ref, uploaded.file_id)
    record = state.get("upload_feedback")
    fresh = not record or tuple(record["identity"]) != identity
    if not fresh:
        if not record.get("document"):
            record["document"] = hashlib.sha256(uploaded.getbuffer()).hexdigest()
        # Registry status wins if a rerun interrupted the UI after publication,
        # or Advanced processing completed a previously failed attempt. The
        # content hash only joins UI feedback; this read verifies ownership.
        docs = store.documents(capability, ref)
        if any(
            d["id"] == record["document"] and d["status"] == "indexed" for d in docs
        ):
            if record.pop("code", None):
                record["message"] = "This PDF is ready to chat."
            ui.status(record["message"], state="complete", expanded=False)
            return
        if not record.get("code"):
            return  # A deleted upload must not silently reappear on a rerun.
    retry = False
    try:
        failed = not fresh and bool(record.get("code"))
        with ui.status(record["message"] if failed else "Preparing your PDF…",
                       state="error" if failed else "running", expanded=failed) as status:
            if failed:
                retry = ui.button("Retry processing", key="retry_upload")
                if not retry:
                    return
            state["upload_feedback"] = {
                "identity": identity,
                "document": hashlib.sha256(uploaded.getbuffer()).hexdigest(),
                "code": "upload_interrupted",
                "message": "Processing was interrupted. Retry when ready.",
            }
            status.update(label="Preparing your PDF…", state="running", expanded=False)
            result = store.ingest_upload(
                capability,
                ref,
                uploaded.name,
                uploaded,
                retry=retry,
                progress=lambda label: status.update(label=label),
            )
            status.update(label="PDF ready", state="complete")
        state["upload_feedback"] = {
            "identity": identity,
            "document": result["id"],
            "message": f"{uploaded.name} is ready to chat."
            if not result["duplicate"]
            else "This PDF is already ready in your workspace.",
        }
        ui.rerun()
    except CloudError as error:
        if error.code in {"expired", "login"}:
            raise
        state["upload_feedback"].update(code=error.code, message=str(error))
        status.update(label=str(error), state="error", expanded=False)
        ui.rerun()  # Refresh the document list; the failed ledger prevents retries.
