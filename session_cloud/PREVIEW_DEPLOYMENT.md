# Separate DocuMind preview — do not replace the existing app

## Deployment coordinates

- Repository: `parthrudrawar/DocMind`
- Branch: `feat/temporary-cloud-workspaces` (never `main`)
- Main file path: `session_cloud/streamlit_app.py`
- Python: **3.12**, explicitly selected in Advanced settings
- Dependencies: `session_cloud/requirements.txt` (13 exact direct pins)
- Configuration: this feature branch's root `.streamlit/config.toml`
- Secrets template: `session_cloud/secrets.toml.example`; actual values only in
  the **new preview app's** Secrets UI. No actual Secrets file belongs in Git.

The existing entrypoint already bootstraps repository-relative imports and the
bundled OAuth component. There is no second/simplified RAG entrypoint to maintain.
All seven analysis features and the original-style seven-tab UI remain intact.
No Docker, Redis, FastAPI, Celery, Pinecone, Cloudinary, service-role key or cloud
write key is required by this preview. The durable daily allowance requires the
preview-only `preview_quota.sql` setup described below. The old application remains
in the repository for rollback, but its entrypoint/dependencies are not selected.

## Preview daily AI allowance (local preparation; not applied automatically)

Before using this candidate, review and run `session_cloud/preview_quota.sql`
once in the preview Supabase project's SQL Editor. It creates only
`preview_ai_usage` and `preview_reserve_ai()`, with no changes to existing
`cloud_*` tables, Auth configuration or production functions. Do not run the old
`authenticated_cloud/schema.sql` for this setup. No generated values or new
Secrets are needed. Do not apply this to a production project without approval.

First run the numbered SELECT blocks in `session_cloud/preview_quota_preflight.sql`
separately in the preview project's SQL Editor. They inspect catalog metadata and
permissions only, and never invoke `preview_reserve_ai()` or read user records.
Expect all prerequisite booleans true, no target relation/type collisions, no
same-name functions in `public`, and client roles with public-schema usage but
without superuser/RLS-bypass privileges. Stop
on collisions or unexpected permissions; do not drop existing objects or reset
counters. The checks cannot identify whether you selected the intended project;
verify that in the dashboard. No migration is applied by these checks.

The definer function uses `search_path = pg_catalog, pg_temp`, with temporary
objects last and all application relations explicitly schema-qualified.

Supabase Free includes PostgreSQL and database RPCs. One row per authenticated
account stores the current UTC day and request count. The function locks that
row, derives identity from `auth.uid()` and the day from database time, and
permits at most 25 reservations/day. There is no browser-supplied owner/count,
direct table access, refund, reset or delete endpoint. Rows do not grow by day.
Refresh, logout, new workspaces and app restarts cannot reset these counts.

Every outgoing Groq call reserves first, including analysis, decomposition,
quote fallback and follow-ups. A chat action can use several requests. Provider
401/429/timeouts and incomplete streams retain their reservation. Failed or
uncertain quota RPCs block Groq without retrying or falling back to memory;
an uncertain RPC can conservatively spend a reservation without a Groq call.
Missing SQL produces the generic usage-verification unavailable message.
The existing shared request, token, lifetime, time and concurrency caps still
apply and may stop usage before 25. Those extra safety counters remain local.
Groq provider rate limits have a separate provider-limit message.

The SQL and tests are prepared locally; live Supabase enforcement still requires
the setup above and preview verification. No cloud database was modified here.

Community Cloud searches dependencies beside the entrypoint before the repository
root, and runs from the repository root. Therefore do not select
`authenticated_cloud/streamlit_app.py`, install that directory's requirements, or
copy its Secrets. Root configuration is branch-specific; main still has its
original 2 MB acceptance setting.

## Create the SECOND app — manual, not performed by this change

1. Confirm the feature branch commit is visible on GitHub. Keep the current app's
   repo/branch/entrypoint/Secrets unchanged. Do not merge to main.
2. Open Community Cloud, choose **Create app**, then deploy from GitHub.
   Use the coordinates above. Choose a NEW available subdomain, for example
   `documind-parthss-preview`; do not edit or reuse the live app.
3. Set Python **3.12** in Advanced settings. Fill only the new app's Secrets using
   the six names in `secrets.toml.example`:
   `FREE_PLANS_CONFIRMED` (boolean `true`), `SUPABASE_URL` (HTTPS project URL,
   no trailing slash), `SUPABASE_PUBLISHABLE_KEY` (publishable, not service role),
   `APP_URL` (exact new HTTPS app URL **with trailing slash**), `GROQ_API_KEY`
   (authorized free account key), `GROQ_MODEL` (`openai/gpt-oss-120b`).
   Never paste credentials into GitHub, logs, screenshots or this document.
4. In Supabase Auth > URL Configuration, ADD the exact preview redirect alongside
   the existing live redirect. Do not replace the Site URL or remove existing
   allowlist entries. The broker appends `?oauth_state=<random-state>` to APP_URL;
   use the exact preview origin/path allowlist entry, not a broad wildcard domain.
   Keep Google provider enabled and Google's authorized redirect to
   `https://<project-ref>.supabase.co/auth/v1/callback` unchanged. Do not create
   paid resources. These are manual provider changes; none were performed here.
5. Create only this new app. In its build log confirm Python 3.12 and the candidate
   requirements (FastEmbed, FAISS, BM25), not Pinecone/Cloudinary dependencies.
   Review any startup diagnostics without sharing private URLs/query strings.
6. Use two Google accounts in separate browsers and only non-sensitive fixtures.
   Verify empty workspaces; separate uploads; Store > Prepare > Index; selected
   document chat; genuine incremental Groq text; citations/excerpts/pages;
   history/New Chat; all seven explicit analysis actions; deletion/reindex
   invalidation; logout, expiry and restart. Changing another user's IDs must
   fail, never return their data. Test OAuth PKCE, refresh and expired sessions.
7. Interrupt a stream and an analysis. Confirm incomplete results are not stored
   as completed. Test a concurrent second operation and quota exhaustion without
   resetting allowances. Stop preview testing on any privacy/security failure.
8. Measure total parent + child memory, CPU, startup/download time and response
   latency in the preview. Start with small digital PDFs. Increase size gradually;
   successful 100 MB transfer does NOT establish complete 100 MB indexing.
   Do not repoint the live application until separately approved after these tests.

## Linux/native and cold-start considerations

Pinned native packages have Python 3.12 Linux x86-64 wheels compatible with the
documented Debian 11 base (glibc >=2.28 requirements are satisfied by that base).
A cross-platform binary-only dependency resolution is not a Linux execution test.
The current host is Windows; actual Linux subprocess limits/native loads must be
checked in the separate preview. No source builds or GPU/Torch packages are needed.
OCR is OFF. Do not rename the optional OCR examples to active requirements or
packages files until separately tested; scanned/mixed PDFs fail explicitly.

The original embedding and cross-encoder models download lazily to separate
public-only temporary caches, each capped at 160 MiB. Cold downloads and first
inference share the existing 120-second request deadline. A slow/blocked model
host causes a safe error, not fallback retrieval or partial indexing. A later
explicit retry can reuse a valid partial cache; no automatic AI retry is added.
On Linux, Hugging Face snapshot symlinks count as small link entries, with blob
data counted once; escaping, dangling and directory links/junctions fail closed.
Private workspace accounting and link rejection are unchanged.

Only immutable model state is shared. One heavy operation/model worker and one
generation gate are shared across users. Before parsing a new PDF, the idle model
worker is released to avoid stacking its memory on the parser. Linux model guard:
3 GiB virtual-address limit and sampled 640 MiB resident limit; parser: 512 MiB
virtual limit, 60 seconds CPU / 120 seconds wall. Child environments exclude
provider secrets. These guards are not a hard whole-app memory limit or a native
code security sandbox. Actual low-memory Cloud behavior remains unverified.

## Storage, quotas and free-plan limitations

- Two active workspaces/process, each bound server-side to a verified Supabase
  identity AND opaque authenticated capability. Client workspace IDs alone never
  authorize data. Same-account independent sign-ins are separate workspaces.
- Up to 3 files, 100 MiB/file accepted, 128 MiB/workspace, 256 MiB tracked private
  storage/process. Models have separate caches. 200 pages, 1 MB extracted text,
  1,200 child chunks and snapshot/time limits can reject smaller files.
- Streamlit buffers uploads before application validation. Concurrent 100 MB
  transfers and parent/native memory can exceed free hosting limits. A 100 MB
  processing guarantee is NOT made. No silent truncation or partial publication.
- One-hour workspace TTL, bounded by auth expiry; logout/expiry revoke access
  before ownership-scoped cleanup. Active operations finish/cancel before removal.
  Sweep runs on application activity. Browser disconnect is not instant deletion.
- Restart loses all workspace mappings/history. Old inaccessible temporary bytes
  may persist until host recycling. No exact erasure or document recovery promise.
- Shared conservative provider limits: 20 calls/account/day, 60/process/day,
  200/process lifetime; 120k/180k reserved-token bounds, including failed calls,
  follow-ups and analysis. Provider free limits may reject sooner. Counters are
  process-local and reset on restart, not durable public anti-abuse protection.
- Stay on existing free plans. No upgrade, trial, billing activation or paid
  fallback exists here. Provider quotas may be shared with the current app even
  when using a different API key. Authorize a tiny live test budget separately.

This is a restricted portfolio **preview candidate**, not verified production.
Remaining acceptance gates: real Google OAuth/refresh/logout, real Groq streaming
and answer/analysis grounding, two-browser isolation, Linux cold model loads,
total memory/concurrency and representative PDF-size tests. OCR parity is omitted
until separately approved and verified.

## Verification reference and rollback

Read `PREVIEW_READINESS.md` for this preparation's actual test evidence and
`PHASE3C_REPORT.md` for historical feature parity and retrieval measurements.
Do not interpret mocked provider success as live Groq/OAuth success.

Rollback is leaving the existing app on main untouched; the old commit remains
`dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c` and local branch
`rollback/authenticated-cloud-dec268c`. Stopping/rebooting ONLY the preview can
lose preview data; it does not require deleting any provider resources. There
are no destructive cleanup commands or migrations in these deployment steps.

Official documentation:

- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies
- https://docs.streamlit.io/deploy/streamlit-community-cloud/status
