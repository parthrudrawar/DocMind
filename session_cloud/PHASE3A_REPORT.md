# Phase 3A: original Source Guide and Collection Brief

Implemented only in the clean publishing repository on
`feat/temporary-cloud-workspaces`. No commit, push, deployment, billing, cloud
resource or real Secrets change. The original Docker project was read only;
its existing working-tree changes and data were not modified. Main/rollback
remain at `dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`.

## Restored original behavior

- **Source Guide:** original `run_source_guide` prompt, original first level-1
  summary chunk, original first **800 characters**, 20-word minimum, fenced-JSON
  handling, summary and 3–5 topic output. Same central model, temperature zero,
  512 maximum output tokens. Public result fields include `doc_id`, `summary`,
  `key_topics` and `created_at`.
- **Collection Brief:** original document/topic input assembly, missing-guide
  placeholders, exact three-sentence prompt, temperature **0.3**, 300 output
  tokens, `brief` output. It covers **all indexed PDFs in the authorized
  workspace**, independent of the chat selection. Guides are never automatically
  generated as a side effect of clicking Brief.
- Original escaped sidebar summary HTML, blue topic chips and green brief card
  match directly evaluated original frontend expressions. No generic summary
  prompt or alternative model is substituted.

Original references: `app/workers/tasks.py`, `app/api/routes/brief.py`,
`app/api/routes/documents.py`, `frontend/app.py`. Digests are recorded in
`source_manifest.json`. Tests evaluate actual original AST fragments; they do
not import original settings, credentials, database sessions or Celery workers.

## Evidence, authentication and storage

Existing hybrid retrieval, original cross-encoder reranking and context
preparation verify the owned selected snapshot. Its hash-verified L1 summary
records are projected with the original filename/page metadata mapping. The
original summary/guide inputs, **not the retrieved children**, are supplied to
the analysis prompts. Related retrieval counts/metrics remain factual.

Neither original feature requests numbered claim citations. The candidate does
not invent them: source panels explicitly label the actual L1 input excerpts as
**input provenance, not claim-level citations or semantic entailment**. Brief
provenance points to PDF excerpts that produced its source-guide inputs. Exact
quotes, filenames and page ranges come from those authorized records. Model
invented citation markers reject completion. The original UI escapes model text;
existing sanitized source-card rendering is reused. System-level untrusted
document policy supplements the unchanged original user prompt.

Every operation resolves the opaque capability via the existing Google/Supabase
authorization and server-owned workspace registry. Neither Session State nor
browser document IDs prove ownership. Results and request fingerprints live only
in that workspace's temporary SQLite database. Reads and reruns do not call AI.

The SAME chat generation gate, provider budget, bounded queue/thread and
cancellation mechanism serve analysis. One provider call per analysis, no
automatic retries. Every call—including failure—reserves full prompt/framing and
output bounds. Existing daily/account/global/process caps, 180-second operation,
45-second provider, 10-second socket and native model guards remain unchanged.
Revoke/check local capabilities and expiry during progress, reverify Supabase
at phase boundaries and before publication. No provider key enters the UI,
native worker, result payload or log.

Only cleanly terminated, structurally validated results publish atomically.
Interrupted regeneration cannot overwrite a still-valid earlier result. A
completed request ID replays without charge; failed IDs cannot automatically
resume. UI actions are explicit clicks and reruns cannot silently resend them.

Successful snapshot replacement/deletion invalidates analysis caches in its
transaction, even when a same-content reindex has the same checksum. Reads and
replays also check snapshot identity/fingerprints. Regenerating a guide invalidates
briefs and changes their dependency fingerprint. Index/delete operations cannot
mutate inputs during the provider call; a race between retrieval and generation
fails closed. Failures preserve the prior valid snapshot/result.

## Verification

Final matched-stack full suite: **321 passed, 1 skipped in 617.15 seconds**.
The skip requires Windows symlink-creation permission; it is not a claimed
Linux pass. Ruff, exact pinned/native dependency checks, standalone imports,
syntax and `git diff --check` pass. The source/private-artifact pattern scan
found no credentials/private artifacts; nothing is staged. Protected deployed
files, main and rollback are unchanged. No new dependency was added.

Tests cover original prompts/input construction/provider parameters/sidebar HTML,
JSON/fence handling, native PDF → index → guide → brief, same-checksum reindex,
deletion, cache reads, replay, input races, missing guides, short L1 text, malformed
responses, timeouts/rate limits, shared chat budgets/concurrency, two-user foreign
access, cancellation/logout/expiry, HTML escaping and Streamlit explicit-click /
rerun behavior. All provider calls use dummy credentials and mocked HTTP/SSE.

An initial broader run found the sandbox blocking prior native-wheel files,
causing Python to fall back to older versions. That run is NOT counted as
pinned-stack verification. Correcting permissions on the isolated test-runtime
root restored exact versions without modifying original environments or using
new cloud credentials. There was no application fix to bypass that regression.

## Measured resources (not real Groq latency)

Pinned Windows/Python 3.12 native fixture: **one public synthetic page**, genuine
PDF parsing, original chunking/MiniLM, FAISS/BM25/RRF, reranking and expansion;
fixed mocked Groq completions. Source Guide **1.734 seconds**, Brief **0.859
seconds**; one mocked provider call each. Native worker lifetime peak:
**369,315,840 bytes / 352.21 MiB**. Guide/brief evidence work took 0.312/0.343
seconds; cached-weight reranker first initialization 0.860 seconds. Embedding
was already warm from indexing. No cold network download or total Streamlit
memory measurement is claimed.

Provider reservation upper bounds: **1,413 + 1,231 = 2,644 units**. Actual Groq
token consumption is unmeasured/null. These figures are not full-document,
multi-user, 100 MiB, Linux or Community Cloud capacity estimates. See
`PHASE3A_EVALUATION.json`; the reproducible native fixture is in `test_guides.py`.

## Intentional differences and remaining gates

- Explicit clicks replace original automatic Celery post-ingestion work and
  automatic task retries, as required. No Redis/Celery/FastAPI service dependency.
- All-missing guides reject a brief before a provider call, rather than letting
  the original placeholder-only prompt produce an unsupported generic briefing.
  Mixed available/missing guide inputs preserve original placeholders and show
  a warning. No guide is silently synthesized.
- Structural validation, no fabricated references, auth checks and resource
  guards are stricter. They may reject outputs the original accepted. Three
  sentences / claim correctness remain model instructions, not proven semantics.
- Invalidation is conservative: any successful workspace snapshot change clears
  all analysis caches, even if another document's own text was unchanged. Failed
  index changes do not clear the previous valid results.
- The original 800-character excerpt is a known coverage limitation, faithfully
  retained and disclosed, not replaced with a generic whole-PDF summary.
- Runtime schema is ephemeral and versioned; restart only a separately approved
  candidate preview, not the deployed app. No legacy migrations or data resets.
- No real Groq, real Google/two-browser isolation, Linux/cloud execution, large
  PDF or total host-memory verification here. Previous live-verification gates
  remain open; this is not production readiness or semantic-output parity.
- Contradictions, Knowledge Gaps, Smart Questions, Insights and Timeline remain
  disabled for subsequent Phase 3 milestones.

## Changed files

New: `session_cloud/original_guides.py`, `analysis_workspace.py`,
`analysis_service.py`, `analysis_view.py`, `PHASE3A_REPORT.md`,
`PHASE3A_EVALUATION.json`, `tests/test_guides.py`.

Updated: `.gitignore`, `session_cloud/runtime.py`, `context_engine.py`,
`index_workspace.py`, `index_worker.py`, `groq_stream.py` (default remains zero;
allow original brief temperature 0.3), `streamlit_app.py`, `source_manifest.json`,
`README.md`. Dependencies and deployed root configuration are unchanged.
