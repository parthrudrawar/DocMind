# Temporary cloud candidate — Phase 3C

## Approved separate preview preparation

Use [PREVIEW_DEPLOYMENT.md](PREVIEW_DEPLOYMENT.md) for the feature-branch preview.
The approved preview uses `session_cloud/streamlit_app.py`, the adjacent pinned
`requirements.txt`, Python 3.12, and this branch's root Streamlit configuration
(100 MB acceptance ceiling). Main, rollback and the existing deployed app remain
unchanged. Earlier references below to unchanged root config / no push describe
historical implementation checkpoints, not the newly approved preview packaging.

All **seven analysis features** are connected: Source Guide and Collection
Brief in the sidebar; Contradictions, Knowledge Gaps, Smart Questions, Insights
and Timeline in their original tabs. Together with Chat and Documents, all
**seven tabs** are functional, not integration placeholders. Read the
consolidated `PHASE3C_REPORT.md` for the feature-parity checklist, actual tests,
measurements, intentional differences and approval-gated preview plan.

Smart Questions preserves first-four-L2/first-two-L3 300-character snippets,
original prompts, optional topic, temperature 0.5 and 1024 output tokens. Ask
revalidates the current cached question and queues genuine streaming Chat over
the full indexed collection, as the original did. Insights preserves
first-five-L2/first-three-L3 400-character excerpts, topic, temperature 0.4 and
1200 output tokens, including filename-based supporting-document fields.
Timeline preserves L1 first-600 plus two L3 first-400 excerpts/document, actual
server-recorded upload dates, per-document calls at temperature 0.2 and 1500
tokens, year ordering and first-80-character lowercase claim deduplication.
These original analyses sample indexed chunks; they do not substitute hybrid
retrieval hits or generic summaries. Source panels are input provenance, not
fabricated claim-level citations. Timeline dates may use the original prompt's
upload-year fallback and are not independently verified publication dates.

Every expensive analysis is explicitly user-triggered, uses the same chat
budget/gate and publishes only a completed current result. Question/insight
topic caches are workspace-local. New preview runtime schema `phase3c` records
upload dates in its own SQLite database; it never migrates/reopens legacy or
deployed data. Restart only an approved candidate preview after this change.
The current root Streamlit configuration remains unchanged; the non-active
`preview.config.toml.example` must be considered only for a separately approved
preview. The existing live app is not changed or ready for replacement.

**Phase 3B restores Contradictions and Knowledge Gaps** in their original tabs.
Both cover ALL indexed workspace PDFs, not the chat selection. Buttons are
explicit triggers; reruns/read-only cache loads never generate analysis.
Contradictions retains the original distinct-filename UI prerequisite, all
document pairs, first five L3 excerpts of 300 characters, exact fact-checking
prompt, temperature 0, 1024 output tokens, 0.4 threshold and descending signal.
Knowledge Gaps retains the original first L1 summary or first three L3 fallback
paragraphs (space joined), first 1500 characters, research-librarian prompt,
temperature 0.3 and 1024 output tokens. Neither original algorithm uses a hybrid
query/reranker; replacing those inputs with search hits would change behavior.

These are **potential** contradictions/research gaps in sampled material, not
proof of full-document inconsistency or absence. Confidence is the original
uncalibrated model signal, never an accuracy probability. Exact sampled matches
are distinguished from model paraphrases. Source cards show authenticated
**input provenance**, not fabricated claim-level citations. No-findings results
are distinguished from never-run, stale, missing-evidence and failed analyses.
Existing chat budgets, concurrency, deadlines and completed-only workspace
caches apply to every pair/call. No shared results or automatic analysis after
indexing. See `PHASE3B_REPORT.md` and `PHASE3B_EVALUATION.json` for verification.

**Phase 3A restores Source Guide and Collection Brief**, on explicit sidebar
clicks only. Source Guide preserves the original level-1 summary's first 800
characters, JSON summary/topics prompt, temperature zero and 512 output tokens.
Collection Brief preserves the all-indexed-workspace scope, cached-guide/topic
input construction, missing-guide placeholders, three-sentence prompt,
temperature 0.3 and 300 output tokens. Chat document selection does NOT narrow
the brief. If no guides exist, generate one first; there is no automatic cascade
or generic fabricated substitute. See `PHASE3A_REPORT.md`.

The original escaped summary/topic chips and green brief card are reused.
Source panels identify actual **input provenance**, not model-generated
claim-level citations (the original features assign no citation numbers).
Hybrid retrieval/reranking verifies the authorized snapshot and projects the
original L1 records; retrieved child passages do not replace the original
analysis input. The 800-character original excerpt is not a full-document review.

Results live only in the authorized workspace SQLite database. Reads and UI
reruns make no AI calls. Successful deletion/reindexing invalidates all analysis
caches conservatively; guide regeneration invalidates dependent briefs. Invalid
or interrupted regeneration does not overwrite an earlier still-valid result.
Only explicit new clicks retry failed requests. Chat and analysis share the same
single generation gate, provider budget, timeouts, cancellation infrastructure
and free-tier policy. Every analysis call counts, including failures. No cloud
credentials, Docker deployments, live entrypoint, main or rollback were changed.
No real Groq or browser/OAuth verification is claimed. Start a fresh candidate
runtime for the versioned ephemeral schema; do not migrate legacy databases.

Current status: **2C2 implements genuine Groq SSE streaming**, bounded provider
reservations, observable worker stages and atomic completed-turn storage. Chat
is enabled only in this separate candidate entrypoint when a server-side Groq
key is configured and owned indexed PDFs are selected. No live deployment or
real Secrets were changed. Native/model and mocked-provider tests do not prove
real Groq answer quality or Streamlit Cloud readiness. See `PHASE2C2_REPORT.md`.

The unchanged **2C1** evidence foundation supplies the
original cross-encoder reranking, neighbor/parent context expansion, original
prompt builders and literal citation-attribution validation. In the Chat tab's
search form, opt into **Preview original reranking and expanded answer evidence**.
This is a genuine evidence preview, not an AI answer. All documents stay in the
existing authenticated temporary workspace. See `PHASE2C1_REPORT.md`.

The original prompts now execute through a fixed-origin server-side HTTP/SSE
adapter, without LangChain/global deployment settings. All original analysis
panels are now connected; prior reports are historical checkpoints.
`PHASE2B_REPORT.md`, `PHASE2A_REPORT.md` and `PHASE1_REPORT.md` are
historical checkpoints. No live deployment, Secrets or main-branch change.

The original reranker uses a separate public model cache capped at 160 MiB,
alongside the embedding model's 160 MiB cache, in the same bounded shared worker.
No request/index cache is shared. Reranking batches are scheduled one pair at a
time rather than 64. The original 4,500-token stored-cost expansion policy is
retained, with an additional 8,192 **untruncated MiniLM-token** / 50,000-character
complete-evidence rejection guard. This is not a Groq-token usage budget.
Linux execution, real OAuth/browser flow, cold downloads and large PDF memory
profiling remain unverified. Restart any candidate preview after upgrading the
worker protocol; do not change the currently deployed entrypoint.

The sidebar has **Prepare PDF**, then **Index PDF / Reindex PDF**. Only indexed
documents appear in **Searchable documents**. The Chat tab's **Find supporting
passages** form inspects real hybrid retrieval without making an AI request.
Source panels show unescaped-as-HTML plain text, actual filenames and page
numbers. Retrieved passages are not answers. The separate chat composer streams
unverified drafts and reveals clickable source panels only after validation.

## Conversational safety (2C2)

Use only the existing free Groq account, never enable billing. The optional
candidate-only `GROQ_API_KEY` and `GROQ_MODEL` names are in the dummy Secrets
template; actual values remain server-side. The reviewed model is the original
`openai/gpt-oss-120b`, temperature zero. No provider fallback or automatic retry.
The candidate does not inspect or change a provider account's billing settings.

One generation/process, a 16-event bounded queue, 180-second turn deadline,
45-second provider deadline and 10-second socket read timeout. Cancellation
closes the consumer, revokes publication and keeps the generation gate occupied
until the producer exits; blocked I/O can take a timeout interval to stop.
Model work retains its existing 120-second and 640 MiB sampled/job guards.
Source deletion/reindexing is blocked during answer generation; snapshot changes
between retrieval and generation reject the request rather than use stale data.

Every provider call reserves its **entire UTF-8 prompt byte upper bound plus
framing allowance and maximum output**, without refunding failed calls. This is
deliberately pessimistic, not measured Groq tokens. Per UTC day: 20 calls and
120,000 reserved tokens/verified account; 60 calls and 180,000/process; 200 calls
lifetime/process. All helpers count. Up to six calls/turn, at most three quote
fallbacks, 2,048 answer output tokens, 256/helper, 8,000 answer characters.
Complete prompt bound is 64,000 with total context bound 131,072. Actual provider
limits may reject earlier. Long PDF contexts can exhaust these budgets after
only a few answers. Counters survive logout/new chats/workspaces, **not process
restart**; this remains a restricted portfolio candidate, not durable public
anti-abuse protection. No billing operation is implemented.

Only a `stop` finish plus `[DONE]`, valid source numbers and literal authorized
quotes can complete a turn. Uncited factual responses are rejected; the exact
insufficient-evidence response `The provided evidence does not answer this
question.` is permitted. Literal source checks do not prove semantic entailment.
Optional follow-up failure leaves a valid answer with no invented suggestions.
Request IDs prevent replay charges; failed IDs cannot be automatically resumed.
Explicit Retry uses a new ID and allowance. Interrupted drafts are session-local,
plain-text, clearly unverified and **never completed SQLite turns**. Reruns of a
running request cancel it instead of resending it. Conversation context is the
original last six completed messages for the exact current document selection.
Selection is retained per chat; logout/expiry revoke publication and workspace
cleanup remains ownership-scoped. Original prompt-injection policies and safe
Markdown/citation HTML rendering are retained; no claim of foolproof injection
resistance is made.

Additional 2B guards: 1,200 retrieval children/workspace, 3 MB prepared source
text, 16 MiB/snapshot, one heavy operation and one shared model worker per app
runtime, 120 seconds/worker request, 640 MiB Windows job memory cap. Linux uses
a 3 GiB virtual-address ceiling plus a 50 ms sampled 640 MiB RSS guard; this is
not a hard cgroup RSS limit. Public model cache has its own 160 MiB disk cap.
Six index attempts/account/day, twenty/process/day; sixty retrieval attempts/
account/day, two hundred/process/day. Failed attempts count. These process-local
counters are not durable anti-abuse protection across restarts.

The candidate has its own pinned `requirements.txt`; do not replace the deployed
requirements or entrypoint until separately approved. FastEmbed uses the original
MiniLM ONNX weights; installing PyTorch/sentence-transformers is unnecessary.
100 MiB is an upload ceiling, **not** a processing guarantee. A near-ceiling
upload may leave insufficient workspace disk/memory for a complete index and
will be rejected explicitly. No truncation or partial searchability is allowed.

Branch: `feat/temporary-cloud-workspaces`. Local rollback branch:
`rollback/authenticated-cloud-dec268c`, commit
`dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`.
The deployed `authenticated_cloud/streamlit_app.py`, its requirements, SQL,
Secrets and root Streamlit config are unchanged. No push or deployment is part of
this phase. Do not switch the deployed entrypoint yet.

## Implemented

- Existing Supabase AuthBroker and Google PKCE handshake, verification, refresh
  and logout. Auth-only transport cannot call document/database endpoints and
  needs no cloud write key. Provider credentials stay on the server.
- Server-owned registry binds each opaque login capability hash and verified
  Supabase identity to a new random workspace. Neither a browser project ID nor
  session_state authorizes a read, write or deletion. Two tabs with independent
  login capabilities get independent workspaces, even for the same account.
- A new private temporary root per process, one SQLite DB per workspace,
  hashed PDF filenames, exact owned-path deletion, symlink/junction rejection,
  per-workspace history and atomic deduplicated turn storage for the future engine.
- Original CSS, chat renderer/state logic, responsive chat viewport/composer,
  source panels/activity/follow-up rendering capability, and seven-tab shell.
  Smart Question card rendering is copied; no questions are fabricated.
- Complete PDF-byte storage up to 100 MiB. A separate **Prepare PDF** action runs
  original page/table extraction in an OS-limited child and atomically stores all
  parsed pages in SQLite. The result is **prepared, not indexed**. Any failed
  page/table or resource limit rejects the whole preparation. The chat composer,
  source-guide and analysis actions remain disabled until their engine is ready.
  This paragraph describes the historical preparation step; 2B indexing is now
  available separately, as described above.

## Initial safeguards and their limits

Two active workspaces/process, one upload/delete operation at a time, one-hour
workspace TTL bounded by the existing auth deadline, three documents/workspace,
128 MiB/workspace and 256 MiB total tracked local storage. SQLite is capped at
4096 pages; chat histories have five chats and twenty turns/chat. There is no
silent PDF truncation. Upload loops have a 30-second deadline checked between
bounded 1 MiB reads. Six upload attempts/verified account/day, twenty/process/day;
failed attempts count and logout does not reset counters. Counters reset at UTC
midnight or process restart. They are not durable abuse prevention.
Preparation has separate six/account/day and twenty/process/day attempt budgets;
failed attempts count, and logout/new workspaces do not reset them.

Expiry and logout revoke access before cleanup. Cleanup runs on service activity,
only for directories recorded in this process's registry, and waits for active
operations. Browser disconnect does not guarantee immediate physical cleanup.
A process restart forgets every capability/workspace mapping and uses a new root;
old temporary bytes may remain on disk until the host recycles storage. No exact
erasure guarantee or cross-restart document recovery is claimed.

Streamlit buffers incoming uploads in RAM before this service receives them. The
storage semaphore cannot bound concurrent HTTP upload buffering. Milestone 2A
limits the parser child to 512 MiB, 120 seconds wall time, 60 seconds CPU,
200 pages, 32,000 UTF-8 text bytes/page, 1,000,000 total text bytes, and a 4 MiB
result. Windows uses an inheritable Job Object; Linux uses process/session
termination and resource limits. The Linux memory cap is per process; Windows
caps the job including OCR descendants. These are parser guards, not a hard cap
on the whole Streamlit application or an OS security sandbox for native exploits.

OCR is disabled by default. Scanned pages requiring it fail explicitly, including
mixed PDFs; they are never replaced by empty pages. Optional OCR uses the original
2x RGB/psm=6 Tesseract path, capped at three pages, four million pixels/page and
15 seconds/page, under the same outer guard. See `requirements.ocr.txt` and
`packages.ocr.example.txt`; do not enable before actual OCR acceptance tests.

There is **no verified 100 MB RAG processing, OCR parity or original answer-quality
claim** yet. Phase 2B measured native model memory and resolved Linux wheels;
Linux execution, larger-document profiling and live browser verification are
still required before replacing the deployed entrypoint.

## Next phase — approval-gated preview verification

Implementation is integrated, not production-verified. Follow the separate
preview plan in `PHASE3C_REPORT.md` only after explicit approval to commit/push
and create a new preview app. Real Groq quality, real OAuth/two-browser tests,
Linux/cloud execution, cold initialization, total application memory and
representative large-PDF processing remain blockers. Keep current deployment,
main, rollback and original Docker data unchanged. No paid service or billing.

Required acceptance: two Google users, foreign IDs/paths, token refresh/expiry,
logout during operations, concurrent requests, PDF bombs, scanned PDFs, full
disk, interrupted streaming, restart, and responsive browser layout. Unit/AppTest
fixtures are not live Community Cloud or Google OAuth verification.
