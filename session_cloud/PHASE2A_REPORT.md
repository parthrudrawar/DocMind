# Phase 2A — verified complete-document PDF preparation

Branch: `feat/temporary-cloud-workspaces`. Work remains uncommitted; nothing was
pushed or deployed. Main, rollback commit, original Docker sources, deployed
`authenticated_cloud` code/config/requirements and Secrets are unchanged.

This is the first verified Phase 2 milestone, not completion of Phase 2 or RAG
feature parity. The explicit split prevents partially restored retrieval from
being presented as the original answer-quality pipeline.

## Reuse and adaptations

`original_pdf.py` copies the original ParsedPage metadata and page-text,
paragraph, overlap and table-extraction helpers. Provenance SHA256 is recorded
in `source_manifest.json`. Structural extraction order and table serialization
are retained. Non-text image blocks are excluded from paragraph text.

`bounded_pdf.py` replaces orchestration only: sequential complete-document
parsing, encrypted/repaired/malformed/blank rejection, explicit limits and OCR
dispatch. Original silent page substitution/table/OCR failures are deliberately
replaced with whole-document rejection. Optional OCR retains 2x RGB rendering
and psm=6, with explicit pixel, count and timeout guards. OCR remains disabled
and real Tesseract OCR is not verified in this environment.

`parse_worker.py`, `process_limits.py`, `parser_runner.py` are new adapters for
an isolated interpreter and bounded process lifecycle. No inherited provider
credentials are passed, stdout/stderr are discarded, only fixed error codes
reach the UI, and child/OCR processes are terminated after timeout or revocation.
These are resource boundaries, not a privileged native-code security sandbox.

`runtime.py` adds per-workspace prepared pages and preparation metrics in SQLite.
All pages and status change commit together; failure leaves the original PDF
accepted with no prepared-page rows. Reads, preparation and deletion enforce
the existing server-owned identity/capability binding. Deletion cascades parsed
pages. Duplicate uploads preserve their actual preparation state.
Preparation attempts are reserved under the server lock: six/account/day and
twenty/process/day. Failed attempts count; fresh login/workspace creation does
not reset the account's allowance. These counters reset on UTC day or restart.

`streamlit_app.py` exposes Prepare PDF, truthful state messages and prepared
document selection. Files are never labeled indexed/searchable at this stage.
The original chunker remains available unchanged in the clean repository, but
MiniLM-tokenizer integration/chunk publication is reserved for milestone 2B.
No alternate approximate chunker or simplified RAG engine was introduced.

## Executed verification

Combined suite: **191 passed, 1 skipped** in 40.43 seconds:

- 28 new preparation tests, including real native PDF/table extraction in real
  child processes and atomic SQLite publication.
- 29 Phase 1 checks passed; the pre-existing real symlink test was skipped because
  Windows cannot create symlinks in this environment.
- 134 existing startup/configuration diagnostics tests passed.

New checks cover exact table cells, original page numbers/ordering, table text
not duplicated as prose, malformed/encrypted/blank/mixed-scanned PDFs, page/text
limits, table/page exceptions, OCR pixel guard before allocation, timeout and
actual child memory exhaustion, process termination on revocation, suppressed
child diagnostics, stripped provider environment, protocol validation, foreign
workspace/document access, rollback after publication failure, deletion and two
independent Streamlit AppTest document-selection views, plus global and per-account
preparation budgets that cannot be bypassed by creating a new workspace.

Ruff and syntax/import checks passed. Real local native imports passed for
PyMuPDF 1.26.7, NumPy 1.26.4, ONNX Runtime 1.31.0, FAISS CPU 1.9.0.post1,
rank-bm25 0.2.2 and the FastEmbed embedding/cross-encoder classes (0.7.4).
No models were instantiated/downloaded and no provider calls were made.

## Measured sample, not a capacity guarantee

A one-page generated digital PDF prepared successfully through the real child
and SQLite boundary. Child-reported peak RSS: **154,865,664 bytes (147.69 MiB)**;
parsing wall time **0.125 seconds**; CPU **0.0156 seconds**. Wall/CPU measure parsing
only, excluding cold process startup. RSS is the child's lifetime peak, including
imports. The Streamlit host, upload buffers and future model loads are excluded.
They do not establish 100 MB or OCR processing feasibility.

## Native compatibility and remaining blockers

Official PyMuPDF documentation lists Linux wheels and Python stable-ABI support,
but warns that glibc compatibility varies by wheel release:
https://pymupdf.readthedocs.io/en/latest/installation.html
Streamlit supports OS packages through packages.txt:
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies

Only Windows CPython 3.12 native execution was tested here. Linux resource guards
and Community Cloud wheel installation/runtime still require real verification.
Tesseract/pytesseract are not installed in the test environment; optional OCR
files are prepared only. Do not copy them into the active deployment yet.
The earlier NumPy/PyArrow runtime incompatibility remains to be resolved in the
candidate's full dependency lock; it is not exercised by these native UI cards.

Still missing before Phase 3: original MiniLM tokenizer/embedding, bounded chunk
publication, FAISS/BM25/RRF, cross-encoder reranking, parent/neighbor context,
original decomposition/prompts, real Groq streaming, citation/follow-up validation,
history integration and AI quotas. Retrieval quality and source-citation accuracy
have therefore **not** been measured; page/table preparation accuracy has.

Next milestone 2B: restore and test those original ingestion/retrieval components
against a known-answer multi-PDF corpus, with a compatible native dependency lock
and measured model memory. Milestone 2C then connects original generation,
streamed events, validated citations/follow-ups and temporary conversation history.
All analysis features remain user-triggered and deferred. No public deployment
or Phase 3 approval is implied by these unit/native tests.
