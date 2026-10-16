"""Synchronous account-owned RAG; the original Docker pipeline is untouched."""
from __future__ import annotations

import hashlib
import re
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from app.ingestion.chunker import Chunker
from app.reasoning.citations import iter_citation_markers
from app.reasoning.prompt_safety import UNTRUSTED_DOCUMENT_POLICY, wrap_untrusted_document
from authenticated_cloud.auth import CloudError
from authenticated_cloud.providers import MAX_BYTES, scope
from community_cloud.service import DemoError, DemoService, LocalEmbedder, NO_EVIDENCE


class Generator:
    """Reuse existing bounded Groq generation, without its password/session layer."""
    _generate = DemoService._generate

    def __init__(self, key, complete=None):
        self._key, self._complete = key, complete


class CloudService:
    def __init__(self, auth, store, groq_key, *, embedder_factory=LocalEmbedder, complete=None):
        self.auth, self.db, self.store = auth, auth.db, store
        self._generator = Generator(groq_key, complete)
        self._factory, self._model = embedder_factory, None
        self._work = threading.Lock()

    @contextmanager
    def _operation(self, capability):
        user = self.auth.authorize(capability)
        if not self._work.acquire(blocking=False):
            raise CloudError("busy")
        try:
            yield user
        except CloudError:
            raise
        except Exception:
            raise CloudError("unavailable") from None
        finally:
            self._work.release()

    def _documents(self, user):
        rows = self.db.call("/rest/v1/cloud_documents?select=*&order=created_at.asc", token=user.access, method="GET")
        if any(row.get("owner") != user.user_id for row in rows):
            raise CloudError("document")
        return {row["id"]: row for row in rows}

    def snapshot(self, capability):
        user = self.auth.authorize(capability)
        self.db.rpc(user.access, "bootstrap")
        documents = self._documents(user)
        turns = self.db.call("/rest/v1/cloud_turns?select=*&order=id.asc&limit=50", token=user.access, method="GET")
        if any(turn.get("owner") != user.user_id for turn in turns):
            raise CloudError("document")
        return {"documents": list(documents.values()), "turns": turns}

    def _owned(self, user, doc_id, ready=True):
        scope(user.user_id, doc_id)
        document = self._documents(user).get(doc_id)
        if not document or (ready and document["state"] != "ready"):
            raise CloudError("document")
        return document

    def _embed(self, texts):
        if self._model is None:
            self._model = self._factory()
        return self._model.embed(texts)

    def ingest(self, capability, filename, data):
        with self._operation(capability) as user:
            if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES or not data.startswith(b"%PDF-")
                    or not isinstance(filename, str)):
                raise CloudError("pdf")
            name = re.sub(r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").rsplit("/", 1)[-1])[:120]
            if not name.lower().endswith(".pdf"):
                raise CloudError("pdf")
            doc_id = hashlib.sha256(data).hexdigest()
            previous = self._documents(user).get(doc_id)
            if previous:
                if previous["state"] != "ready":
                    raise CloudError("busy")
                return {"duplicate": True}
            try:
                pages = DemoService._parse_pdf(data)
            except DemoError:
                raise CloudError("pdf") from None
            chunks = [chunk for chunk in Chunker(target_tokens=220, overlap_tokens=35,
                       parent_tokens=800).chunk_document(doc_id, pages) if chunk.indexable]
            if not 0 < len(chunks) <= 200:
                raise CloudError("pdf")
            record = self.db.rpc(user.access, "begin", p_id=doc_id, p_filename=name,
                                 p_pages=len(pages), p_chunks=len(chunks))
            if not record.get("created"):
                raise CloudError("busy")
            # Ledger precedes ALL remote writes. Failure leaves a recoverable pending record.
            vectors = self._embed([chunk.text for chunk in chunks])
            user = self.auth.authorize(capability)
            current = self._owned(user, doc_id, ready=False)
            if current["state"] != "pending":
                raise CloudError("document")
            self.store.upload_pdf(user.user_id, doc_id, data)
            self.store.upsert(user.user_id, doc_id, chunks, vectors)
            user = self.auth.authorize(capability)
            self.db.rpc(user.access, "finish", p_id=doc_id)
            return {"duplicate": False}

    def delete(self, capability, doc_id):
        with self._operation(capability) as user:
            self._owned(user, doc_id, ready=False)
            # Hides the source immediately. Failed deletion is retried, not forgotten.
            self.db.rpc(user.access, "delete", p_id=doc_id)
            self.store.delete_vectors(user.user_id, doc_id)
            self.store.delete_pdf(user.user_id, doc_id)
            user = self.auth.authorize(capability)
            self.db.rpc(user.access, "forget", p_id=doc_id)

    def download(self, capability, doc_id):
        with self._operation(capability) as user:
            self._owned(user, doc_id)
            # Count remote PDF downloads against the same conservative global AI budget.
            self.db.rpc(user.access, "ai", p_ids=[doc_id])
            data = self.store.download_pdf(user.user_id, doc_id)
            self.auth.authorize(capability)
            return data

    def ask(self, capability, question, document_ids):
        if (not isinstance(question, str) or not question.strip() or len(question) > 500
                or len(question.encode()) > 1000 or not isinstance(document_ids, list)
                or not 1 <= len(document_ids) <= 3 or any(not isinstance(item, str) for item in document_ids)
                or len(set(document_ids)) != len(document_ids)):
            raise CloudError("document")
        with self._operation(capability) as user:
            docs = self._documents(user)
            for key in document_ids:
                self._owned(user, key)
            selected = {key: docs[key] for key in document_ids}
            self.db.rpc(user.access, "ai", p_ids=document_ids)
            matches = self.store.query(user.user_id, selected, self._embed([question])[0])
            chunks = [SimpleNamespace(doc_id=match["metadata"]["doc_id"], text=match["metadata"]["text"],
                      page_number=int(match["metadata"]["page"]), page_end=int(match["metadata"]["page_end"])) for match in matches]
            evidence, citations = DemoService._evidence(SimpleNamespace(documents=selected), chunks)
            messages = [{"role": "system", "content": (
                "Answer only from the PDF evidence. Cite each factual claim with [number]. "
                "Use concise Markdown, no HTML. Never invent sources. "
                f"If evidence is insufficient, reply exactly: {NO_EVIDENCE}\n{UNTRUSTED_DOCUMENT_POLICY}")},
                {"role": "user", "content": f"{wrap_untrusted_document(evidence)}\nQuestion: {question}"}]
            try:
                answer = self._generator._generate(messages)
            except DemoError:
                raise CloudError("answer") from None
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
                raise CloudError("answer")
            answer = answer.strip()
            markers = list(iter_citation_markers(answer))
            if (any(number not in {cite['number'] for cite in citations} for number, _, _ in markers)
                    or (not markers and answer != NO_EVIDENCE)):
                raise CloudError("answer")
            for number, start, end in reversed(markers):
                answer = answer[:start] + f"[{number}]" + answer[end:]
            used = {number for number, _, _ in markers}
            citations = [cite for cite in citations if cite["number"] in used]
            user = self.auth.authorize(capability)
            self.db.rpc(user.access, "save_turn", p_ids=document_ids, p_question=question,
                        p_answer=answer, p_citations=citations)
            return {"text": answer, "citations": citations}
