# Phase 2C1: verified answer-evidence foundation

**Phase 2C is not complete.** This is its first explicitly scoped milestone:
original reranking, context expansion, prompts and citation-attribution contracts.
Groq generation/streaming, completed/interrupted turn handling, provider follow-up
calls and their quotas remain for 2C2. Chat stays disabled; seven analysis features
stay gated for Phase 3. No fabricated answer, activity or follow-up is displayed.

Work is only on `feat/temporary-cloud-workspaces` in the clean publishing clone.
The original Docker project, deployed entrypoint, real Secrets, main and rollback
are unchanged. No commit, push, deployment, cloud-resource or billing operation.

## Original code reused

- `app/retrieval/reranker.py`: original
  `Xenova/ms-marco-MiniLM-L-6-v2`, FastEmbed ONNX runtime, logits, sorting and top-k
  method. Constructor/cache lifecycle is injected; the scoring method's AST is
  identical. An adapter schedules **one pair per batch instead of 64**, rejecting
  missing/non-finite scores instead of silently returning a shortened zip.
- `context_assembler.py`: original metadata projection, neighbor-before-parent
  order, sibling ordinal sorting, deduplication, inherited scores and nominal
  4,500 stored-token-cost expansion. Replace only global SQL acquisition with
  already-authorized snapshot rows. Missing document titles fall back to the
  actual filename, as in the original; no titles/pages are invented.
- `retriever_node.py`: original top-25 hybrid prefix, top-20 cross-encoder
  candidates, best-score merging across sub-questions, final top-6 default and
  expansion order. Sub-questions are serialized to bound CPU/model concurrency,
  rather than using the original up-to-four parallel branches. Any failed
  branch rejects this preview; no unreranked or partial fallback is substituted.
- Original aggregator/evidence labels, answer system prompt, six-message history
  builder, untrusted-document policy/wrapper, decomposition template/heuristic,
  quote template, follow-up template and legacy confidence helper are preserved.
  No LangChain/global deployment settings/provider client is imported. AST checks
  verified the answer/history functions, aggregator, evidence scoring and rerank
  method. Source digests are recorded in `source_manifest.json`.
- Original quote sentence scoring/selection and citation field schema are reused.
  A provider fallback is explicitly injected per request, not globally cached;
  2C1 deliberately has **no provider implementation**. Short quotes requiring it
  report that requirement rather than inventing a replacement. Original marker
  parsing and existing sanitized frontend source rendering are reused.

## Security and behavior

`WorkspaceStore.prepare_answer_context` checks the verified Supabase identity,
opaque capability, registered workspace, selected indexed document ownership and
limits before work, and reauthorizes before returning evidence. Workers receive
only server-derived snapshot paths and selected document sets. Every projected
child, neighbor and parent stays within the selected authorized corpus. This is
not authorization based on a browser ID or on Streamlit Session State.

One bounded worker shares model/tokenizer assets across users, **not documents,
queries, indexes or chat state**. The existing heavy-operation gate still allows
one native operation; concurrent requests fail busy instead of mixing buffers.
Embedding and reranker caches each have a 160 MiB disk cap. Both models share
the existing 640 MiB Windows job-memory guard; Linux still uses its documented
virtual-address/file limits and sampled RSS monitor. The 120-second request
deadline includes lazy model initialization. PDF parsing releases idle models.
Provider secrets are not forwarded into this worker; child diagnostics remain
discarded/sanitized.

Retain the original expansion policy, but reject complete evidence beyond
50,000 characters or **8,192 untruncated MiniLM tokens**. Safety counting clones
public tokenizer configuration and disables truncation/padding on that clone;
the shared tokenizer is never mutated. This is **not a Groq-token quota**.
The original stored-token counts can undercount whole context: the corpus's
nominal 4,500-cost expansion produced 3,535–7,606 untruncated MiniLM tokens.
That discrepancy is measured and visible; the candidate does not silently alter
chunking or drop context to hide it. 2C2 must budget the complete model-specific
prompt/history and every provider call conservatively before enabling requests.

Citation validation rejects out-of-range/zero markers and quote text not present
in the actual numbered passage. Original whitespace/80-word ellipsis presentation
is accounted for; filenames, page ranges and snippets come only from that passage.
This is provenance/literal-text validation, **not proof of semantic entailment**.
It is stricter than the original formatter's unchecked LLM quote fallback.

The existing search form has an explicit **Preview original reranking and
expanded answer evidence (no AI request)** option. It displays true passages,
filenames/page ranges and labels neighbor/parent scores as inherited relevance
signals, not confidence probabilities. Original chat/activity/citation UI remains
intact but generation is still gated. Existing history is not replaced or filled
with fake/incomplete answers.

A pre-existing disk-monitor race was fixed: atomic archive renames could remove
a listed file before `stat()`, rejecting otherwise valid work. On that exact
condition, restart the **entire** size scan, bounded to three attempts. Persistent
errors still fail closed; caps and ownership checks are unchanged.

## Actual evaluation

`evaluate_answer_context.py` reuses the identical known-answer three-PDF,
18-page, twelve-question corpus from 2B. `PHASE2C1_EVALUATION.json` records results.
The reference executes original source functions over the same verified snapshot,
with predicate-filtered fixture rows replacing the SQL reader and original
batch-64 cross-encoder inference. Candidate batching is one. This isolates the
reranking/context comparison; it is not another independent ingestion benchmark.
Runtime: Windows x64 / Python 3.12.4, matched Docker retrieval dependencies:
FastEmbed 0.9.0, FAISS 1.12.0, NumPy 2.4.6, ONNX 1.31.0, tokenizers 0.23.3.

| Check | Original reference | Candidate |
| --- | ---: | ---: |
| Reranked recall@6 | 1.0000 | 1.0000 |
| MRR@6 | 1.0000 | 1.0000 |
| Questions evaluated | 12 | 12 |

Selected-child order matched 12/12; expanded-context order matched 12/12. No
corpus request was blocked by the additional hard evidence guard. Deterministic
extractive fixtures passed **12/12 citation provenance checks**. These are not
Groq answers and do not establish generated-answer/citation semantic accuracy.

Measured candidate worker peak RSS: **435,765,248 bytes / 415.58 MiB**, containing
both model instances and request work. First context request: **5.875 seconds**;
median **4.867 seconds**. Cached-weight reranker cold initialization was **0.484
seconds**, with the embedding worker already warm from indexing. Its first
retrieval/rerank/expansion stage took 5.172 wall / 4.875 CPU seconds. These are
mock-auth, native evidence-preparation timings, **not** first-token or Groq
response latency. No cold network download/cloud resource measurement is claimed.

## Verification

Final full suite: **242 passed, 1 skipped in 143.89 seconds**. The skipped
test requires Windows symlink-creation permission; its Linux negative-path
behavior has not been runtime-verified here.

Native version assertions prevent Python silently falling back to old packages.
The first attempted runs used such a fallback because a prior isolated runtime
had sandbox-denied files; they were stopped and are not counted as matched-stack
verification. A fresh dedicated test environment restored the exact declared
versions without changing the original environment.

Ruff, syntax/standalone imports, native version checks and original-function AST
checks pass. No `app.config` or original database service is imported. Linux
Python 3.12 binary resolution succeeded for FAISS 1.12, NumPy 2.4.6, FastEmbed
0.9, ONNX 1.31 and tokenizers 0.23.3; **Linux execution is still unverified**.
No source credentials/private-artifact patterns were found; nothing is staged.

## Differences, risks and next milestone

- No live Groq requests, streaming tokens, provider rate-limit/timeouts, follow-up
  generation or partial-turn persistence have been implemented/verified here.
  Existing stream-contract unit tests are not evidence of an actual Groq stream.
- 2C2 must connect genuine bounded Groq streaming using the preserved prompts;
  issue observable activity events; atomically save only completed validated
  turns; separately label interrupted drafts; preserve chat-owned selections;
  cancel/revalidate on logout/expiry; budget decomposition, answer, quote
  fallback and follow-up calls, including failure/concurrency cases. No automatic
  retries that could invisibly multiply free-tier usage.
- Prompts/citations provide safeguards, not a guarantee that a model cannot
  hallucinate or follow injected prose. The runtime has no tools/provider keys
  in document prompts. Test actual hostile-document completions in 2C2.
- Cached models add ~100 MiB relative to the 2B sample. Child guards do not cap
  the whole Streamlit host or uploads already buffered by Streamlit. Free-host
  memory/CPU, simultaneous users, 100 MiB processing and actual OAuth/browser
  sessions still need live verification; no free-cloud readiness/parity claim.
- Restart any separate candidate preview after worker protocol changes. Do not
  switch the live entrypoint, change real Secrets, deploy or advance to Phase 3
  on the strength of this milestone alone. All work stays uncommitted.

## Exact file scope

New: `session_cloud/original_reranker.py`, `original_context.py`,
`original_prompts.py`, `original_quotes.py`, `original_evidence.py`,
`context_engine.py`, `attribution.py`, `disk_usage.py`,
`evaluate_answer_context.py`, `PHASE2C1_REPORT.md`, `PHASE2C1_EVALUATION.json`,
and `tests/test_answer_context.py`.

Updated: `.gitignore`, `session_cloud/index_contract.py`, `index_service.py`,
`index_worker.py`, `index_workspace.py`, `runtime.py`, `streamlit_app.py`,
`requirements.txt`, `secrets.toml.example` (comment only), `source_manifest.json`
and `README.md`. Existing Phase 1/2A/2B changes are preserved.

Reference documentation: [FastEmbed rerankers](https://qdrant.tech/documentation/fastembed/fastembed-rerankers/)
and [tokenizers Linux wheels](https://pypi.org/project/tokenizers/0.23.3/).
