# Phase 2B: original hybrid retrieval in temporary workspaces

Branch: `feat/temporary-cloud-workspaces`. No commit, push, deployment, Secrets
change, cloud resource change, or original Docker project edit was performed.
`main` and `rollback/authenticated-cloud-dec268c` remain at
`dec268c0fdb0e3b6183e7e0c75cd95d9d6cf959c`.

## Implementation and provenance

- Import the existing, byte-identical `app/ingestion/chunker.py` and
  `app/retrieval/rank_fusion.py`. Chunking retains sentence/table handling,
  220-token children, 35-token overlap, 800-token parents, summaries, section,
  page range, child ordinal, OCR/table flags and parent relationships.
- Copy original `Embedder`, `VectorStore` and `BM25Store` into the candidate.
  Constructors inject explicit settings/cache/private paths and remove global
  singletons. An AST comparison verified other methods unchanged, apart from
  substituting the injected storage root for global configuration. Source
  SHA256 values are in `source_manifest.json` and the evaluation JSON.
- Use original `sentence-transformers/all-MiniLM-L6-v2` through **FastEmbed ONNX**,
  not the PyTorch sentence-transformers package. Token counting uses the original
  helper/tokenizer behavior. Startup rejects a missing actual tokenizer.
  Dimension is 384; original float32 L2 normalization remains unchanged.
- Use original HNSW inner-product search: M=32, efConstruction=200, efSearch=50.
  BM25 remains `BM25Okapi` with lowercase whitespace tokenization, positive-score
  filtering and original tie order. Search top 30 from each, then original
  RRF k=60. Document selection restricts the corpus before either search.
- Match the Docker retrieval lock: FastEmbed 0.9.0, FAISS CPU 1.12.0,
  NumPy 2.4.6, ONNX Runtime 1.31.0 and rank-bm25 0.2.2. Candidate requirements
  also use PyArrow 26 with NumPy 2; the older cloud NumPy 1/Arrow 26 ABI mismatch
  is not inherited. No existing dependency environment was modified.

Two explicit adaptations (not silent algorithm replacements):

1. Remap original random chunk UUIDs to deterministic hashes of document,
   occurrence and unchanged chunk text; remap parent links too. Reindexing an
   unchanged PDF preserves citation targets and metadata.
2. Schedule the original embedder in batches of **8**, rather than Docker's
   default 128, to bound activation memory. All texts are processed; no batch
   is published independently. The independent comparison below measures the
   effect against the original 128-item schedule.

## Atomicity, isolation and controls

Only Phase 2A prepared pages may be indexed. A complete candidate snapshot
contains original FAISS/BM25 archives, complete chunk metadata, float32 vectors
and a hash manifest. Publish the manifest checksum and snapshot pointer together
with indexed document status in one private SQLite transaction. A failed build,
reindex or revoked login cannot publish a partial snapshot; the prior complete
index stays authoritative. No pickle loading is used. Hashes are anchored in
server-owned SQLite before native FAISS deserialization.

Every operation revalidates the existing Supabase-issued identity and opaque
login capability against the server registry, including before publication and
returning search results. Caller workspace/document claims are not ownership.
Each snapshot directory derives from that registry. Only the model/tokenizer
is retained by the serialized worker; stores, queries, vectors and results are
request-local. No cross-workspace retrieval cache is kept. Two users share one
model PID, not indexes. Parsing another PDF releases idle model residency before
starting the separately bounded parser; the model is loaded again on demand.

Selection, reindexing and deletion rebuild complete selected/remaining corpora.
Deletion atomically replaces/removes the active snapshot before returning, so
old FAISS/BM25 hits cannot survive a successful delete. Heavy native work does
not hold the registry lock, so logout can revoke work in progress. Existing
temporary chat history is preserved; no AI answers are produced here.

Limits: two active workspaces, three PDFs/workspace, 1,200 retrieval children,
3 MB prepared text, 16 MiB/snapshot; existing 128 MiB/workspace and 256 MiB total
tracked workspace disk budgets remain. Snapshot builds reserve temporary disk
before starting. Public model cache has a separate 160 MiB cap. One heavy
operation runs at a time; competing operations receive a truthful busy error.
Six indexing attempts/account/day, twenty/runtime/day; sixty retrieval attempts/
account/day, two hundred/runtime/day. Failed attempts count; reset is UTC day or
process restart. These are not durable, cross-restart anti-abuse counters.

Each worker request has a 120-second wall deadline. Failure/revocation kills the
worker, discards request/result files and leaves publication unchanged. Windows
uses a 640 MiB job/process committed-memory cap. Linux uses a 3 GiB virtual-address
cap plus a 50 ms sampled 640 MiB RSS monitor (not a hard cgroup RSS cap). Linux
file size is bounded to 160 MiB during public model initialization, then reduced
to 16 MiB for snapshot work. Applying 16 MiB before the ~90 MB model download
would block first-run initialization; the order has a focused regression test.
Child stdout/stderr are discarded, provider environment credentials are not
forwarded, and supervisor diagnostics log only fixed stages/codes/numeric exits.

The sidebar now exposes Index/Reindex, indexed-only selection and Delete.
The Chat tab includes an explicit retrieval-inspection form. Passage panels
display actual filenames, page ranges and plain text, not untrusted HTML.
The original seven-tab shell remains. Generation/analysis controls stay gated.

## Measured comparison

Evidence: `PHASE2B_EVALUATION.json`; reproducible driver
`python -B -m session_cloud.evaluate_retrieval`. It requires a read-only original
source root, a separate public MiniLM model copy and a **new** output directory.
It never loads the original app configuration, .env, documents or databases.

Three generated non-sensitive PDFs, 18 pages, 25 searchable children; twelve
known-answer questions covering exact identifiers, semantic paraphrases and two
multi-PDF questions. Relevance is presence of the labeled supporting topic in
the retrieved text. Recall measures the fraction of expected topics found in
top k; MRR@30 uses the first relevant hit. This is a small synthetic benchmark,
not a broad production relevance evaluation.

| Metric | Original source baseline | Temporary candidate |
| --- | ---: | ---: |
| Recall@1 | 0.9167 | 0.9167 |
| Recall@5 | 1.0000 | 1.0000 |
| Recall@10 | 1.0000 | 1.0000 |
| MRR@30 | 1.0000 | 1.0000 |

All child boundaries matched. All twelve complete returned rankings matched
after mapping random IDs to document/page/text. Baseline executes independent,
unchanged original class/function bodies, with isolated storage/configuration
and the original 128-item inference schedule. Both use the Docker-locked native
retrieval versions and identical public model bytes. Test host is Python 3.12.4,
Windows x64, **not the live Docker binary or Linux deployment**. This establishes
sampled pre-rerank hybrid parity only, not complete RAG or answer-quality parity.

Measured candidate build stages (chunking + embedding + serialization; excludes
model initialization and real authentication):

| Snapshot children | Wall seconds | CPU seconds | Worker lifetime peak RSS |
| --- | ---: | ---: | ---: |
| 8 | 1.937 | 1.781 | 314.23 MiB |
| 17 | 4.485 | 3.688 | 313.52 MiB |
| 25 | 5.547 | 5.094 | 314.41 MiB |

Store retrieval wall latency: median 0.125 s, max 0.156 s, with mocked identity
verification and no provider generation. Native PDF preparation peaks were
about 147 MiB. Evaluator host peak was 378.51 MiB, including its independent
baseline model; this is **not** deployed Streamlit RAM. Peaks are separate, not
an actual simultaneous whole-application measurement. No 100 MiB PDF was parsed
and indexed in this evaluation.

## Verification

Final full suite on the Docker-aligned candidate libraries: **217 passed,
1 skipped in 94.97 seconds**. The skip is Windows denying creation of the
test symlink; the Linux symlink negative test still needs execution there.
Coverage includes actual MiniLM/native indexes, two users sharing a model but
not results, concurrent busy rejection, atomic first-index/reindex failures,
identity changes before publication, stable IDs, selection/deletion, corruption,
logout/expiry/fresh-authority restart, malformed requests/PDFs, actual child
timeout/memory limits, disk/quota limits, model release before parsing, sanitized
child diagnostics/environment and independent Streamlit AppTest sessions.

Ruff and standalone imports/syntax passed. Final credential/artifact pattern
scan found no detected credentials or private data; nothing is staged. This is
a pattern scan, not a guarantee against every possible secret format.

Already completed: independent real MiniLM comparison; Ruff; AST/syntax and
standalone imports without `app.config`; all candidate pinned package versions
match the private test interpreter. Linux Python 3.12 binary resolution passed
for FAISS 1.12, NumPy 2.4.6, FastEmbed 0.9, ONNX Runtime 1.31, PyMuPDF 1.26.7,
PyArrow 26 and BM25. This is wheel availability, **not Linux runtime execution**.
The first broad resolver exceeded its verification time bound; explicit native
wheel resolution subsequently succeeded. No Linux/container/cloud app was run.

Earlier verification caught and fixed deep Windows archive-path failures,
an outdated UI expectation that prepared meant searchable, and a fixture that
did not fit on its PDF page. One earlier old-library run had a fail-closed
resource rejection that did not recur in the subsequent full runs; no security
guard was bypassed. Explicit memory/time/disk failures are tested as rejections.

## Remaining limits and Phase 2C

- A 100 MiB upload ceiling is not a processing guarantee. Existing page, text,
  OCR, chunk, disk and time limits may reject it completely. Streamlit buffers
  uploads before application admission, so host OOM cannot be ruled out merely
  by child caps. Large-file and simultaneous browser-upload profiling on the
  intended free Linux host remains required. OCR is still off by default.
- Linux execution, first cold model download, actual cgroup behavior and live
  Google/browser sessions have not been verified in this milestone. Official
  Community Cloud resource figures are variable, not a guaranteed RAM budget.
- Registry restart tests verify fresh-authority semantics; no live Streamlit
  host restart was rehearsed. Old roots become inaccessible, but physical bytes
  may remain until host recycling. No cross-restart recovery/erasure claim.
- Restore original cross-encoder reranking, context assembly/parent-neighbor
  expansion, prompts/citation validation, genuine Groq streaming/interruption
  handling, follow-ups, and AI usage budgets in 2C. Advanced analysis remains a
  later explicit integration. No real Groq request was made in 2B.
- Do not switch entrypoints, push main or deploy this candidate without review
  and approval. No paid service is required or enabled by these changes.

Native/reference documentation checked:
[FAISS wheels](https://pypi.org/project/faiss-cpu/1.12.0/),
[FastEmbed](https://qdrant.github.io/fastembed/),
[ONNX Runtime wheels](https://pypi.org/project/onnxruntime/1.31.0/),
[PyMuPDF wheels](https://pypi.org/project/pymupdf/1.26.7/),
[Streamlit resource limits](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app#resource-limits).

## Exact Phase 2B file scope

New: `session_cloud/original_embedder.py`, `original_vector_store.py`,
`original_bm25_store.py`, `index_contract.py`, `index_engine.py`,
`index_workspace.py`, `index_service.py`, `index_worker.py`,
`evaluation_corpus.py`, `evaluate_retrieval.py`, `PHASE2B_EVALUATION.json`,
`PHASE2B_REPORT.md`, and `tests/test_session_retrieval.py`.

Updated: `.gitignore`, `session_cloud/runtime.py`, `streamlit_app.py`,
`requirements.txt`, `README.md`, `source_manifest.json`, and
`tests/test_pdf_preparation.py`. Other Phase 1/2A uncommitted work is preserved.
Deployed `authenticated_cloud`, original packaged `app`/`frontend`, root config,
main/rollback refs, and the original Docker repository remain unchanged.
