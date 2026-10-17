# Phase 3B — original Contradictions and Knowledge Gaps

Implemented only in the separate `feat/temporary-cloud-workspaces` candidate.
No commit, push, deployment, credential changes, external provider requests or
cloud-resource operations. Original Docker source was read only. Protected
deployed application files, main and rollback remain unchanged.

## Original behavior preserved

Read-only reference: original `app/reasoning/contradiction_graph.py`,
`gap_graph.py`, their API routes and `frontend/app.py` analysis tabs. Reference
SHA-256 hashes are recorded in `source_manifest.json`.

- Both analyses operate on **all indexed PDFs in the authorized workspace**,
  independent of Chat's document selector. Accepted/prepared-only files are not
  inputs. Contradictions retains the original UI's two-distinct-filenames gate;
  server execution also requires two documents with usable paragraph evidence.
- Contradictions loads up to six L3 chunks/document and actually compares the
  first five, each sliced to 300 characters and joined with `\n---\n`.
  Every unordered document pair is compared in original document order.
  The scientific fact-checker prompt explicitly requires direct contradictions
  and excludes differences in emphasis, scope and context. System policy,
  evidence wrappers, JSON fields, temperature zero and 1024-token output remain
  unchanged. The original **0.4 threshold and descending confidence ordering**
  are retained. No arbitrary score increase or replacement scoring formula.
- Knowledge Gaps uses the first L1 summary, falling back to the first three L3
  paragraphs joined with **spaces**, then slices to 1500 characters/document.
  The exact research-librarian system/human prompts and 3–6-gap instruction,
  topic/description structure, temperature 0.3 and 1024-token output are reused.
- The original comparison expander/two-column/VS cards and yellow gap cards
  are restored. Original card HTML is compared directly in tests, with all
  document/model values escaped. Native expander labels also escape Markdown
  image/link syntax.

These original algorithms **do not use hybrid retrieval or cross-encoder
reranking**. Reading their actual L1/L3 samples from the authenticated, sealed
FAISS/BM25 snapshot preserves the algorithm; substituting RAG hits would not.
Existing chat retrieval/reranking and Source Guide/Collection Brief remain intact.

## Safety and intentional differences

- New analyses are explicitly user-triggered, never automatically run after
  indexing or on Streamlit reruns. Completed results are stored only in the
  workspace SQLite database. Reads/replays do not invoke Groq.
- Existing opaque capability/auth verification, workspace registry, bounded
  native worker and heavy-operation gate are reused. No browser-supplied path,
  project ID or model-generated ownership assertion is authoritative. Provider
  IDs/filenames are discarded in favor of server-owned pair metadata.
- Every native snapshot file is hash-verified before loading. Missing evidence,
  corrupt indexes, foreign provenance and changed revisions fail closed; they
  are not converted to successful empty findings. Revision checks occur before
  sampling, after sampling and at the atomic completed-result transaction.
- Chat, guides and these features share one generation gate and the same
  verified-owner/global request/token reservations. A three-document scan makes
  three sequential provider calls; each counts, including failures. The
  existing 180-second request / 45-second provider deadlines, bounded queues,
  context reservations and cancellation/timeout behavior remain in force.
- Provider responses require complete SSE termination, bounded JSON arrays,
  valid fields and finite 0–1 confidence. Malformed or interrupted output,
  provider failure, budget exhaustion or a failed later pair cannot publish
  partial findings. Earlier still-current results survive failed rescans.
  New calls use explicit new request IDs; reruns never resend interrupted work.
- Fabricated citation markers are rejected in raw and **decoded JSON** fields,
  including Unicode-escaped markers. These original analyses assign no numbered
  citations. Source panels therefore show **input provenance**, not invented
  claim-level citations. Exact sampled matches are distinguished from model
  paraphrases; a paraphrase is not presented as a verified quotation.
- Findings are labeled **potential contradictions / potential knowledge gaps**.
  The original confidence value is exposed as an **uncalibrated model signal**,
  not a probability. No-findings is distinct from never-run/stale/failed state.
  Summary-level gaps are not asserted to prove full-document absence, an
  unsupported claim, or a retrieval failure. These limitations are intrinsic to
  the original algorithm and are not hidden behind new generic summaries.
- Original payload field names are retained, with server-generated candidate
  request-scoped item IDs, a common UTC run timestamp and extra provenance /
  metrics. Gap items retain model order rather than relying on original SQL
  per-row timestamp/tie ordering. No public legacy API or schema is modified.
- Index replacement/deletion already invalidates all analysis results in its
  transaction, including same-content reindexing. Fingerprints also reject
  old replays. Logout/expiry revoke access and suppress in-flight publication.
  Temporary data remains non-recoverable after server restart, as designed.

## Verification and measurements

See `PHASE3B_EVALUATION.json` for recorded native-fixture measurements. The four
contradictory/consistent/incomplete/unrelated two-PDF fixtures exercise genuine
PDF preparation, original chunking/embedding, complete index publication and
owned source sampling. **All Groq responses are fixed HTTP/SSE mocks.**
Original loader/prompt/threshold/parameter and UI comparisons evaluate the
actual read-only original AST, avoiding original credentials/global services.

Measured warm analysis steps: Contradictions **0.203–0.344 seconds**, Gaps
**0.125–0.250 seconds**. Native worker lifetime peak **330,866,688 bytes /
315.54 MiB**. Each two-document analysis makes one mocked provider call.
Reserved input-plus-output upper bounds are 2752–2765 for a comparison and
2561–2574 for gaps; real provider token consumption is unknown. These are NOT
real Groq latency, total application memory, cold-start or 100 MB PDF results.

Automated checks cover two-user ownership/cache isolation, original selection
and pair semantics, invalid/missing evidence, corrupt indexes, resource and
budget failures, provider auth/rate/timeout failures, interrupted streams,
concurrent requests, cancellation, logout, expiry, replay, failed rescan
preservation, same-checksum reindex/delete invalidation, HTML/Markdown safety
and actual Streamlit explicit-click/rerun behavior.

Final matched-stack full regression: **363 passed, 1 skipped in 277.01 seconds**,
including **42 Phase 3B tests**. The skip is the existing Windows test requiring
OS symlink-creation permission; no Linux pass is claimed. An earlier full run
passed 360 tests plus the same skip before the final three regressions were
added. All final application changes are covered by the last complete run.

Ruff (`session_cloud` and `tests`), standalone/native imports, syntax/whitespace,
all 13 exact dependency pins and their applicable direct runtime constraints
passed. Source credential/private-artifact pattern scanning passed for all 102
non-ignored source/report files, including the explicitly allowed new test.
Nothing is staged. Protected deployed files have no diff against main; main,
rollback and HEAD still point to `dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`.
These checks are not an exhaustive credential proof, Linux install test or
live cloud/provider verification. No new dependency was required.

## Files for this milestone

New: `original_comparisons.py`, `comparison_service.py`, `comparison_view.py`,
`PHASE3B_REPORT.md`, `PHASE3B_EVALUATION.json`, `tests/test_comparisons.py`.

Updated: `.gitignore` (allow this dummy-only test source, not test artifacts).
Updated within `session_cloud/`: `analysis_workspace.py`, `analysis_service.py`,
`analysis_view.py`, `index_worker.py`, `runtime.py`, `streamlit_app.py`,
`source_manifest.json`, `README.md`. No new dependency or schema migration.
Prior-phase changes and unrelated working-tree files were preserved.

## Remaining work / blockers

Phase 3C: original Smart Questions, Insights and Timeline (still gated), with
the same ownership, provider budgets, explicit triggers and invalidation rules.

Before replacing any live deployment: approve/run real Groq quality tests,
real Google OAuth and simultaneous two-browser isolation, cancellation/logout,
Linux/Community Cloud cold starts, total-process memory/concurrency and
representative large-PDF tests. The existing 100 MB upload ceiling is NOT a
promise that every 100 MB PDF can be processed on free hosting. OCR remains
explicitly unavailable in this candidate. Process-local quotas reset on process
restart; provider free-account limits remain necessary. No production-readiness
or live semantic parity claim is made. No paid provider, new dependency, or
billing was introduced. Candidate worker protocol changed: restart only an
approved local candidate preview, not the current deployed application.
