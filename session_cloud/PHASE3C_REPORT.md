# Phase 3C — consolidated integration and preview handoff

Candidate: `C:\Users\rudra\Documents\Codex\documind-cloud-publish`,
`feat/temporary-cloud-workspaces`. Implementation is local and uncommitted.
Original Docker checkout, deployed entrypoint/configuration, Secrets, cloud
resources, main and rollback are not changed. No deployment, push, billing,
provider completion request or deletion of existing user data was performed.
Deletion/corruption tests use newly generated, isolated synthetic fixtures only.

## Restored remaining analyses

The actual original graphs, routes and frontend were read without importing
their global settings, SQL sessions or provider clients. Reference hashes are in
`source_manifest.json`. `original_exploration.py` adapts only data acquisition;
the original system/human prompts and sampling rules are retained.

| Feature | Original algorithm/settings retained | Candidate integration |
|---|---|---|
| Smart Questions | All indexed PDFs; up to four L2 sections/document, fallback two L3 paragraphs, first 300 chars/chunk, ` | ` join; first ten documents; optional topic; five-string JSON prompt; legacy question-object compatibility; category `smart`; temperature 0.5, max output 1024 | Original full-width cards and Ask action; only explicit generation. Ask resolves the current cached question on the server, selects the whole indexed collection as the original API did, and queues the existing genuine streaming Chat pipeline. |
| Insights | All indexed PDFs; first five L2 sections, fallback three L3 paragraphs, first 400 chars/chunk, space join; first twelve documents; optional topic; pattern/trend/anomaly JSON and supporting filenames; temperature 0.4, max output 1200 | Original colored cards, badges and supporting-document fields; reverse filename-to-ID mapping uses only owned documents. Unknown/foreign filenames reject the result instead of becoming unverified source links. |
| Timeline | All indexed PDFs in upload order; first L1 summary 600 chars plus first two L3 paragraphs 400 chars each; one provider call/document; original publication-date/upload-year fallback prompt; temperature 0.2, max output 1500 | Original year bands and importance-colored event cards. Exact original chronological sort and lowercase-first-80-character claim deduplication. Server UTC upload dates live only in new temporary SQLite, not a legacy/cloud migration. |

These original algorithms use representative stored chunks, **not hybrid search
or generic LLM summaries**. The existing hybrid/reranked Chat evidence pipeline
is preserved. Timeline events retain their originating owned document ID even
when filenames collide; the original displayed filename/year/claim/importance
fields remain. Insights preserves the original last-matching-filename reverse
map when filenames collide; this remains a provenance ambiguity, not a proven
claim citation.

Intentional output/UI differences: results have request-scoped item IDs and a
common UTC run timestamp rather than independently timestamped legacy rows.
Insights retains original type grouping but model order for same-run ties;
Knowledge Gaps likewise retains model order rather than per-row timestamp
ordering. Timeline is cached in the owned SQLite result store rather than only
frontend state. Topic edits select that topic's cache instead of showing a
previous topic. Schema/provenance validation is stricter than the original's
permissive defaults (invalid fields or invented source names reject completion).
These are disclosed adaptations, not semantic feature-parity claims.

## Full feature-parity checklist

"Integrated/tested" below means local native/unit/AppTest or mocked-provider
verification. It does NOT mean real Groq, real Google/two-browser or cloud
acceptance. Differences are disclosed rather than presented as full parity.

| Original surface/capability | Candidate status / difference |
|---|---|
| Dark theme, header, full sidebar, seven-tab navigation | Integrated. No analysis placeholder tabs remain. Seven tabs are Chat, Documents, Contradictions, Knowledge Gaps, Smart Questions, Insights, Timeline. |
| PDF upload and content-hash duplicate detection | Integrated, workspace-scoped. Sequential Store → Prepare → Index replaces original batch/Celery flow; acceptance never implies successful indexing. |
| Original PDF text and table extraction | Reused under bounded subprocess execution; complete publication only. Malformed/encrypted/resource-exhausting PDFs fail explicitly. |
| Scanned/mixed PDF OCR | **Not at parity**: disabled by default; explicit rejection, never silent empty-page indexing. Optional bounded path remains unverified on cloud. |
| Uploads up to 100 MB | Acceptance ceiling implemented/tested in storage; **real 100 MB processing not verified**. Current live root configuration remains at its old setting. A non-active preview config example is provided. |
| Original tokenizer/chunk boundaries, page/section/table metadata | Reused; deterministic citation IDs substitute for random chunk UUIDs. Full-source chunk-boundary comparison is available. |
| Original MiniLM embedding model | Reused; CPU batches of eight bound memory instead of original larger scheduling. One immutable model worker, not one per user. |
| FAISS HNSW + BM25 + reciprocal rank fusion | Reused, with complete owned snapshots instead of shared/global shards. Selected PDFs restrict both retrieval corpora before search. |
| Original cross-encoder reranking | Reused; bounded batch-one scheduling. Native and reference comparison tests cover it. |
| Parent/neighbor expansion and context aggregation | Reused; original nominal 4500-token policy plus strict complete-evidence rejection guards. No silent partial evidence truncation. |
| Original prompts and injection protection | Reused; fixed-origin Groq transport, document wrappers and system safety policy preserved. |
| Genuine Groq streaming, formatting, Markdown tables | Integrated. Incremental tokens and safe citation/HTML rendering; real provider quality/latency still pending. |
| Activity stages / source panels / inline citation badges | Integrated, actual observable stages only. Chat citations are source-validated; analysis source panels are explicitly input provenance. |
| Follow-up suggestions / selected-document conversational context | Integrated; successful completed turns only; auxiliary calls consume the same AI budget. |
| New Chat / session history / conversation selection | Integrated; five chats and twenty turns/chat. Intentionally temporary, unlike persistent Docker history. |
| Document selection, deletion, reindex | Integrated; transactional complete index replacement and conservative invalidation of all analysis caches. |
| Source Guide | Phase 3A integrated: original first-800-char L1 input, original JSON summary/topics and sidebar chips. Explicit trigger rather than automatic generation. |
| Collection Brief | Phase 3A integrated: all indexed PDFs, original cached-guide/topic assembly and missing-guide markers. Explicit trigger; no silent generic substitute. |
| Contradictions | Phase 3B integrated: original L3 sampling, all pairs, prompt, 0.4 threshold, descending uncalibrated signal and side-by-side cards. |
| Knowledge Gaps | Phase 3B integrated: original L1/L3 fallback sampling, prompt, topic/description and yellow cards. Potential research gaps, not proof of whole-document absence. |
| Smart Questions / Ask | Phase 3C integrated; original cards and topic. Ask uses canonical owned cache and normal full-collection streaming Chat, not the original error-as-completed-message behavior. |
| Insights | Phase 3C integrated; original type/title/description/supporting filenames and cards. Model-selected supporting documents are not claim-level citations. |
| Timeline | Phase 3C integrated; original extraction/sort/dedup/output/UI. Model dates may use upload-year fallback and are not independently verified publication dates. |
| Authentication / user isolation | Supabase Google PKCE broker, token verification/refresh/local logout unchanged; server registry owns temporary SQLite/files/indexes and all operation checks. No editable shared project ID. Live two-browser verification pending. |
| Cancellation / retries / provider failures | Shared bounded generation lifecycle; visible Cancel analysis/response, explicit new-request retries, no automatic rerun resubmission, no incomplete result publication. |
| Original persistent projects, Redis/Celery/background jobs | Intentionally omitted in the approved single-process temporary cloud architecture; original Docker services remain unchanged. Busy/resource errors replace unbounded job queues. |
| Restart recovery / durable document/chat storage | Intentionally omitted by approved temporary-workspace design; no Pinecone/Cloudinary document operations. Old temporary bytes may remain until host recycling; no exact secure-erasure guarantee. |

## Ownership, caching, failure and quota behavior

- All seven analyses use the existing verified capability/owner/workspace
  registry. Model-returned user/workspace/document IDs never authorize access.
  Snapshot paths derive only from the server registry; files are hash-verified.
- Results are saved only after complete provider termination, schema/provenance
  checks, current-input verification and a final atomic SQLite transaction.
  Failed later timeline documents/pairs cannot publish a partial collection.
  Earlier still-current completed results survive failed rescans.
- Every successful deletion/reindex clears analysis caches. Revision plus
  input/topic fingerprints reject stale reads and replays. Questions and
  Insights caches are per topic; editing a topic no longer displays another
  topic's stale result. This is an intentional improvement over a single
  frontend-only cache. Corrupt or foreign-provenance cache payloads fail closed.
- Ask stores only a reference to a cached question until the next rerun; the
  server re-resolves that current owned result before queueing the question.
  It cannot use an arbitrary supplied question as a workspace ownership claim.
  The handoff selects all indexed PDFs before widgets are instantiated, avoiding
  unsupported widget mutation or JavaScript tab switching.
- The same chat/analysis gate, bounded queues, owner/global request-token
  reservations, 180-second request and 45-second provider deadlines apply.
  Defaults remain 20 provider calls/account/day, 60/process/day, reserved-token
  upper bounds 120,000/account and 180,000/process, 200 calls/process lifetime.
  A call per timeline document or contradiction pair counts separately. Failed
  calls count; no automatic retries/refunds or separate feature allowances.
- Limits are process-local, not durable cross-restart abuse controls. Providers'
  free-account limits still apply. Nothing can promise that a configured paid
  provider account will never charge; use only explicitly free/no-billing
  credentials. No billing/account-plan changes or real calls were made here.
- Existing one-hour/auth-deadline expiry, two-workspace cap, three-document cap,
  128 MiB/workspace, 256 MiB tracked storage and parser/model guards are retained.
  These do not bound all Streamlit HTTP upload buffering or total app RSS.
- All HTML uses static original templates and escaped values; native topic
  labels escape Markdown. The restored collection analyses reject decoded JSON
  citation markers because they do not assign numbered claim citations. Original prompt safety
  is retained; no private reasoning/keys/headers/internal exceptions are shown.

Candidate runtime schema is `phase3c`: the document registry row records
server UTC upload time for the original timeline order/fallback. There is no
Alembic/Supabase/legacy migration and no reopening existing data. Restart only
an approved candidate preview after the protocol/schema update.

## Verification evidence

Focused Phase 3C suite: **46 passed**, including a genuine two-PDF parse/index
fixture exercising all seven analyses through fixed local HTTP/SSE mocks.
The original loaders and generation functions are evaluated directly from the
read-only source AST. Tests compare exact system/human prompts, sample limits,
fallback joins, provider parameters, output structures, timeline ordering and
deduplication and original question/insight/timeline HTML. Security/failure tests
cover two-user access, stale Ask references, malformed/foreign attribution,
corrupt caches/upload dates/indexes, logout/expiry, cancellation/consumer close,
rate/auth/timeout errors, replay, shared budgets, later-document atomic failure,
explicit Streamlit clicks and reruns. No real provider quality is implied.

Fresh reference comparison, **3 synthetic PDFs / 18 pages / 12 known-answer
queries / 25 searchable chunks**:

- Hybrid candidate and original: recall@1 **0.9167**, recall@5/@10 **1.0**,
  MRR@30 **1.0**, identical top-30 ordering **12/12**, identical chunk boundaries.
- Reranked candidate and original: recall@6 / MRR@6 **1.0**, selected and expanded
  order **12/12 identical**, deterministic fixture citation provenance **12/12**.
  These are retrieval and source-provenance metrics, NOT real answer accuracy
  or semantic claim entailment. No Groq request was made by these benchmarks.
- An initial reranked-context benchmark hit the PDF preparation timeout during
  concurrent testing; the fresh isolated rerun passed without changing limits.
- The first full suite recorded 407 passed, one skipped, one failed and one
  fixture error. The real regression was extra SQLite overhead: a separate
  upload-date table grew an empty DB to 73,728 bytes, exceeding the existing
  64 KiB test budget. Storing the date in the document row restores **65,536
  bytes**; the existing storage-limit test passes without increasing limits.
  The fixture error was its tiny upload exceeding the existing 30-second
  deadline; the isolated rerun passed. The precise host stall cause is not
  established. No upload/parser deadline or security assertion was weakened.

Final complete sequential regression: **409 passed, 1 skipped in 339.53 seconds**,
including **46 Phase 3C tests**. The skip is the existing Windows symlink test
requiring OS permission; it is not a claimed Linux pass. The storage regression
is fixed and the fixture upload deadline error did not recur. No production
limit, deadline or test assertion was relaxed.

Ruff (`session_cloud`, `tests`), standalone/native imports, syntax/whitespace,
preview TOML security settings and all **13 exact dependency pins plus applicable
direct runtime constraints** passed. Source credential/private-artifact pattern
scan passed for **109** non-ignored source/report files, including the new tests.
No staged files and no diff against main for deployed root/auth/frontend/app
files. Main, rollback and HEAD remain at
`dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`. Pattern scans are not an exhaustive
credential/security proof. All original and existing working-tree changes were
preserved; original Docker files were never edited.

## Measured resources and limitations

Fresh native two one-page PDFs, original MiniLM/index/reranker, cached public
weights, dummy authentication and **mocked Groq**:

| Operation | Wall seconds | Provider calls | Reserved tokens (upper bound, not actual usage) |
|---|---:|---:|---:|
| Source Guide | 0.781 | 1 | 1425 |
| Collection Brief | 0.437 | 1 | 1262 |
| Contradictions | 0.156 | 1 | 3014 |
| Knowledge Gaps | 0.172 | 1 | 2823 |
| Smart Questions | 0.140 | 1 | 2946 |
| Insights | 0.125 | 1 | 3284 |
| Timeline | 0.172 | 2 | 6652 |

Native worker lifetime peak: **370,614,272 bytes / 353.45 MiB**. Cached-weight
reranker initialization in the guide call: **0.344 seconds**. Embeddings were
warm from indexing; full network cold initialization was not measured. Index
build work measured 0.250 / 0.172 seconds (not full process startup). These are
not live Groq/Supabase latency, total application memory, isolated-host load
benchmarks, scanned-PDF measurements or 100 MB processing evidence.

The larger 18-page corpus measured native worker peak **436,428,800 bytes /
416.21 MiB**. Reranked context requests: first **3.266 seconds**, median **4.9845**,
max **6.031**; cached-weight reranker initialization **0.265 seconds**. The hybrid
retrieval median/max was **0.3745 / 0.547 seconds**. The separate hybrid reference
evaluation driver peaked at **397,611,008 bytes**; that driver loads comparison
models and is not the Streamlit UI process. Neither these separate high-water
marks nor their sum establish total cloud-app memory under concurrency.

Public PyPI release metadata was checked for **all 13 direct pins**: compatible
Python 3.12 Linux x86-64 or universal wheels exist. Native wheel families include
PyMuPDF cp310-abi3/manylinux_2_28, NumPy/FAISS cp312/manylinux_2_27+2_28,
ONNX Runtime/Arrow cp312/manylinux_2_28, Tokenizers cp310-abi3/manylinux2014
and Protobuf cp38-abi3/manylinux2014. This is wheel-availability evidence, not
a clean Linux install/resolver or Community Cloud execution. Windows native
imports and exact pinned runtime constraints are checked separately.

## Separate preview plan — NOT executed, approval required

1. Review this report and explicitly approve committing/pushing the candidate
   branch and creating a **new** preview app. Do not repoint the current app,
   push to main, move rollback, or change production Secrets/resources.
2. On a separately approved preview branch/repository only, use entrypoint
   `session_cloud/streamlit_app.py`, Python **3.12**, and
   `session_cloud/requirements.txt`. Community Cloud searches the entrypoint
   directory before the repository root for dependencies, according to the
   [official dependency documentation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
   Do not accidentally install the live app's Pinecone/Cloudinary requirements.
3. Apply `session_cloud/preview.config.toml.example` to that preview branch's
   root `.streamlit/config.toml`, never this live configuration. Root settings
   matter even for a nested entrypoint; see the
   [official repository/configuration limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status).
   It preserves CORS, XSRF, disabled static serving and generic error UI. The
   preview example accepts 100 MB; actual processing must pass resource gates.
   No `.streamlit` root file was changed in this implementation.
4. Populate **only the new preview app's** Secrets from the dummy
   `session_cloud/secrets.toml.example`: free-plan confirmation, Supabase project
   URL/publishable key, exact preview `APP_URL` (HTTPS + trailing `/`), authorized
   Groq Free key and unchanged model. No service-role/write/Pinecone/Cloudinary
   credentials. Never commit actual Secrets. No paid plans, trials or billing.
5. Approval is required for any Supabase change. Add an exact preview OAuth
   redirect alongside—not instead of—the existing live callback. Keep Google →
   Supabase provider callback unchanged unless separately required. Use distinct
   preview URL/session state; test PKCE, refresh, expiry and local logout. Provider
   account quotas may be shared with the live app even with a separate API key;
   explicitly approve a tiny real-request test budget before making calls.
6. First run a clean Linux/Python 3.12 install/native smoke and synthetic tests.
   Then on the **new preview only**, use two Google users in separate browsers:
   empty initial workspaces, different PDFs, all seven tools, streaming Ask,
   citations/history, foreign IDs/references, concurrent actions, logout/expiry,
   deletion/reindex invalidation, interrupted streams and deliberate failures.
   Use non-sensitive documents, not legacy/private originals.
7. Measure total app RSS, cold model downloads/startup, CPU/latency and actual
   Groq usage under one/two users. Gradually test digital, image-heavy, long,
   scanned and up-to-100-MB PDFs. Explicit rejection is acceptable; silent partial
   indexing is not. Keep OCR disabled until its separate acceptance tests pass.
   If free limits are exceeded, lower accepted workload; never auto-upgrade.
8. Stop at the first privacy/resource/security failure. Preserve logs only after
   sanitization; no tokens/headers/private excerpts. Do not delete existing
   resources. Rollback is simply leaving the current live app/main/rollback
   untouched. Any later replacement needs a separate explicit approval.

**Preview status:** integrated candidate for review and approval-gated testing,
not approved for production replacement. Remaining blockers: real Groq answer /
analysis correctness and citation grounding, live two-browser Google isolation,
Linux/cloud startup, total memory/concurrency, representative PDF-size/OCR
testing, preview-only config/callback setup and process-local quota limitations.
Official Community Cloud docs confirm Linux hosting and dependency discovery;
they do not prove this workload fits its changing resource limits. No paid host,
provider or infrastructure was introduced.

## Files changed in Phase 3C

New: `session_cloud/original_exploration.py`, `exploration_results.py`,
`exploration_view.py`, `preview.config.toml.example`, `PHASE3C_REPORT.md`,
`PHASE3C_EVALUATION.json`, `tests/test_exploration.py`.

Updated: `.gitignore` (allow dummy-only test source),
`session_cloud/runtime.py`, `analysis_workspace.py`, `analysis_service.py`,
`analysis_view.py`, `comparison_service.py`, `index_worker.py`, `groq_stream.py`,
`streamlit_app.py`, `source_manifest.json`, `README.md`. No dependency additions.
All earlier-phase and unrelated working-tree changes are preserved.
