# Phase 1 — temporary workspaces and original UI integration

Implementation: `C:\Users\rudra\Documents\Codex\documind-cloud-publish`, branch
`feat/temporary-cloud-workspaces`. Changes are uncommitted and have not been
pushed. Both `main` and `rollback/authenticated-cloud-dec268c` remain at
`dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`.

The existing deployment entrypoint, original Docker project, Streamlit Secrets
and cloud resources are unchanged. This candidate is not ready to replace the
current working application.

## Files

- `session_cloud/runtime.py`: authoritative capability/identity binding,
  temporary directories, SQLite documents/chats/turns, quotas, upload storage,
  expiry, scoped cleanup and logout.
- `session_cloud/auth_client.py`: Auth-only HTTP transport and configuration;
  reuses the existing AuthBroker without document SQL/write-key dependencies.
- `session_cloud/adapter.py`: local UI contract, scope changes, bounded output,
  per-event authorization and interrupted-stream handling for the future engine.
- `session_cloud/streamlit_app.py`: separate preview entrypoint, sidebar,
  conversation selection, temporary uploads/documents and seven tabs.
- `session_cloud/chat_view.py`, `chat_state.py`, `theme.py`: copied original chat
  layout/state and CSS. Changes are limited to package imports and a disabled
  composer option while processing is unavailable.
- `session_cloud/login.py`: copied existing browser PKCE handshake with its
  component passed explicitly. Existing AuthBroker is unchanged.
- `session_cloud/__init__.py`, `source_manifest.json`, `requirements.txt`,
  `secrets.toml.example`, `README.md`, this report: packaging, provenance,
  dummy-only settings and limitations.
- `tests/test_session_cloud.py`: temporary-data and mocked-provider tests.
- `.gitignore`: allow only the additional regression test source.

## Verified behavior

Phase 1 tests: **29 passed, 1 skipped**. The skip is a real-filesystem symlink
test: Windows does not permit test symlink creation in this environment.

Existing startup diagnostics: all **134 cases passed across runs**. One import
subprocess hit a host timeout during the combined run (162 passed, 1 failed,
1 skipped); both standalone import cases passed on the focused rerun. No timeout
or security assertion was relaxed.

Ruff, syntax/import checks, `pip check`, whitespace checks, source/credential
artifact scan and deployment-preservation checks passed. Nothing is staged.
Original UI reference hashes match their pre-change values.

Tests cover separate users and separate sessions of the same account, foreign
workspace/document/chat IDs, changed identities and invalid tokens, logout,
expiry, restart isolation, account upload quotas surviving logout, storage and
concurrency limits, no partial PDF records, atomic/idempotent turn history,
stream interruption and reauthorization, escaped HTML/Markdown, and two independent
Streamlit AppTest views with seven tabs and New Chat/rerun behavior.

The original Google broker's PKCE exchange, token refresh and logout were exercised
through mocked Auth HTTP responses. No real Google or other provider request ran.

A generated **100 MiB byte fixture** was stored completely. This verifies the
temporary storage path, not a browser upload, valid PDF parsing, indexing, OCR,
AI generation, or successful 100 MB processing on Community Cloud.

## Restored and pending

The original chat renderer, source/activity/follow-up display code, dark CSS and
Smart Question card renderer are reused. New Chat, conversation selection,
temporary document storage/deletion and the seven-tab shell are integrated.
The chat composer and analysis actions remain explicitly disabled. No fake
progress, generated questions, indexed status, or AI results are shown.

**Still pending:** original parser/OCR, MiniLM/token-aware ingestion, FAISS/BM25
indexes, cross-encoder reranking, expanded context, original generation/prompts,
live streamed answers/citations/follow-ups, and original analysis tools. SQLite
history and the event adapter are tested boundaries, not connected AI features.
There is no RAG feature-parity claim.

## Remaining risks and Phase 2

Streamlit uploads consume RAM before service admission. Model/parser memory and
CPU limits, killable parsing, scanned PDFs, malicious PDFs, processing deadlines,
and whole-document index publication require Phase 2 implementation and tests.
The isolated environment also exposed a NumPy/PyArrow runtime incompatibility
when a dataframe was attempted, despite successful dependency metadata checks;
Phase 1 uses native document cards. Validate native dependency compatibility
before restoring the full processing stack.

Per-process budgets reset on restart; idle cleanup is activity-triggered, and
unreachable roots from old processes may persist until host recycling. Live
Google callback/refresh/logout, two real browser users, mobile layout and
Community Cloud resource measurements remain unverified.

Next: adapt the original bounded parsing/chunking/embedding and local hybrid
retrieval into this workspace authority, preserve reranking/context/prompts,
connect real streamed events and history, then restore user-triggered analyses.
Keep main and the deployed entrypoint unchanged until replacement acceptance
tests pass and the owner approves deployment.
