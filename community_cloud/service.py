"""Bounded in-memory RAG adapter. No app settings, databases, Redis or workers."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

from app.ingestion.chunker import Chunker
from app.reasoning.citations import iter_citation_markers
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document
from app.retrieval.rank_fusion import reciprocal_rank_fusion

MODEL = "openai/gpt-oss-120b"
NO_EVIDENCE = "I could not find enough evidence in the selected documents to answer this question."
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 3
MAX_PAGES = 10
MAX_TOTAL_PAGES = 20
MAX_CHUNKS = 200
MAX_TEXT_CHARS = 80_000
SESSION_SECONDS = 3600
MAX_SESSIONS = 8
SESSION_AI_LIMIT = 10
GLOBAL_AI_LIMIT = 40
GLOBAL_UPLOAD_LIMIT = 20


class DemoError(Exception):
    """Safe, fixed messages only. Never put provider exceptions or secrets here."""

    MESSAGES = {
        "configuration": "The demo is not configured. Please contact its owner.",
        "password": "Incorrect password.",
        "locked": "Sign-in is temporarily locked. Wait five minutes and try again.",
        "expired": "Your demo session expired. Sign in again; temporary data is no longer accessible.",
        "busy": "The demo is busy. Please try again shortly.",
        "upload": "Use readable, unencrypted text PDFs: up to 3 files, 2 MB each, 10 pages each and 20 pages total.",
        "text": "This PDF has no readable text or is too large to process. Scanned PDFs need OCR and are not supported here.",
        "upload_limit": "The demo's upload allowance has been reached. Clearing documents does not reset usage.",
        "question": "Select your indexed PDFs and enter a question of at most 500 characters (1,000 UTF-8 bytes).",
        "ai_limit": "The demo's AI allowance has been reached. No further provider request was made.",
        "cooldown": "Please wait at least one minute between AI requests across this shared demo.",
        "provider_auth": "The AI service rejected its configuration. Please notify the owner; do not repeatedly retry.",
        "provider_limit": "Groq's free allowance is temporarily unavailable. Please try later.",
        "provider": "The AI service is temporarily unavailable. Please try later.",
        "citations": "The answer did not contain valid source references. No unverified answer is displayed.",
    }

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.MESSAGES[code])


@dataclass(frozen=True, repr=False)
class Credentials:
    password: str = field(repr=False)
    groq_key: str = field(repr=False)

    @classmethod
    def from_secrets(cls, values):
        password, key = values.get("DEMO_PASSWORD"), values.get("GROQ_API_KEY")
        if (values.get("GROQ_FREE_PLAN_CONFIRMED") is not True
                or not isinstance(password, str) or not 16 <= len(password) <= 128
                or not isinstance(key, str) or not re.fullmatch(r"gsk_[A-Za-z0-9_-]+", key)):
            raise DemoError("configuration")
        return cls(password, key)


@dataclass(repr=False)
class Workspace:
    expires: float
    documents: dict = field(default_factory=dict)
    chunks: list = field(default_factory=list)
    vectors: object = None
    messages: list = field(default_factory=list)
    ai_used: int = 0
    uploads_used: int = 0


class LocalEmbedder:
    """Same MiniLM model as Docker, lazy and single-threaded; no reranker."""

    def __init__(self):
        import tempfile
        from pathlib import Path
        from fastembed import TextEmbedding

        self.model = TextEmbedding(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            cache_dir=str(Path(tempfile.gettempdir()) / "documind-cc-models"),
            threads=1,
        )

    def embed(self, texts):
        import numpy as np

        vectors = np.asarray(list(self.model.embed(texts, batch_size=8)), dtype="float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)


class DemoService:
    """Server-authoritative token registry and process-wide conservative budgets.

    Only this service is cached, not query results/uploads. Every resource operation
    resolves its opaque login token again; UI session_state is not authorization.
    State is deliberately temporary and is lost when the process restarts.
    """

    def __init__(self, credentials: Credentials, *, clock=time.time,
                 embedder_factory=LocalEmbedder, complete=None):
        self._clock = clock
        self._salt = secrets.token_bytes(16)
        self._password_hash = self._password_digest(credentials.password)
        self._key = credentials.groq_key
        self._embedder_factory = embedder_factory
        self._embedder = None
        self._complete = complete
        self._lock = threading.RLock()
        self._work = threading.Lock()
        self._sessions: dict[str, Workspace] = {}
        self._failures = deque()
        self._locked_until = 0
        self._day = int(clock() // 86400)
        self._ai_used = self._uploads_used = 0
        self._last_ai = float("-inf")

    def _password_digest(self, value):
        return hashlib.scrypt(value.encode(), salt=self._salt, n=16384, r=8, p=1)

    @staticmethod
    def _token_hash(token):
        if not isinstance(token, str) or len(token) != 64:
            raise DemoError("expired")
        return hashlib.sha256(token.encode()).hexdigest()

    def _sweep(self):
        now = self._clock()
        for key in [key for key, ws in self._sessions.items() if ws.expires <= now]:
            del self._sessions[key]
        day = int(now // 86400)
        if day != self._day:
            self._day, self._ai_used, self._uploads_used = day, 0, 0

    def _workspace(self, token):
        self._sweep()
        workspace = self._sessions.get(self._token_hash(token))
        if workspace is None:
            raise DemoError("expired")
        return workspace

    def login(self, password):
        with self._lock:
            self._sweep()
            now = self._clock()
            if now < self._locked_until:
                raise DemoError("locked")
            while self._failures and self._failures[0] <= now - 300:
                self._failures.popleft()
            if (not isinstance(password, str) or not 1 <= len(password) <= 128
                    or not hmac.compare_digest(self._password_digest(password), self._password_hash)):
                self._failures.append(now)
                if len(self._failures) >= 5:
                    self._locked_until = now + 300
                    self._failures.clear()
                raise DemoError("password")
            if len(self._sessions) >= MAX_SESSIONS:
                raise DemoError("busy")
            token = secrets.token_hex(32)
            self._sessions[self._token_hash(token)] = Workspace(now + SESSION_SECONDS)
            return token

    def logout(self, token):
        with self._lock:
            self._workspace(token)
            del self._sessions[self._token_hash(token)]

    def snapshot(self, token):
        with self._lock:
            ws = self._workspace(token)
            return {"documents": [dict(doc) for doc in ws.documents.values()],
                    "messages": [dict(message) for message in ws.messages],
                    "ai_remaining": SESSION_AI_LIMIT - ws.ai_used}

    def clear_documents(self, token):
        with self._operation(token) as ws:
            with self._lock:
                self._workspace(token)
                ws.documents.clear()
                ws.chunks.clear()
                ws.vectors = None
                ws.messages.clear()
                # Counters and expiry intentionally survive clearing documents.

    @contextmanager
    def _operation(self, token):
        with self._lock:
            workspace = self._workspace(token)
        if not self._work.acquire(blocking=False):
            raise DemoError("busy")
        try:
            yield workspace
        finally:
            self._work.release()

    def _embed(self, texts):
        if self._embedder is None:
            self._embedder = self._embedder_factory()
        return self._embedder.embed(texts)

    def ingest(self, token, uploads):
        """Accept a bounded batch transactionally; never write PDFs/indexes to disk."""
        import numpy as np

        with self._operation(token) as ws:
            if not uploads or len(uploads) > MAX_DOCUMENTS:
                raise DemoError("upload")
            pending, skipped, seen = [], [], set(ws.documents)
            total_pages = sum(doc["pages"] for doc in ws.documents.values())
            for filename, data in uploads:
                if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES or not data.startswith(b"%PDF-"):
                    raise DemoError("upload")
                name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
                name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:120]
                if not name.lower().endswith(".pdf"):
                    raise DemoError("upload")
                doc_id = hashlib.sha256(data).hexdigest()
                if doc_id in seen:
                    skipped.append(name)
                    continue
                seen.add(doc_id)
                if len(ws.documents) + len(pending) >= MAX_DOCUMENTS:
                    raise DemoError("upload")
                with self._lock:
                    self._workspace(token)
                    if ws.uploads_used >= 6 or self._uploads_used >= GLOBAL_UPLOAD_LIMIT:
                        raise DemoError("upload_limit")
                    # Failed parsing/indexing attempts also consume allowance.
                    ws.uploads_used += 1
                    self._uploads_used += 1
                pages = self._parse_pdf(data)
                total_pages += len(pages)
                if total_pages > MAX_TOTAL_PAGES:
                    raise DemoError("upload")
                chunks = [chunk for chunk in Chunker(target_tokens=220, overlap_tokens=35,
                          parent_tokens=800).chunk_document(doc_id, pages) if chunk.indexable]
                pending.append(({"id": doc_id, "filename": name, "pages": len(pages)}, chunks))
            if not pending:
                return {"accepted": 0, "skipped": skipped}
            new_chunks = [chunk for _, chunks in pending for chunk in chunks]
            if not new_chunks or len(ws.chunks) + len(new_chunks) > MAX_CHUNKS:
                raise DemoError("text")
            try:
                vectors = self._embed([chunk.text for chunk in new_chunks])
            except Exception:
                raise DemoError("busy") from None
            with self._lock:
                self._workspace(token)
                ws.vectors = vectors if ws.vectors is None else np.vstack((ws.vectors, vectors))
                ws.chunks.extend(new_chunks)
                ws.documents.update({doc["id"]: doc for doc, _ in pending})
            return {"accepted": len(pending), "skipped": skipped}

    @staticmethod
    def _parse_pdf(data):
        import fitz

        try:
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if not pdf.is_pdf or pdf.needs_pass or not 1 <= len(pdf) <= MAX_PAGES:
                    raise DemoError("upload")
                pages, text_size = [], 0
                for number, page in enumerate(pdf, 1):
                    paragraphs = [str(block[4]).strip() for block in sorted(
                        page.get_text("blocks"), key=lambda block: (block[1], block[0]))
                        if len(block) > 6 and block[6] == 0]
                    text = "\n\n".join(paragraphs)
                    text_size += len(text)
                    if text_size > MAX_TEXT_CHARS:
                        raise DemoError("text")
                    pages.append(SimpleNamespace(page_number=number, text=text,
                                 paragraphs=paragraphs, table_blocks=[], is_ocr=False))
                if not any(page.text.strip() for page in pages):
                    raise DemoError("text")
                return pages
        except DemoError:
            raise
        except Exception:
            raise DemoError("upload") from None

    def _retrieve(self, ws, question, document_ids):
        import faiss
        import numpy as np
        from rank_bm25 import BM25Okapi

        selected = set(document_ids)
        if not selected or not selected <= set(ws.documents):
            raise DemoError("question")
        positions = [i for i, chunk in enumerate(ws.chunks) if chunk.doc_id in selected]
        chunks = [ws.chunks[i] for i in positions]
        vectors = np.ascontiguousarray(ws.vectors[positions], dtype="float32")
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        scores, ids = index.search(self._embed([question]), min(30, len(chunks)))
        dense = [(chunks[i].chunk_id, float(score)) for i, score in zip(ids[0], scores[0]) if i >= 0]
        sparse_scores = BM25Okapi([chunk.text.lower().split() for chunk in chunks]).get_scores(question.lower().split())
        sparse = sorted(((chunk.chunk_id, float(score)) for chunk, score in zip(chunks, sparse_scores)
                         if score > 0), key=lambda pair: pair[1], reverse=True)[:30]
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        return [by_id[key] for key, _ in reciprocal_rank_fusion(dense, sparse, top_k=6)]

    @staticmethod
    def _evidence(ws, chunks):
        evidence, citations, remaining = [], [], 4000
        for chunk in chunks:
            number = len(citations) + 1
            name = ws.documents[chunk.doc_id]["filename"]
            header = f"[{number}] {name}, pages {chunk.page_number}-{chunk.page_end}\n"
            available = remaining - len(header.encode()) - 2
            if available < 120:
                break
            excerpt = chunk.text.encode()[:available].decode("utf-8", errors="ignore")
            block = header + excerpt + "\n\n"
            remaining -= len(block.encode())
            evidence.append(block)
            citations.append({"number": number, "filename": name,
                              "page_number": chunk.page_number, "page_end": chunk.page_end,
                              "exact_quote": excerpt})
        return "".join(evidence), citations

    def ask(self, token, question, document_ids):
        if not isinstance(question, str) or not question.strip() or len(question) > 500 or len(question.encode()) > 1000:
            raise DemoError("question")
        with self._operation(token) as ws:
            with self._lock:
                self._workspace(token)
                if not document_ids or not set(document_ids) <= set(ws.documents):
                    raise DemoError("question")
                if ws.ai_used >= SESSION_AI_LIMIT or self._ai_used >= GLOBAL_AI_LIMIT:
                    raise DemoError("ai_limit")
                if self._clock() - self._last_ai < 60:
                    raise DemoError("cooldown")
                ws.ai_used += 1
                self._ai_used += 1
                self._last_ai = self._clock()
            try:
                chunks = self._retrieve(ws, question, document_ids)
            except DemoError:
                raise
            except Exception:
                raise DemoError("busy") from None
            evidence, citations = self._evidence(ws, chunks)
            messages = [{"role": "system", "content": (
                "Answer only from the provided PDF evidence. Cite every factual claim with [number]. "
                "Use concise Markdown and no HTML. Never invent references. "
                f"If evidence is insufficient, reply exactly: {NO_EVIDENCE}\n{UNTRUSTED_DOCUMENT_POLICY}"
            )}, {"role": "user", "content": f"{wrap_untrusted_document(evidence)}\nQuestion: {question}"}]
            answer = self._generate(messages)
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
                raise DemoError("provider")
            answer = answer.strip()
            markers = list(iter_citation_markers(answer))
            allowed = {citation["number"] for citation in citations}
            if any(number not in allowed for number, _, _ in markers) or (not markers and answer != NO_EVIDENCE):
                raise DemoError("citations")
            for number, start, end in reversed(markers):
                answer = answer[:start] + f"[{number}]" + answer[end:]
            used = {number for number, _, _ in markers}
            result = {"role": "assistant", "text": answer,
                      "citations": [citation for citation in citations if citation["number"] in used]}
            with self._lock:
                self._workspace(token)
                ws.messages.extend(({"role": "user", "text": question}, result))
                ws.messages = ws.messages[-20:]
            return dict(result)

    def _generate(self, messages):
        try:
            if self._complete is not None:
                return self._complete(messages)
            from groq import Groq

            # No retry, tools, secondary provider, paid fallback or billing setup.
            with Groq(api_key=self._key, timeout=45, max_retries=0) as client:
                result = client.chat.completions.create(
                    model=MODEL, messages=messages, temperature=0, max_completion_tokens=1024)
                return result.choices[0].message.content
        except DemoError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = "provider_auth" if status in (401, 403) else "provider_limit" if status == 429 else "provider"
            raise DemoError(code) from None
