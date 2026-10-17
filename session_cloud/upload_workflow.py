"""Authorized, content-idempotent automatic ingestion; failures require a click."""

import hashlib
import time

from authenticated_cloud.auth import CloudError


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
                progress("Reading pages and tables")
                self.prepare(capability, ref, key[1])
            progress("Building searchable evidence")
            self.index_document(capability, ref, key[1])
            self.check(capability, ref)
            succeeded = True
            return {"status": "indexed", "id": key[1], "duplicate": False}
        except CloudError as error:
            code = error.code
            raise
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
    retry = False
    if not fresh and record.get("code"):
        ui.warning(record["message"])
        retry = ui.button("Retry processing", key="retry_upload")
    if not fresh and not retry:
        if not record.get("code"):
            # Revalidate ownership before displaying a retained completion.
            docs = store.documents(capability, ref)
            if any(
                d["id"] == record["document"] and d["status"] == "indexed" for d in docs
            ):
                ui.success(record["message"])
        return
    state["upload_feedback"] = {
        "identity": identity,
        "code": "upload_interrupted",
        "message": "Processing was interrupted. Retry when ready.",
    }
    try:
        with ui.status("Preparing your PDF…", expanded=False) as status:
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
        ui.warning(str(error))
