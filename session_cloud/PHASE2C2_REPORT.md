# Phase 2C2: temporary conversational RAG implementation

## Status and preservation

Implemented on `feat/temporary-cloud-workspaces`, uncommitted. No push, deploy,
billing, cloud-resource, real Secrets or original Docker project change. Main
and rollback stay at `dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`. The deployed
`authenticated_cloud` entrypoint, root requirements/config and provider resources
are unchanged. Seven analysis features remain disabled for Phase 3.

This is an implementation with offline verification, **not production readiness
or verified live Groq answer-quality parity**. No real API key was read, no real
Groq completion made, and no real Google/browser/cloud session was tested.
Presence-only checks found no candidate-local `.streamlit/secrets.toml` and no
`GROQ_API_KEY` environment variable. Original/live credentials were not read or
reused. A separately approved candidate configuration is needed for live tests;
never paste a credential into a report, chat, command argument or committed file.

## Original generation reused and explicit adaptations

- Original answer system prompt, six-message context builder, evidence
  aggregation, decomposition heuristic/template, follow-up template, exact-quote
  sentence selection/fallback prompt, legacy confidence and evidence labels.
- Original chat shell, safe Markdown/table/citation rendering, source cards,
  follow-up buttons and streaming token/stage callback contract.
- Groq uses the original central model `openai/gpt-oss-120b`, temperature zero.
  Replace global LangChain clients with fixed-origin `httpx` SSE transport:
  genuine deltas, `max_completion_tokens`, hidden reasoning, no tools, redirects,
  inherited HTTP proxy settings, automatic retries or raw provider diagnostics.
- Original formatter parallel helper pool becomes serialized bounded calls.
  Maximum three short-quote fallbacks and six provider calls/turn. A helper's
  invalid quote rejects completion; optional follow-up failure returns no fake
  suggestions. Uncited factual responses reject completion; an exact, minimal
  insufficient-evidence response is allowed, as documented in README.
- Only same-selection completed history is passed to the original last-six
  prompt builder. Switching document selection cannot reintroduce old document
  excerpts via conversation context. This is stricter than the original.

## Conversation lifecycle, authorization and resource policy

Server-issued capability and verified Supabase identity authorize workspace,
chat and indexed documents before any provider call. Each answer uses the
hash-verified native evidence snapshot. Ownership and local revocation/TTL are
checked on chunks; Supabase verification occurs at phase/provider boundaries,
before commit and at least once/second during progressing streams. Blocking
network operations are bounded separately; revocation is not instantaneous
during blocked I/O. Final source metadata comes only from the authorized context.

SQLite stores request fingerprint/status and completed turns in the private
workspace. A repeated completed ID replays without provider calls; a failed or
running ID cannot automatically resend. New Chat is empty. UI selections persist
per chat; queued turns freeze their selection. Only fully framed, validated
answers and metadata commit atomically. Interrupted drafts remain explicitly
unverified UI text, outside conversation context/SQLite completed history.
Streamlit reruns cancel running requests and offer explicit, newly charged Retry.
Logout/expiry prevent further output/commit; cleanup remains workspace-scoped.

Actual search, reranking and expansion events are emitted inside the native
worker, then displayed with generation/citation stages. Immutable numbered
progress files avoid a Windows read/replace sharing race. No fabricated progress
or private reasoning is displayed. Worker retains models, not request state.

Generation policy and precise conservative reservations are documented in README:
one concurrent generation, 16 queued events, 180-second turn / 45-second provider
deadlines, 10-second socket reads; cancellation gate remains held until producer
exit. The existing native model process has its own limits. Complete prompts
are rejected, never silently truncated. UTF-8/framing bounds are pessimistic,
not exact Groq token counts; observed provider usage is recorded separately when
present. Failed calls count. Limits are server-side, per verified account and
process; logout/new workspaces do not reset them. They reset on process restart,
so they are not durable public abuse protection. No billing API is used, but the
app cannot verify the owner's provider billing plan without external evidence.

## Verification and measurements

Final combined suite: **292 passed, 1 skipped in 208.92 seconds**. This includes
the final-delivery recovery regression. The earlier matched-stack suite passed
291/1 before that last regression was added. The skip requires Windows
symlink-creation permission, not an assumed
Linux pass. Ruff, standalone imports, syntax, exact candidate dependency versions
and original generation/history helper AST checks pass. The final source/private
artifact pattern scan has zero findings and zero staged files. No new native package
was required. Linux execution remains unverified; the 2C1 binary-resolution
artifact is historical evidence only, not a current Linux runtime test.

The complete **offline** evaluation used the same three PDFs, eighteen pages and
twelve known-answer questions. Native PDF parsing, tables/chunking, MiniLM,
FAISS/BM25/RRF, original reranking and expansion all ran. The provider was
`httpx.MockTransport`, emitting bounded SSE frames of fixed known-answer strings.
No network completion, private credential or paid service was used.

- **12/12** completed the streaming/validation/history lifecycle.
- **12/12** prompts matched the executed original prompt builder.
- **12/12** citation lists matched the executed original formatter on the same
  authorized context and fixture answers.
- **12/12** literal oracle facts had citations to their actual PDF/page ranges.
- These are orchestration/provenance fixtures, **not measured LLM answer
  correctness, semantic entailment or real Groq citation accuracy**.

Measured Windows/Python 3.12.4 native stack: NumPy 2.4.6, FAISS 1.12.0,
FastEmbed 0.9.0, ONNX 1.31.0, tokenizers 0.23.3, httpx 0.28.1. Worker lifetime
peak RSS: **435,937,280 bytes / 415.74 MiB**; evaluation driver peak:
**166,313,984 bytes / 158.61 MiB**. These are separate peaks, not synchronized
total Streamlit RAM. Median first fixture token **6.156 s**, median completion
**6.320 s**; first request **6.140 / 6.281 s**. Cached-weight reranker first
initialization **0.813 s**; embedding was warm from indexing. There is no measured
cold network download, actual Groq network/first-token latency or cloud-host RAM.

All twelve fixture turns reserved **418,379 token-upper-bound units**; actual
Groq token consumption is **unmeasured/null**. The offline evaluator explicitly
raises TEST-ONLY token reservations because it has no real transport/key. The
unchanged default account budget would fit only the **first three** of these
turns; it must not be claimed to run twelve such answers in one day. This is
conservative resource protection, not a calibrated token estimate. See
`PHASE2C2_EVALUATION.json` and the reproducible offline driver. Full raw report:
`C:/Users/rudra/Documents/Codex/documind-phase2c2-test-runtime/evaluation/generation-report.json`
(SHA256 `a79be9dedbe33cedc2ae9232f88a8dd3f7e000dbde10590b61c54d8869bf5eec`).

An initial full run was stopped when the exact-version regression detected
sandbox-denied prior wheel files and fallback to older libraries. It is not
counted as pinned-stack verification. An explicit sandbox read grant restored
access to the existing pinned wheels, referenced read-only from a fresh test
virtual environment. A public-wheel download attempt was stopped; no dependency
or environment in the original Docker project was modified.

## Remaining gates before Phase 3 / deployment

- Real Groq streaming, provider-reported consumption, answer correctness and
  semantic grounding on the same corpus, adversarial PDFs and realistic questions.
- Real Google OAuth/refresh/logout and two-browser tests on a separately approved
  candidate URL; current live deployment must not be switched automatically.
- Linux/Community Cloud execution, total app/worker RAM, cold public model
  download, large PDF/OCR behavior, concurrent browser load and timeouts. Upload
  acceptance of 100 MiB is not a successful-processing guarantee.
- Free-tier reservations can stop long-document demos after a few answers.
  Pessimistic counters are intentional and must not be described as exact usage
  or silently relaxed. Review measured usage before changing budgets.
- Citation validity checks provenance and literal quotes, not whether every
  factual claim is entailed. Prompt injection safeguards are retained, not proven
  invulnerable. Incomplete/rate-limited answers are never labeled successful.
- No analysis integration in this milestone. Do not advance on a claim of live
  parity; review offline results and perform the remaining approved live checks.

## File scope

New: `session_cloud/groq_stream.py`, `generation_budget.py`, `generation.py`,
`evaluate_generation.py`, `PHASE2C2_REPORT.md`, and `tests/test_generation.py`.
The successful offline run is summarized in `PHASE2C2_EVALUATION.json`.

Updated: `.gitignore`, `session_cloud/adapter.py`, `chat_state.py`, `chat_view.py`,
`context_engine.py`, `index_service.py`, `index_worker.py`, `index_workspace.py`,
`runtime.py`, `streamlit_app.py`, `secrets.toml.example` (dummy only),
`source_manifest.json`, `README.md`. No new production dependency required:
existing pinned `httpx==0.28.1` implements transport.

Provider contract references: [Groq streaming](https://console.groq.com/docs/text-chat),
[API framing/options](https://console.groq.com/docs/api-reference),
[free-plan rate limits](https://console.groq.com/docs/rate-limits).
