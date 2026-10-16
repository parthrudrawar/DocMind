# Deployment packaging report

Prepared folder: `C:\Users\rudra\Documents\Codex\documind-cloud-deployment`

This is a separate, uninitialized GitHub-ready folder, not a deployment and not
an initialized Git repository. The original Docker repository and existing files
were only read. No real environment files, credentials, PDFs, databases, indexes,
models, virtual environments or Git metadata were copied.

## Contents

Exactly 30 files:

```
.gitignore
.streamlit/config.toml
README.md
PACKAGING_REPORT.md
authenticated_cloud/__init__.py
authenticated_cloud/auth.py
authenticated_cloud/providers.py
authenticated_cloud/service.py
authenticated_cloud/streamlit_app.py
authenticated_cloud/schema.sql
authenticated_cloud/requirements.in
authenticated_cloud/requirements.txt
authenticated_cloud/secrets.toml.example
authenticated_cloud/README.md
authenticated_cloud/VERIFICATION.md
authenticated_cloud/component/index.html
authenticated_cloud/component/auth.js
authenticated_cloud/component/style.css
community_cloud/__init__.py
community_cloud/service.py
app/__init__.py
app/ingestion/__init__.py
app/ingestion/chunker.py
app/reasoning/__init__.py
app/reasoning/citations.py
app/reasoning/prompt_safety.py
app/retrieval/__init__.py
app/retrieval/rank_fusion.py
frontend/__init__.py
frontend/citations.py
```

The README's runtime allowlist was used, with 26 source files copied byte-for-byte.
The Streamlit configuration was copied from `community_cloud/cloud_config.toml`
to `.streamlit/config.toml`. Only the dummy Secrets example was included.
The development-only `requirements.tests.txt`, test modules and test outputs
were intentionally omitted: they are not application/runtime dependencies.
The root README, strict ignore policy and this packaging report are new.

## Packaging verification performed

- PASS: SHA-256 equality of all 26 copied files with their source originals;
  Streamlit configuration also matches its source exactly.
- PASS: 10 local modules import from THIS folder, in an isolated Python 3.12
  subprocess, with network access and imports of `app.config`, the Docker PDF
  parser, FAISS, BM25, FastAPI, Celery, Redis and SQLAlchemy explicitly blocked.
- PASS: all 63 requirements are exactly pinned; each matches its version in
  the existing isolated Python 3.12 tooling environment. Required FastEmbed,
  Groq and PyMuPDF classes import without creating models or provider clients.
- PASS: Python AST parsing, component JavaScript syntax, TOML/config assertions
  and Ruff checks on all packaged Python source.
- PASS: synthetic PDF parsing -> explicit-settings chunking -> escaped Markdown
  and citation rendering entirely in memory. No user files or AI calls used.
- PASS: actual Git ignore evaluation with 31 synthetic sensitive/artifact paths
  excluded and 10 required source/config/template paths included. Git validation
  used private temporary metadata outside this folder, removed afterwards;
  this folder was NOT initialized as a repository.
- PASS: dummy template values verified before copying; package scanned for
  credential patterns, actual secret-file names, data/model artifacts and symlinks.
  No matches. Such scans supplement the explicit allowlist, not a guarantee
  that future edits cannot introduce secrets.

Streamlit emitted its expected missing-ScriptRunContext warning when the
entrypoint was imported outside a running UI; this did not invoke `main()` or
load real Secrets. The prior application's `VERIFICATION.md` is historical
development evidence, not a claim that its full test suite was rerun for packaging.

## Remaining issues / owner actions

1. Packaging is complete, but live Google OAuth/Community Cloud callback and
   browser-session checks remain unverified. Previous application browser tests
   were blocked by local Chromium process closures. Packaging does not resolve
   or bypass that launch gate.
2. No live Supabase SQL/RLS, private Cloudinary delivery, Pinecone operations or
   Groq completion was verified. Complete the acceptance checks in the app guide.
3. Dependency verification used the existing Windows Python 3.12 environment;
   this task did not perform a fresh Linux installation or measure Free-host RAM.
   The copied dependency lock targets Linux/Python 3.12 per the original guide.
4. Configure fresh values only through Community Cloud Secrets, using the example.
   Its committed `FREE_PLANS_CONFIRMED=false` is intentional. It must not silently
   enable public access or attest to provider billing configuration.
5. Future Community Cloud settings: main file `authenticated_cloud/streamlit_app.py`,
   Python 3.12. The adjacent `authenticated_cloud/requirements.txt` is the runtime
   dependency file; do NOT replace it with the original Docker requirements.

No SQL, Docker operations, cloud resources, model downloads, provider requests,
billing changes, commits, pushes or deployments were performed by this packaging task.
