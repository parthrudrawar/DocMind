# Preview ingestion and Chat regression fixes

Date: 2026-10-18. Branch: `feat/temporary-cloud-workspaces`.
Status: local candidate only; no commit, push, deployment or Secrets changes.

## Findings and changes

1. The automatic workflow already called storage, preparation and indexing in
   sequence. An indexing exception left the PDF correctly `prepared`, with no
   searchable partial snapshot. Its failed-attempt ledger prevented subsequent
   reruns from spending more attempts automatically. The old caption made this
   look like a normal manual-indexing step. The UI now describes incomplete
   processing, shows one status panel and offers an explicit retry that resumes
   from prepared pages. Successful uploads still finish indexing automatically.
   SHA-256 content deduplication and the server ledger remain authoritative.
2. Automatic ingestion ran in the sidebar before the main tabs were rendered.
   Advanced processing exceptions also escaped before Chat was built. All PDF
   processing now runs after the page and composer are rendered, with expected
   errors handled in the reserved sidebar progress container. A successful
   Advanced retry reconciles the matching upload's earlier failure message.
   The same authorized registry check also handles a rerun immediately after
   publication, before upload feedback was saved. UI content hashes only join
   feedback to server-owned documents; they do not authorize access.
   A failed attempt refreshes the page once so the retained PDF and its Delete
   action appear immediately. That refresh does not repeat processing.
3. Chat was explicitly disabled when no indexed source was selected. Its
   composer now stays available; submitting while generation is unavailable
   displays guidance without making an AI request or saving an unanswered turn.
   A pending response still disables duplicate submissions and retains Cancel.
4. The source multiselect used both `default=` and Session State assignment.
   Session State is now its only value source. New ready sources are included,
   deleted sources are filtered out, and intentional deselections persist.
5. `index_resource` previously covered contention, worker exits, storage/text/
   chunk limits, resident memory, cache limits and some supervisor I/O failures.
   It did not establish an out-of-memory condition. Contention now has a distinct
   `index_busy` code; malformed snapshot contents are distinguished from size
   exhaustion. No resource ceiling was increased or removed.
6. The supplied live failure was:

   ```text
   documind_index stage=worker_request reason=supervisor_io native_code=11
   ```

   Error 11 is `EAGAIN`. Both accounting helpers explicitly raised it after
   three immediate `FileNotFoundError` retries. Atomic `.part`/snapshot/cache
   renames could invalidate those scans while the worker was healthy. The
   supervisor then mislabeled the accounting failure as a resource limit.
   Complete rescans now have a short backoff and a strict one-second/50-attempt
   bound. Unknown totals still fail closed; disappearing files are never just
   ignored. Final model-cache totals are verified even for fast worker replies.

## Native failure evidence and diagnostics

The supplied error matches the reproduced transient-filesystem-accounting
failure; it does not establish an out-of-memory error. The old log does not distinguish the workspace scan from
the public-model-cache scan. Both helpers contained the same three-retry failure
mechanism and both are fixed. Real cloud execution after this patch remains
unverified; the exact live directory was not observed.

Before the fix, two deterministic regressions using real atomic renames failed
with `BlockingIOError: [Errno 11]` in the two helpers. Both pass after bounded
backoff was added. A real-PDF/native-worker regression also reproduces the old
three-attempt failure during model-cache accounting, verifies no partial index,
then successfully retries with bounded rescans and retrieves the correct
document/page evidence. Upload and preparation counters do not reset or repeat.
Persistent churn remains bounded and fails closed. Tests also verify that cache
limits cannot be skipped when a worker finishes between samples.

A generated, non-sensitive PDF is exercised with the real parser, MiniLM,
FAISS/BM25 and worker process. A deliberately reduced one-chunk test limit
reproduces `index_resource` with `worker_stage=chunking reason=chunk_capacity`.
The separate chunk-limit test verifies that the document stays prepared, no partial snapshot remains,
an explicit retry under the normal limit succeeds, retrieved citations retain
the document/page, and deletion followed by reupload builds a fresh complete
index. This verifies an actual resource guard independently of the EAGAIN bug.

Worker diagnostics now record only closed-vocabulary stages/reasons and bounded
numeric values. An internal sidecar preserves the last stage if a worker exits
before returning JSON. Raw stderr, exception messages, paths, document content,
user/workspace identifiers and credentials are never forwarded to logs.

Log format (placeholders, not a live result):

```text
documind_index stage=... worker_stage=... reason=... code=... exit=... rss_bytes=... memory_limit_bytes=... elapsed_ms=... native_code=...
documind_ingestion stage=index code=...
```

`worker_exit` identifies process termination, not its cause. It alone does not
prove OOM. `exit=None` can occur when a persistent worker returns a guarded error
while still alive, before its supervisor closes it.

If indexing still fails after an approved preview rollout, inspect only the
`documind_index` line in Streamlit Manage app -> logs. Accounting stages now
distinguish `workspace_accounting` and `model_cache_accounting`; persistent churn
has `reason=filesystem_churn`. Omit full logs, credentials, tokens, private text,
filenames and identifiers. Extended diagnostics are local until that rollout.

## Verification

- Targeted pinned-runtime regression run: 16 passed in 101.35 seconds.
- Additional Advanced-success feedback regression: 1 passed in 12.31 seconds.
- Final UI/rerun/feedback checks: 43 passed, 3 native PDF tests deselected, in
  19.97 seconds. This includes post-publication recovery and failed-document
  list refreshes without repeating processing.
- Atomic-rename reproduction: 2 expected failures before the fix; bounded-retry
  and persistent-churn checks then passed (4 passed).
- Post-fix integration: 31 passed in 122.92 seconds, including native PDF
  ingestion/retrieval, filesystem-churn recovery, all seven analyses and cache
  safety. The native EAGAIN reproduction/recovery case took 16.40 seconds; that
  is a local test duration including forced failure and retry, not cloud latency.
- Full confirmation suite: **464 passed, 1 skipped in 343.56 seconds**. The skip
  is the Windows symlink-cleanup test because symlink privileges are unavailable.
- Final pre-commit review rerun: **464 passed, 1 skipped in 849.43 seconds**,
  using the complete `tests` directory. Initial root-level discovery encountered
  inaccessible unrelated temporary folders and stopped during collection; those
  folders were preserved, no tests were excluded, and temporary output was
  redirected outside the repository for the successful run.
- The preceding full run finished with 461 passed, 3 failed, 1 skipped in
  836.56 seconds: one native snapshot-publication permission error (errno 13)
  and two Streamlit AppTest timeouts. Those three cases passed unchanged in an
  isolated rerun (3 passed in 18.13 seconds). The subsequent complete suite above
  passed without product-code or test changes. The initial failures remain
  recorded here; their transient host-level causes were not proven.
- Ruff: passed repository-wide.
- Imports: 52 candidate modules plus native libraries passed on Python 3.12.
- Dependencies: all 13 exact pins and 44 direct constraints passed.
- Credential/artifact scan: passed; empty temporary probe files were checked by
  size without reading locked files. No secrets or private artifacts were staged.
- Pre-commit scan: all 120 tracked/candidate source files passed credential and
  forbidden-artifact checks. Git metadata writes remain blocked by local access
  permissions (`.git/index.lock` and `.git/FETCH_HEAD`). No ACLs were changed,
  no commit was created, and the fixes were not pushed.

Earlier interrupted/incorrect-runtime runs are not passing verification. A fresh
isolated test environment restored the pinned NumPy 2.4.6, FAISS 1.12.0,
FastEmbed 0.9.0 and Protobuf 5.29.5 without editing deployment requirements or the
Docker project. Test documents, databases and downloaded test packages are in
isolated directories outside this repository. Providers use dummy credentials
and mocked transports; no real Groq completion was requested.

## Limits and remaining verification

The 100 MiB upload ceiling is acceptance, not a promise that every such PDF can
be indexed. The existing page/text/chunk limits, 640 MiB model-worker cap,
120-second index deadline, cache and snapshot caps, disk budgets, quotas,
serialized processing and atomic publication are preserved. OCR remains off.
Host/Streamlit overhead is additional to the worker cap. Linux cold downloads,
actual cloud memory pressure and live browser recovery remain unverified.
Processing is synchronous; controls render first, while server-side events can
queue during a running operation. No background UI worker was introduced.

Hybrid retrieval, reranking, prompts, citation validation, streaming generation,
all seven analysis tabs and OAuth were preserved. Main, rollback, the original
Docker project and existing deployments were not changed.

## Files changed

- `.gitignore` (allow the focused new regression test only)
- `session_cloud/streamlit_app.py`
- `session_cloud/chat_view.py`
- `session_cloud/upload_workflow.py`
- `session_cloud/runtime.py`
- `session_cloud/index_contract.py`
- `session_cloud/disk_usage.py`
- `session_cloud/index_engine.py`
- `session_cloud/index_service.py`
- `session_cloud/index_worker.py`
- `tests/test_preview_regressions.py` (new)
- `tests/test_ux_integration.py`
- `tests/test_answer_context.py`
- `tests/test_pdf_preparation.py`
- `tests/test_session_cloud.py`
- `session_cloud/PREVIEW_REGRESSION_REPORT.md` (this report)
