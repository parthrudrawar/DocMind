# Preview header, daily allowance and PDF/table verification

Date: 2026-10-18. Branch: `feat/temporary-cloud-workspaces`.
Prepared on the feature branch; no actual Secrets change or cloud database
operation was performed by the agent. Publication status is reported separately.

## Changes

- The OAuth component requests a 64-pixel frame even after successful login.
  The preview now places it in a named container and hides that container's
  wrapper only when the authenticated workspace renders. The handshake remains
  mounted; sign-in assets shared with the old application were not edited.
  Main top padding and heading/subtitle spacing were reduced using existing
  semantic/keyed selectors. Sidebar, navigation, chat viewport and composer
  behavior were retained. Desktop/mobile browser visual verification is pending.
- The daily account request allowance is 25. Chat, follow-ups, decomposition,
  quote fallback and all seven analysis features reserve through one method
  before contacting Groq. Failed provider attempts retain their reservation.
  Provider 401/429/timeouts remain separate from the account daily-limit error.
- `SupabaseDailyQuota` revalidates the server-held authentication capability,
  checks its stable account identity, and calls a fixed authenticated RPC. It
  sends no claimed owner, date or count. Invalid responses, missing setup,
  transport failures and uncertain reservations block AI without retry or a
  process-memory fallback. An uncertain reservation can conservatively count
  even though no provider call was sent.
- The additive `preview_quota.sql` creates one row per account and a no-argument
  `preview_reserve_ai()` function. It derives identity from `auth.uid()`, locks
  the account row, and computes the current UTC date after acquiring the lock.
  It permits at most 25 reservations for that account/day. RLS is enabled and
  direct table access is revoked. There is no client refund/reset/delete API.
  Refresh, logout, workspace recreation and process restart cannot reset the
  database row. No daily row accumulation or scheduled cleanup is needed.
- Supabase Free's existing database can hold this small registry. No paid
  service, service-role key, cloud write key or new Secret is required. The
  existing shared request, token, lifetime, timeout and concurrency safeguards
  remain, and may stop requests before the daily allowance. These extra safety
  counters remain process-local; only the account daily request cap is durable.
- The original PDF/table parsing, chunking, MiniLM, FAISS/BM25 fusion,
  cross-encoder reranking, expansion and citation validation are unchanged.
  One prompt instruction was added to retain source values, signs, decimal
  precision, units and column associations when a Markdown table is requested.
  Existing sanitized Markdown rendering and citation controls are reused.

## PDF and table evidence

A generated, non-sensitive two-page PDF with a ruled financial table is tested
through automatic ingestion, repeat-upload deduplication, native hybrid search,
native reranking/context preparation, mocked Groq SSE, citation validation and
HTML rendering. Prepared page two preserves the table's headers and exact rows:

| Year | Revenue (USD millions) | Margin (%) |
| --- | --- | --- |
| 2023 | 1,250.50 | -2.75 |
| 2024 | 1,450.00 | 3.25 |

The rendered fixture was visually checked for layout and legibility. The test
checks value preservation, progressive token callbacks, complete table HTML,
escaped malicious HTML, citation document/filename/quote/page provenance and
cross-user rejection. The original chunker can combine short content from
pages one and two. In that case the citation correctly reports that page range;
the test does not fabricate a narrower page-two reference.

This is real PDF parsing and real native retrieval/reranking, with a deterministic
mock provider. It does not verify a real Groq model's factual or numerical
accuracy. No real provider request or private PDF was used.

## Verification

- Focused quota, PDF and generation run: 72 passed, 2 skipped in 34.64 seconds.
  Two additional local-budget boundary/uncertain-RPC regressions were added
  afterward and are included in the final complete run below.
- Final publication review suite: **490 passed, 3 skipped in 450.71 seconds**,
  including the hardened SQL search-path and catalog-only preflight regressions.
  The skips are the two PostgreSQL execution tests unavailable in this sandbox and the existing
  Windows symlink-cleanup test without symlink privileges. No skipped check is
  counted as passed.
- Ruff and Git whitespace checks passed.
- Python 3.12 imports: 53 candidate modules plus native libraries passed.
- Dependency checks: all 13 deployment pins and 44 direct constraints passed.
- Tracked/candidate credential and forbidden-artifact scan passed; actual Secrets
  and ignored private-data files were not read. Explicit allowlisted staging was
  blocked by local `.git/index.lock` permissions; no new commit or push was made.

The initial focused tests had incorrect expectations about the adapter's handled
errors and the original chunker's page ranges. Those test expectations were
corrected. A broader run was interrupted after finding older assertions for the
generic budget code; these now expect the dedicated daily account-limit code,
while retaining checks that no extra provider call was sent. These interrupted
or pre-correction runs are not reported as passes.

## Setup and remaining limits

Follow-up security hardening: the migration function's fixed search path is now
`pg_catalog, pg_temp`. The read-only `preview_quota_preflight.sql` checks catalog
objects, every matching function signature, trusted migration prerequisites and
effective client permissions without invoking the counter. Relevant local quota
and security checks passed (25 passed, 2 database tests deliberately deselected
so no migration was executed). See `PREVIEW_DEPLOYMENT.md` for expected results.

The user subsequently reported successful migration in the preview Supabase
project and verification that the table, function and RLS exist. This is
user-reported evidence, not an independent database check by the agent. Do not
rerun the initial CREATE migration or reset existing counters. Fresh preview
projects still require the one-time setup in `PREVIEW_DEPLOYMENT.md`. No existing
`cloud_*` tables or RPCs are changed by that migration. Unavailable quota storage
still blocks the candidate's AI requests.

Two real PostgreSQL tests are provided for concurrent reservations, UTC rollover,
RLS/direct API rejection, new-runtime/relogin behavior and no refund. The local
Windows sandbox prevents `initdb` restricted-token startup; those tests are
explicitly skipped here, not passed. They must run in an environment permitting
an isolated PostgreSQL process, followed by live preview Supabase verification.
No existing PostgreSQL cluster or external database was contacted.

Remaining checks are live Supabase quota persistence/concurrency, OAuth/browser
layout at desktop/mobile sizes, real Groq table generation and cloud memory/time
behavior. The 100 MiB upload ceiling is still acceptance only: existing page,
text, chunk, disk, memory, wall-time and OCR restrictions remain. Arbitrary or
scanned PDF tables cannot be promised; OCR stays off and no partial index is
published after a processing failure.

## Files changed

- `.gitignore` (allow the two new regression sources)
- `session_cloud/theme.py`, `session_cloud/streamlit_app.py`
- `session_cloud/daily_quota.py`, `session_cloud/preview_quota.sql` (new)
- `session_cloud/preview_quota_preflight.sql` (new, read-only catalog checks)
- `session_cloud/generation_budget.py`, `session_cloud/generation.py`
- `session_cloud/analysis_service.py`, `session_cloud/comparison_service.py`
- `session_cloud/runtime.py`, `session_cloud/original_prompts.py`
- `session_cloud/evaluate_generation.py` (explicit mock quota for offline tests)
- `session_cloud/README.md`, `session_cloud/PREVIEW_DEPLOYMENT.md`
- `session_cloud/secrets.toml.example` (comments only; no credentials)
- `tests/test_daily_quota.py`, `tests/test_pdf_tables_e2e.py` (new)
- `tests/test_generation.py`, `tests/test_guides.py`
- `tests/test_comparisons.py`, `tests/test_exploration.py`
- `session_cloud/UI_QUOTA_TABLE_REPORT.md` (this report)

Free-plan reference checked during implementation:
https://supabase.com/pricing (PostgreSQL and 500 MB database on the Free plan).
