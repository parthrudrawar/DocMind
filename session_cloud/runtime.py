"""Temporary storage authority. No remote document storage or model imports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from authenticated_cloud.auth import CloudError, digest
from session_cloud.index_workspace import IndexedWorkspace
from session_cloud.analysis_workspace import AnalysisWorkspace


class SessionError(CloudError):
    MESSAGES = {**CloudError.MESSAGES,
        "expired": "This temporary workspace expired. Sign in again to start an empty workspace.",
        "upload": "Use a PDF up to 100 MB, within the temporary workspace's remaining allowance.",
        "disk": "The temporary storage allowance is full. Remove a document or try later.",
        "history": "This temporary chat is full. Start a new chat.",
        "phase": "Retrieval indexing and AI processing are not enabled in this candidate milestone.",
        "pdf_invalid": "This PDF is malformed, repaired, empty, or could not be processed completely.",
        "pdf_encrypted": "Password-protected PDFs are not supported. Upload an unencrypted copy.",
        "pdf_pages": "PDF preparation supports at most 200 pages. Split this document before retrying.",
        "pdf_text": "This PDF exceeds a text or preparation-output limit. Split it before retrying.",
        "pdf_blank": "No searchable text was found. This document was not prepared or indexed.",
        "pdf_table": "Table extraction failed. No partial document was published; split or replace the PDF.",
        "pdf_ocr_unavailable": "This PDF needs OCR, which is not enabled or available in this candidate environment.",
        "pdf_ocr_limit": "This PDF exceeds the safe scanned-page or OCR image-size allowance.",
        "pdf_ocr_failed": "OCR did not complete. No partial document was prepared or indexed.",
        "pdf_time": "PDF preparation timed out. No partial document was published; split it before retrying.",
        "pdf_resource": "PDF preparation stopped at a resource or process guard. No partial document was published.",
        "pdf_worker": "PDF preparation could not be verified. No partial document was published.",
        "preparation_limit": "The PDF preparation allowance is exhausted. Failed attempts count; try the next UTC day.",
        "index_invalid": "The complete index could not be verified. No partial results were published. Reindex the document or remove it.",
        "index_resource": "Indexing or search reached a resource limit. Split the PDF; no partial index was published.",
        "index_time": "Indexing or search timed out. The previous complete index was preserved.",
        "index_model": "The original MiniLM model or tokenizer is unavailable. Search was not enabled.",
        "context_limit": "The complete evidence exceeds the safe answer-context limit. Select fewer documents or ask a narrower question.",
        "context_invalid": "The answer evidence could not be verified. No answer was generated.",
        "generation_budget": "The temporary demo AI allowance is exhausted. Failed calls count; try after the next UTC day. Restart limits also apply.",
        "generation_context": "The complete evidence and conversation exceed the safe AI context allowance. Start a new chat or select fewer PDFs.",
        "generation_invalid": "The AI response could not be verified. It was not saved as a completed answer.",
        "generation_incomplete": "The AI response was interrupted or reached its output limit. It was not saved. Retry explicitly if needed.",
        "generation_interrupted": "This request was interrupted or already attempted. It was not resent. Use Retry to make a new request.",
        "citation_invalid": "The answer's source references could not be verified. The answer was not saved.",
        "provider_rate": "Groq's rate limit was reached. No automatic retry was made. Please wait before retrying.",
        "provider_auth": "The AI provider is not configured correctly. Please contact the demo owner.",
        "provider_model": "The configured AI model is unavailable. Please contact the demo owner.",
        "provider_timeout": "The AI provider timed out. No incomplete answer was saved; retry explicitly if needed.",
        "provider_unavailable": "The AI service is temporarily unavailable. No incomplete answer was saved.",
        "analysis_invalid": "The analysis response could not be verified. No new result was saved.",
        "analysis_evidence": "The required indexed evidence is unavailable. Index the PDFs completely and retry explicitly; this is not a finding of no contradictions or gaps.",
        "analysis_input": "Generate a source guide first, or use a document with enough indexed summary text. No analysis request was sent.",
        "analysis_stale": "The analysis inputs changed. Generate a new result explicitly; outdated results are not shown.",
    }


@dataclass(frozen=True)
class Limits:
    upload_bytes: int = 100 * 1024 * 1024
    workspace_bytes: int = 128 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    workspaces: int = 2
    documents: int = 3
    chats: int = 5
    turns: int = 20
    ttl_seconds: int = 3600
    operation_seconds: int = 30
    user_uploads: int = 6
    global_uploads: int = 20
    user_preparations: int = 6
    global_preparations: int = 20


@dataclass(repr=False)
class Lease:
    ref: str
    owner: str
    expires: float


class WorkspaceStore(IndexedWorkspace, AnalysisWorkspace):
    """Opaque auth capabilities resolve to server-owned, per-session SQLite/files.

    AuthBroker revalidates Supabase on every public data operation. Only hashes of
    capabilities are retained here. Registry state never comes from session_state.
    A fresh process gets a new private root and cannot reopen an older root.
    """

    def __init__(self, auth, *, limits=Limits(), clock=time.time, monotonic=time.monotonic, base_dir=None, model_cache=None,
                 provider=None, generation_limits=None):
        if any(type(value) is not int or value <= 0 for value in vars(limits).values()):
            raise ValueError("Limits must be positive integers")
        self.auth, self.limits, self.clock, self.monotonic = auth, limits, clock, monotonic
        self.root = Path(tempfile.mkdtemp(prefix="documind-session-", dir=base_dir)).resolve()
        # Native FAISS archives include a random temporary suffix. Preserve the
        # full, server-owned path on Windows even in a deeply nested temp root.
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            self.root = Path("\\\\?\\" + str(self.root))
        self._marker = secrets.token_hex(32)
        (self.root / ".instance").write_text(self._marker, encoding="ascii")
        self._lock, self._work = threading.RLock(), threading.Lock()
        self._leases, self._retired, self._known, self._active = {}, {}, set(), set()
        self._day, self._uploads, self._user_uploads = int(clock() // 86400), 0, {}
        self._preparations, self._user_preparations = 0, {}
        self._indexing, self._user_indexing, self._queries, self._user_queries = 0, {}, 0, {}
        from session_cloud.index_service import IndexService
        # Contains only public model assets; never a workspace or credential.
        self._index_service = IndexService(model_cache or (self.root / "public-model-cache"))
        self.generation = None
        self.analyses = None
        if provider is not None:
            from session_cloud.generation import GenerationService
            from session_cloud.generation_budget import GenerationLimits
            self.generation = GenerationService(self, provider, limits=generation_limits or GenerationLimits())
            from session_cloud.analysis_service import AnalysisService
            self.analyses = AnalysisService(self, self.generation)

    def _folder(self, ref):
        if (ref not in self._known or not re.fullmatch(r"[a-f0-9]{32}", ref)
                or self.root.is_symlink() or getattr(self.root, "is_junction", lambda: False)()
                or (self.root / ".instance").read_text(encoding="ascii") != self._marker):
            raise SessionError("document")
        folder = self.root / ref
        for path in [folder, *folder.rglob("*")]:
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise SessionError("document")
        if folder.resolve().parent != self.root:
            raise SessionError("document")
        return folder

    def _bytes(self, folder):
        from session_cloud.disk_usage import disk_bytes
        return disk_bytes(folder)

    def _capacity(self, lease, extra=0):
        folder = self._folder(lease.ref)
        # Inspect only registered, private directories; never a caller-supplied path.
        total = sum(self._bytes(self._folder(ref)) for ref in self._known)
        if self._bytes(folder) + extra > self.limits.workspace_bytes or total + extra > self.limits.total_bytes:
            raise SessionError("disk")

    def _sweep(self):
        now = self.clock()
        for key, lease in list(self._leases.items()):
            if lease.expires <= now:
                del self._leases[key]
                self._retired[key] = now + 8 * 3600
        for key, until in list(self._retired.items()):
            if until <= now:
                del self._retired[key]
        live = {lease.ref for lease in self._leases.values()} | self._active
        for ref in self._known - live:
            # The exact target was created and registered by this process.
            folder = self._folder(ref)
            if folder.exists():
                shutil.rmtree(folder)
            self._known.remove(ref)
        if int(now // 86400) != self._day:
            self._day, self._uploads, self._user_uploads = int(now // 86400), 0, {}
            self._preparations, self._user_preparations = 0, {}
            self._indexing, self._user_indexing, self._queries, self._user_queries = 0, {}, 0, {}

    @contextmanager
    def _db(self, lease):
        connection = sqlite3.connect(self._folder(lease.ref) / "workspace.sqlite3", timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                raise SessionError("disk")
            connection.execute("PRAGMA max_page_count=4096")  # 16 MiB at SQLite's default page size
            connection.execute("PRAGMA cache_size=-256")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 100_000)
            with connection:
                yield connection
        except sqlite3.Error:
            raise SessionError("disk") from None
        finally:
            connection.close()

    def open(self, capability):
        user = self.auth.authorize(capability)
        key = digest(capability)
        with self._lock:
            self._sweep()
            if key in self._retired:
                raise SessionError("expired")
            lease = self._leases.get(key)
            if lease:
                if lease.owner != user.user_id:
                    raise SessionError("expired")
                return lease.ref
            if len(self._leases) >= self.limits.workspaces or len(self._retired) >= 128:
                raise SessionError("busy")
            lease = Lease(secrets.token_hex(16), user.user_id,
                          min(user.deadline, self.clock() + self.limits.ttl_seconds))
            if lease.expires <= self.clock():
                raise SessionError("expired")
            self._known.add(lease.ref)
            try:
                self._folder(lease.ref).mkdir(mode=0o700)
                with self._db(lease) as db:
                    db.executescript("""
                        CREATE TABLE documents(id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                            size INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('accepted','prepared','indexed')),
                            page_count INTEGER NOT NULL DEFAULT 0, preparation_metrics TEXT NOT NULL DEFAULT '{}',
                            upload_date TEXT NOT NULL);
                        CREATE TABLE prepared_pages(document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                            number INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(document_id,number));
                        CREATE TABLE chats(id TEXT PRIMARY KEY);
                        CREATE TABLE active_index(singleton INTEGER PRIMARY KEY CHECK(singleton=1), name TEXT NOT NULL, checksum TEXT NOT NULL);
                        CREATE TABLE turns(chat TEXT NOT NULL REFERENCES chats(id), id TEXT NOT NULL,
                            question TEXT NOT NULL, answer TEXT NOT NULL, metadata TEXT NOT NULL,
                            PRIMARY KEY(chat,id));
                        CREATE TABLE generation_requests(chat TEXT NOT NULL REFERENCES chats(id), id TEXT NOT NULL,
                            fingerprint TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','complete','failed')),
                            PRIMARY KEY(chat,id));
                        CREATE TABLE analysis_results(kind TEXT NOT NULL, target TEXT NOT NULL,
                            revision TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                            PRIMARY KEY(kind,target));
                        CREATE TABLE analysis_requests(id TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                            status TEXT NOT NULL CHECK(status IN ('running','complete','failed')), payload TEXT);
                    """)
                self._capacity(lease)
                self._leases[key] = lease
            except Exception:
                self._sweep()
                raise
            return lease.ref

    def check(self, capability, ref):
        user = self.auth.authorize(capability)
        with self._lock:
            self._sweep()
            lease = self._leases.get(digest(capability))
            if lease is None or lease.owner != user.user_id or lease.ref != ref:
                raise SessionError("expired")
            self._folder(lease.ref)
            return lease

    @contextmanager
    def _operation(self, capability, ref):
        lease = self.check(capability, ref)
        if not self._work.acquire(blocking=False):
            raise SessionError("busy")
        try:
            with self._lock:
                self.check(capability, ref)
                self._active.add(ref)
            yield lease
        finally:
            try:
                with self._lock:
                    self._active.discard(ref)
                    self._sweep()
            finally:
                self._work.release()

    def documents(self, capability, ref):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                return [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY rowid")]

    def upload(self, capability, ref, filename, stream):
        """Accept complete bytes only; never label unparsed PDFs as indexed."""
        if not isinstance(filename, str):
            raise SessionError("upload")
        name = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1])[:120]
        if not name.lower().endswith(".pdf"):
            raise SessionError("upload")
        with self._operation(capability, ref) as lease:
            with self._lock:
                if lease.owner not in self._user_uploads and len(self._user_uploads) >= 100:
                    raise SessionError("limit")
                count = self._user_uploads.get(lease.owner, 0)
                if count >= self.limits.user_uploads or self._uploads >= self.limits.global_uploads:
                    raise SessionError("limit")
                if len(self.documents(capability, ref)) >= self.limits.documents:
                    raise SessionError("limit")
                self._uploads += 1
                self._user_uploads[lease.owner] = count + 1
            folder = self._folder(ref)
            temporary = None
            destination, completed = None, False
            try:
                started, total, content_hash = self.monotonic(), 0, hashlib.sha256()
                with tempfile.NamedTemporaryFile(dir=folder, prefix="incoming-", suffix=".part", delete=False) as target:
                    temporary = Path(target.name)
                    while True:
                        block = stream.read(min(1024 * 1024, self.limits.upload_bytes - total + 1))
                        if not block:
                            break
                        if not isinstance(block, bytes) or (total == 0 and not block.startswith(b"%PDF-")):
                            raise SessionError("upload")
                        total += len(block)
                        if total > self.limits.upload_bytes:
                            raise SessionError("upload")
                        if self.monotonic() - started > self.limits.operation_seconds:
                            raise SessionError("busy")
                        with self._lock:
                            if self._leases.get(digest(capability)) is not lease or lease.expires <= self.clock():
                                raise SessionError("expired")
                            self._capacity(lease, len(block))
                        content_hash.update(block)
                        target.write(block)
                        target.flush()
                if not total:
                    raise SessionError("upload")
                with self._lock:
                    self.check(capability, ref)
                    doc_id = content_hash.hexdigest()
                    with self._db(lease) as db:
                        previous = db.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()
                        if previous:
                            return {"duplicate": True, "status": previous["status"], "id": doc_id}
                        destination = folder / (doc_id + ".pdf")
                        temporary.replace(destination)
                        try:
                            db.execute("INSERT INTO documents(id,filename,size,status,upload_date) VALUES(?,?,?,'accepted',?)", (doc_id, name, total,
                                datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()))
                            self._capacity(lease)
                        except Exception:
                            destination.unlink(missing_ok=True)
                            raise
                    completed = True
                    return {"duplicate": False, "status": "accepted", "id": doc_id}
            except SessionError:
                raise
            except Exception:
                raise SessionError("upload") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                if destination is not None and not completed:
                    destination.unlink(missing_ok=True)

    def prepare(self, capability, ref, doc_id, *, limits=None):
        """Publish all parsed pages in one transaction; this does not publish an index."""
        from session_cloud.parse_contract import ParseFailure, ParseLimits
        from session_cloud.parser_runner import run_parser

        limits = limits or ParseLimits()
        if not isinstance(limits, ParseLimits) or not isinstance(doc_id, str) or not re.fullmatch(r"[a-f0-9]{64}", doc_id):
            raise SessionError("document")
        with self._operation(capability, ref) as lease:
            with self._lock, self._db(lease) as db:
                row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
                if row is None:
                    raise SessionError("document")
                if row["status"] in {"prepared", "indexed"}:
                    return {"status": "prepared", "pages": row["page_count"],
                            "metrics": json.loads(row["preparation_metrics"])}
                self._capacity(lease, limits.output_bytes + 128 * 1024)
                count = self._user_preparations.get(lease.owner, 0)
                if ((lease.owner not in self._user_preparations and len(self._user_preparations) >= 100)
                        or count >= self.limits.user_preparations
                        or self._preparations >= self.limits.global_preparations):
                    raise SessionError("preparation_limit")
                self._preparations += 1
                self._user_preparations[lease.owner] = count + 1
            folder = self._folder(ref)
            output = folder / ("prepare-" + secrets.token_hex(16) + ".json")

            def active():
                with self._lock:
                    if self._leases.get(digest(capability)) is not lease or lease.expires <= self.clock():
                        raise SessionError("expired")
                    self._capacity(lease)

            try:
                # A cached model's idle RAM still counts against the host. The
                # global heavy-operation lock is held: release model residency
                # before starting the separately bounded native PDF parser.
                self._index_service.close()
                result = run_parser(folder / (doc_id + ".pdf"), output, limits, active)
                with self._lock:
                    self.check(capability, ref)  # Fresh Supabase verification before publication.
                    with self._db(lease) as db:
                        if not db.execute("SELECT 1 FROM documents WHERE id=? AND status='accepted'", (doc_id,)).fetchone():
                            raise SessionError("document")
                        db.executemany("INSERT INTO prepared_pages VALUES(?,?,?)",
                            ((doc_id, page["page_number"], json.dumps(page, ensure_ascii=False)) for page in result["pages"]))
                        db.execute("UPDATE documents SET status='prepared',page_count=?,preparation_metrics=? WHERE id=?",
                                   (len(result["pages"]), json.dumps(result["metrics"]), doc_id))
                        self._capacity(lease)
                return {"status": "prepared", "pages": len(result["pages"]), "metrics": result["metrics"]}
            except ParseFailure as error:
                raise SessionError(error.code) from None
            finally:
                output.unlink(missing_ok=True)

    def parsed_pages(self, capability, ref, doc_id):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                if not db.execute("SELECT 1 FROM documents WHERE id=? AND status IN ('prepared','indexed')", (doc_id,)).fetchone():
                    raise SessionError("document")
                return [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM prepared_pages WHERE document_id=? ORDER BY number", (doc_id,))]

    def delete_document(self, capability, ref, doc_id):
        with self._operation(capability, ref) as lease:
            if not isinstance(doc_id, str) or not re.fullmatch(r"[a-f0-9]{64}", doc_id):
                raise SessionError("document")
            with self._db(lease) as db:
                with self._lock:
                    self.check(capability, ref)
                    if not db.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
                        raise SessionError("document")
                self._delete_indexed(capability, ref, lease, db, doc_id)

    def new_chat(self, capability, ref):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                if db.execute("SELECT count(*) FROM chats").fetchone()[0] >= self.limits.chats:
                    raise SessionError("history")
                chat = secrets.token_hex(16)
                db.execute("INSERT INTO chats VALUES(?)", (chat,))
            self._capacity(lease)
            return chat

    def chats(self, capability, ref):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                return [row[0] for row in db.execute("SELECT id FROM chats ORDER BY rowid")]

    def history(self, capability, ref, chat):
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                if not db.execute("SELECT 1 FROM chats WHERE id=?", (chat,)).fetchone():
                    raise SessionError("document")
                messages = []
                for row in db.execute("SELECT * FROM turns WHERE chat=? ORDER BY rowid", (chat,)):
                    messages.extend([{"role": "user", "content": row["question"], "turn_id": row["id"]},
                                     {"role": "assistant", "content": row["answer"], "turn_id": row["id"],
                                      "meta": json.loads(row["metadata"])}])
                return messages

    def save_turn(self, capability, ref, chat, turn_id, question, answer, metadata):
        """Future engine boundary; text is never trusted as HTML by the renderer."""
        if (not all(isinstance(value, str) for value in (turn_id, question, answer))
                or not 1 <= len(turn_id) <= 64 or not 1 <= len(question) <= 4000
                or not 1 <= len(answer) <= 8000 or not isinstance(metadata, dict)
                or metadata.get("stream_error") or metadata.get("stream_interrupted")
                or metadata.get("completed") is False):
            raise SessionError("history")
        payload = json.dumps(metadata)
        if len(payload.encode()) > 20_000:
            raise SessionError("history")
        with self._lock:
            lease = self.check(capability, ref)
            with self._db(lease) as db:
                if not db.execute("SELECT 1 FROM chats WHERE id=?", (chat,)).fetchone():
                    raise SessionError("document")
                if db.execute("SELECT 1 FROM turns WHERE chat=? AND id=?", (chat, turn_id)).fetchone():
                    return False
                if db.execute("SELECT count(*) FROM turns WHERE chat=?", (chat,)).fetchone()[0] >= self.limits.turns:
                    raise SessionError("history")
                db.execute("INSERT INTO turns VALUES(?,?,?,?,?)", (chat, turn_id, question, answer, payload))
                self._capacity(lease)
                return True

    def logout(self, capability):
        with self._lock:
            key = digest(capability)
            if self._leases.pop(key, None) is not None:
                self._retired[key] = self.clock() + 8 * 3600
            self._sweep()
        return self.auth.logout(capability)
