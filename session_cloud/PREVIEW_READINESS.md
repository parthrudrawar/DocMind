# Preview preparation evidence — 2026-10-17

Verdict: **candidate prepared for a separate preview, NOT production verified**.
No Streamlit app, cloud resource, production Secret or billing setting was changed.

## Changes in this preparation

- Designate existing `session_cloud/streamlit_app.py` and its adjacent 13-pin
  `requirements.txt` as the preview entrypoint/dependencies. No simplified engine,
  new provider or replacement UI. Preserve all seven analysis features.
- Apply the reviewed preview config to this feature branch's root
  `.streamlit/config.toml`: 100 MB acceptance, CORS/XSRF on, static serving off,
  generic error details, original dark theme. Main retains its original config.
- Fix public Hugging Face cache accounting in `disk_usage.py` / `index_service.py`:
  Linux snapshots link to blobs; following links doubled model sizes and could
  falsely exceed the unchanged 160 MiB/cache guard. Count internal link entries
  plus blobs once; reject escaping/dangling/directory links and junctions.
  Private workspace accounting/authorization/link rejection are unchanged.
- Add nine preview regressions in `tests/test_preview_deployment.py`, covering
  packaging, config, remote-provider independence, cache accounting, invalid
  links and bounded retry/fail-closed behavior. POSIX link metadata is emulated
  on Windows; this is not a live Linux filesystem test.
- Fix the existing dummy worker in `tests/test_session_retrieval.py` to publish
  its response through a closed `.part` file + atomic rename, matching the real
  worker. The initial full run exposed its incomplete-JSON/file-lock race.
  No production protocol or security check was weakened.
- Remove trailing blank lines from seven copied candidate reference modules;
  prompts and algorithm statements are unchanged.
- Add `PREVIEW_DEPLOYMENT.md`, update candidate README/example and allow the new
  dummy-only test in `.gitignore`. Commit also includes the previously uncommitted
  Phase 1–3C candidate code/reports/tests, now explicitly approved for publication.

## Verification

- Focused preview tests: **9 passed**; then preview + corrected real-child
  diagnostic fixture: **10 passed**.
- Initial complete suite: **417 passed, 1 skipped, 1 failed**, 337.40 seconds.
  Failure: non-atomic dummy response publication described above, subsequently
  corrected. Final complete rerun: **418 passed, 1 skipped, 0 failed**, 554.41
  seconds. Skip: existing Windows OS symlink-creation permission test. Native
  model tests ran with actual cached MiniLM weights; provider calls were mocked.
- Repository-wide Ruff: passed. Candidate/remaining deployment source syntax
  and dummy TOML validation: passed. Git whitespace check: passed after EOF fixes.
- All 13 exact installed direct pins verified, including NumPy 2.4.6.
  Standalone `-I -B` Python 3.12 smoke: **48 candidate modules**, native imports
  and **44 applicable direct dependency constraints** passed. No Pinecone,
  Cloudinary or legacy provider module was loaded.
- Binary-only dependency resolution for Python 3.12 / Linux x86-64 manylinux_2_28:
  **passed, 58 total packages**, using the public PyPI index, no source builds.
  This proves a compatible resolution, NOT clean Linux installation or execution.
- Import-root coverage passed after adding Pillow to the diagnostic allowlist;
  Pillow is a Streamlit dependency, and pytesseract imports are optional OCR only.
- Credential-pattern/prohibited-artifact scans of nonignored source and actual
  staged blobs passed. No real Secrets, `.env`, PDFs, DBs, model assets, API keys
  or production files are staged. Pattern scans are not a universal secret proof.
- Main, rollback and deployed `authenticated_cloud`, `community_cloud`, `app`
  and `frontend` sources remain unchanged. Original Docker source was not edited.

## Resource evidence and limits

Historical Phase 3C native fixture results remain in `PHASE3C_EVALUATION.json`:
reranked corpus worker peak **416.21 MiB**, first evidence request **3.266 s**,
median **4.9845 s**, max **6.031 s**; original/candidate recall@6 and MRR@6 both
1.0 for twelve fixture queries, same selected/expanded order. All-seven-tools
two-small-PDF fixture used real cached native models but **mocked Groq**.
These are Windows synthetic measurements, not total Streamlit memory, cold
network initialization, live provider latency, or a new Linux performance run.

Limits remain: two workspaces, one heavy operation and one generation at a time;
100 MiB/file accepted, 128 MiB/workspace, 256 MiB tracked private disk; separate
160 MiB public model caches; parser 512 MiB/120 s wall, model sampled 640 MiB/120 s
with 3 GiB Linux address-space ceiling. Idle models close before parsing.
Streamlit HTTP upload buffering and parent/native allocations are NOT bounded by
these child guards. Large/concurrent uploads may exceed free-host resources.
No 100 MB processing guarantee, silent truncation or partial publication.

## Remaining live gates — none claimed passed

1. Clean Linux/Python 3.12 native imports, subprocess limits, anonymous cold model
   downloads (within deadline), total parent/child memory and actual free-host fit.
2. Separate preview callback allowlist and new-app Secrets; real Google PKCE,
   refresh, expiry, logout and two-browser isolation. No provider changes made.
3. Real Groq Free streaming, usage, answer/analysis quality and citation grounding.
   Provider requests in automated tests are mocked; no live AI allowance consumed.
4. Non-sensitive digital/image-heavy/large PDFs, resource exhaustion and concurrent
   uploads. OCR stays OFF; scanned/mixed documents requiring OCR fail explicitly.
5. Logout/expiry/interruption/reindex/delete/restart checks in the actual preview.
   Unit/native isolation tests do not establish live browser isolation.

Cleanup revokes access first and removes only registered inactive owned roots on
activity. Restart uses a new root and loses access; leftover inaccessible bytes
can persist until host recycling. Quotas are per-owner/process but not durable
across process restart; this remains a restricted portfolio preview, not a
public-service anti-abuse guarantee. No Pinecone/Cloudinary or paid fallback.

Follow `PREVIEW_DEPLOYMENT.md` to create only the second app. Do not merge, change
the current deployment, move rollback or delete provider resources.
