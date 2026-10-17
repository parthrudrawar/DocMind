# Preview UX integration and final review

Scope: `feat/temporary-cloud-workspaces` only. The initial implementation was
local only. On 2026-10-18 the user authorized a verified commit and push of ONLY
this feature branch. No main merge, direct deployment, cloud resource/Secret
changes, migration, or edits to the original Docker application, main, rollback
or production app. Existing Google authentication is unchanged. Commit/push
outcome is reported separately in the handoff; a branch push may trigger the
existing preview app's automatic rebuild, not the main-branch production app.

## Interface

Prominent, proportionate sidebar/header branding; requested exact tagline; dark
theme, full available content width, readable messages/source cards, horizontal
narrow-screen tab navigation and wrapping question cards. Native keyed containers
provide header/navigation stickiness and an independently scrolling conversation
above its composer. No new JavaScript, DOM manipulation or generated class names.
Reduced repeat message animations; native Streamlit reruns remain, not hidden.
Sidebar retains New Chat, history, logout, PDFs and Source Guides. Help & privacy
contains real storage, expiry, quota, OCR, provenance and cleanup limitations.

Collection Brief is now prominent in Documents, with all-indexed-document scope,
local loading/retry/errors and source material. Guide/comparison/question/insight/
timeline algorithms and prompts are preserved. Expensive analyses remain explicit
actions; their status now appears with the relevant tool instead of the sidebar.

## Context-limit cause and correction

The original expansion selects children, neighbors and parents with stored token
costs. The embedding tokenizer can truncate its count at the input window, so a
large multi-page parent can be recorded at only a few hundred tokens. Expansion
can therefore admit a huge, duplicative parent before the final **untruncated**
8,192-token / 50,000-character guard rejects the entire result. This is not a
Groq quota or upload-size failure. No private failing PDF or live log was read;
the failure characteristics are reproduced with a synthetic 16-page digital PDF.

The new complete-passage packer preserves the original bundle unchanged when it
fits. Otherwise it protects the strongest reranked passage per represented
document, prioritizes primary evidence over optional neighbors/duplicative parents,
and packs only complete passages under true tokenizer, character and UTF-8 bounds.
The 32,000-byte evidence ceiling reserves room for prompts/history. A second pass
accounts for the actual formatted generation prompt and existing input ceiling.
No token/context limit was increased. If essential passages cannot fit, fail
clearly; never cut source sentences or publish partial indexes. Renumber sources
after packing and validate quotes/citations against exactly that final evidence.
Recorded omission counts and a concise answer caption disclose context selection.
Coverage/semantic entailment is not guaranteed merely by a valid literal citation.

## Automatic ingestion

Selection automatically stores, prepares and indexes, with observed phase status
and ready/error feedback. Content hashes, authoritative owned document status and
a bounded server-side attempt ledger prevent duplicate processing across reruns
or concurrent calls. Widget identity avoids rehashing a large upload on every
unrelated UI rerun; it is not an authorization boundary. Failed/interrupted runs
require explicit Retry and resume only unfinished steps. Underlying attempt limits
still apply and failed attempts are not refunded. Duplicate ready PDFs consume no
new ingestion attempts in this workflow. Delete remains available; manual prepare/
reindex controls are optional advanced actions. 100 MB acceptance is unchanged,
not a guarantee that every PDF passes page/text/chunk/time/memory/storage gates.

## Inline Smart Questions

Ask uses the current server-authorized cached question and all its indexed PDFs.
It creates a bounded, internal question thread in the existing workspace SQLite,
not the active conversation. No new table or schema migration is introduced.
Normal conversation history/selection and active tab are not programmatically
changed. Real SSE tokens, observable stages, validated sources and explicit retry/
cancel states render below the same card. Only validated completed turns persist.
Reruns cannot resend a request; topic changes cancel instead of leaving a stuck
pending state. Completed delivery is recovered from authoritative stored turns.

Up to ten internal answer threads/workspace, separate from the existing five
manual conversations, use the SAME provider gate, per-user/global budgets,
timeouts, authorization and cleanup. No extra provider allowance. Question
regeneration and successful deletion/reindexing invalidate old inline threads;
logout/expiry revoke access. Private source material and conversation state are
never shared across users; only immutable public model weights remain shared.
Rendering uses one authorized bulk answer read rather than repeated authorization
HTTP calls per question; this is not a process-wide private-content cache.

## Verification and remaining limits

Initial complete regression/security suite: **436 passed, 1 skipped**, zero
failures, in **842.72 seconds (14:02)**. Command: isolated Python 3.12
`-B -m pytest tests -q -p no:cacheprovider`, with the public cached MiniLM models
and a new disposable test-output directory outside this repository. No existing
workspace, uploaded PDF, index, database or cloud resource was used as test data.
The skip is `test_symlink_cleanup_fails_closed`: this Windows host does not permit
creating test symlinks (confirmed separately with `-rs`). That OS-specific
symlink-cleanup case remains unverified here; the skip is not a security pass.

Coverage includes native synthetic multi-page ingestion/context packing,
idempotent uploads, retry/cancellation/rerun states, complete-passage and prompt
budgets, inline answer streaming/citations, corrupted/foreign source metadata,
two-user ownership, logout/expiry and bounded inline threads. Provider calls are
mocked; native parsing/indexing/embedding/reranking are exercised with real cached
public models. The initial integration file covered 18 executed cases; final
review extends it to 24 cases.

Verification history: an earlier full run had one outdated positional widget
selector failure (433 passed, 1 skipped, 1 failed). The test was updated to use
the new explicit source-search widget keys, passed individually, and passed in
the final suite. A host-interrupted run was abandoned and is not counted as an
acceptance result. No security/resource limit was loosened to obtain a pass.

Completed final-source checks:

- Repository-wide Ruff and `git diff --check`: passed (Git reports only its normal
  Windows LF/CRLF conversion warnings).
- Python 3.12: all 52 candidate modules and native imports passed; no Pinecone,
  Cloudinary or persistent-cloud provider module was loaded.
- All 13 pinned dependencies and 44 applicable direct dependency constraints:
  passed in the isolated verification runtime.
- All 118 nonignored source files passed syntax, forbidden-artifact and
  credential-pattern checks. This is a pattern scan, not a guarantee against all
  possible secrets. No real environment/Secrets files were opened.
- Feature branch, unchanged protected application/configuration paths and main,
  and initially empty Git staging area: passed. Publication authorization is
  limited to a verified feature-branch commit/push as described above.

All provider tests use dummy keys and mocked HTTP/SSE. No secure preview credential
source was available to this agent; **no real Groq/OAuth request is claimed**.
Native synthetic parsing/indexing/reranking tests use the original cached models.

Browser inventory returned no available browser surfaces. A separate loopback-only
dummy UI harness was started and stopped, without accessing preview credentials
or cloud resources; it was NOT visually tested. AppTest checks widget state and
ordering, not actual CSS geometry, pixel quality or mobile keyboard behavior.
The selectors used exist in the pinned Streamlit 1.54 frontend, but desktop/mobile
geometry still needs manual acceptance. No claim of visual or production readiness.

### Final review and reverification — 2026-10-18

- **442 passed, 1 skipped**, zero failures, in **437.87 seconds (7:17)**, on the
  final corrected source. The same Windows symlink-permission skip remains.
- Focused non-native integration checks: **23 passed, 1 deselected**; the native
  multi-page case then passed in the complete suite. All 24 integration cases
  were exercised there. No real OAuth/Groq request was made.
- Rechecked repository-wide Ruff, whitespace, 52 module/native imports, 13 exact
  dependency pins, 44 direct dependency constraints, and all 118 nonignored
  source files for forbidden artifacts and credential patterns: passed.
- All 117 non-report source-file hashes matched the full-suite source snapshot.
  Main/rollback and protected application/configuration paths remained unchanged.

Review verified citation renumbering against the final complete passages, actual
filename/page/excerpt provenance, rejection of removed-source markers, and
fail-closed behavior when the strongest passage for each represented document
cannot fit. Optional context selection is disclosed; this does NOT prove that
every semantically necessary fact was retrieved or that an answer is exhaustive.

Two small review fixes were included:

1. A prompt-packing metadata dictionary can no longer mask the notice for an
   earlier context-packing omission. Tests cover either stage and their
   combination, including no-omission results. Packing limits/algorithms remain
   unchanged by this review.
2. Deletion retains the handled file-widget identity. Clearing it while the same
   PDF remained selected caused a second automatic ingestion attempt on rerun.
   A regression reproduced two attempts before the fix; now deletion and further
   reruns cause no new attempt and show no stale ready message. New explicit
   selection/retry still uses normal ownership and quota checks. Native index
   removal is covered separately; this UI regression uses a fixture snapshot.

An in-progress suite was stopped when the deletion issue was discovered; it is
not counted as final verification. Only the complete corrected run above is the
acceptance result. Inline answers remain separate from manual Chat/history and
introduce no tab-navigation action. Actual browser tab focus/geometry and real
provider/OAuth behavior remain pending the manual checks below.

### Manual acceptance on an approved preview update

1. Desktop (1366×768), mobile (390×844), short landscape viewport: verify header,
   tab navigation, fixed-in-panel composer, single conversation scrollbar and no
   overlay/clipping. Narrow tables scroll within the answer, not the whole page.
2. Keyboard: Tab/Shift+Tab, navigation arrows, citation focus/popovers, sidebar
   collapse and New Chat. Check readable contrast, wrapping and mobile keyboard.
3. Upload non-sensitive digital PDFs: automatic status → ready; reselect identical
   bytes, rerun, change names, interrupt and retry. No duplicate documents/charges;
   malformed/scanned/oversized processing fails explicitly, never falsely ready.
4. Ask several questions inline, including a provider failure/cancellation; rerun
   and change topic. No automatic AI retry, Chat switch, history contamination or
   incomplete completed answer. Inspect actual excerpt/page citation accuracy.
5. Generate Source Guides, Collection Brief and all remaining tools. Brief covers
   the whole indexed collection. Errors/retries stay by the relevant result.
6. Two independent Google users: different PDFs, queries, inline answers, source
   panels and histories; foreign references fail. Logout/expiry, deletion/reindex
   and interruption must revoke/invalidate as appropriate. Do not reset live quotas.
7. Verify real Groq Free streaming/usage and answer quality with an approved tiny
   test budget. Measure total Cloud memory, cold models, latency and representative
   PDF sizes; these remain unverified. Stay on free plans; never auto-upgrade.

## Exact repository files changed

- `.gitignore`
- `session_cloud/analysis_service.py`
- `session_cloud/analysis_view.py`
- `session_cloud/chat_view.py`
- `session_cloud/comparison_view.py`
- `session_cloud/context_engine.py`
- `session_cloud/context_packing.py` (new)
- `session_cloud/exploration_view.py`
- `session_cloud/generation.py`
- `session_cloud/index_workspace.py`
- `session_cloud/inline_answers.py` (new)
- `session_cloud/question_answers.py` (new)
- `session_cloud/runtime.py`
- `session_cloud/streamlit_app.py`
- `session_cloud/theme.py`
- `session_cloud/upload_workflow.py` (new)
- `tests/test_answer_context.py`
- `tests/test_exploration.py`
- `tests/test_ux_integration.py` (new)
- `session_cloud/UX_IMPROVEMENTS_REPORT.md` (this report)

Requirements and root Streamlit configuration are unchanged. The visual harness
and generated test PDFs/databases are outside the repository in an isolated test
directory and are not deployment files.
