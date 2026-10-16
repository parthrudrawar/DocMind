# Verification report — 2026-10-16

Status: **IMPLEMENTATION PREPARED; NOT LIVE-VERIFIED / NOT READY TO LAUNCH.**

## Executed

- `tests/test_authenticated_cloud.py`: **31 passed**. Real generated text PDFs;
  mocked Supabase/Auth/Cloudinary/Pinecone/Groq; PKCE challenge and one-use state,
  wrong browser proof, expired flow, revoked app sessions, refresh identity binding,
  logout failure, two accounts, concurrent dashboard reads, foreign query/download/
  deletion denial before provider calls, duplicate content scoped per owner,
  persistent registry/chat recovery with a new broker, quota consumption after
  failed generation, pending indexing and idempotent cleanup retries.
- Existing `test_community_cloud_demo.py`, `test_citations.py`,
  `test_private_demo_config.py`: **70 passed**. Combined run with the above:
  **101 passed**. This is relevant regression coverage, NOT the full Docker suite.
- `tests/test_authenticated_cloud_component.py`: **1 passed** separately. Node
  executes the actual bundled JS in isolated storage/message fixtures: sign-in
  tab, opener removal, binding restoration, wrong/missing state, foreign frame
  rejection, tab refresh and logout storage clearing. This is NOT a real browser.
- Ruff: **PASS** on the new application and all three new test modules.
- Python AST, dummy TOML and PostgreSQL outer SQL grammar parsing: **PASS**.
- Linux/Python 3.12 dependency lock generated; isolated venv dependency check: PASS.

## Browser failure — not counted as a pass

`tests/test_authenticated_cloud_browser.py`: **2 failed** in this environment.
Chromium closes its process/connection (`TargetClosedError`) while creating pages
or browser contexts. A separate blank-page smoke also failed. No sandbox flags
were weakened and no application checks were removed to obtain a green result.
An alternative Firefox smoke did not complete and was stopped.

The tests remain enabled. They exercise the bundled component using a loopback
HTTP fixture and intercepted fake Supabase redirect, never real credentials.
The fixture mirrors Streamlit's actual popup sandbox policy (top navigation is
not supported). They MUST pass in a functioning browser environment before launch.

The independent JS protocol fixture passed, but does not remove this browser gate.

## Not executed

- No live Google OAuth, Supabase Auth, SQL migration or PostgreSQL RLS execution.
- No live Cloudinary upload, unsigned-private denial, authenticated download/delete.
- No live Pinecone indexing/retrieval/deletion, Groq completion or resource measurement.
- No Community Cloud deployment, real callback/proxy/session persistence test, or
  real two-Google-account browser isolation test.
- No Docker operations, existing environment reads, credential changes, data
  deletion, billing activation, cloud resource creation, commit or push.

SQL parsing checks syntax outside function bodies; it does **not** prove PL/pgSQL
execution, actual grants, RLS or database concurrency. Mocked quota behavior does
not replace transaction tests against the real PostgreSQL functions.

## Files added (existing application files unchanged)

```
authenticated_cloud/__init__.py
authenticated_cloud/auth.py
authenticated_cloud/providers.py
authenticated_cloud/service.py
authenticated_cloud/streamlit_app.py
authenticated_cloud/component/index.html
authenticated_cloud/component/auth.js
authenticated_cloud/component/style.css
authenticated_cloud/schema.sql
authenticated_cloud/requirements.in
authenticated_cloud/requirements.txt
authenticated_cloud/requirements.tests.txt
authenticated_cloud/secrets.toml.example
authenticated_cloud/README.md
authenticated_cloud/VERIFICATION.md
tests/test_authenticated_cloud.py
tests/test_authenticated_cloud_component.py
tests/test_authenticated_cloud_browser.py
```

Public model/tool/dependency caches and isolated test output were also generated
only in existing ignored tooling directories, not in application data storage.

## Next steps, in order

1. Review `README.md` and configure new dedicated Free provider projects and a Google
   OAuth Web client in **Testing** mode, with two independent test Google accounts.
   Use only the basic OpenID/email/profile scopes. Do not activate billing.
2. Run the initial SQL and configure the independent server write-key **digest**;
   verify real grants/RLS with both authenticated users and anon/direct requests.
3. In a working local browser environment, rerun the component tests. For the
   already-created isolated development venv from this workspace:

   ```powershell
   .\.staging-tools\community-cloud-venv\Scripts\python.exe -m pytest .\tests\test_authenticated_cloud_browser.py -q -p no:cacheprovider
   ```

   On a new machine, install `authenticated_cloud/requirements.tests.txt` in an
   isolated Python 3.12 venv and install its Playwright Chromium browser. Node.js
   is needed only for the separate JS fixture, not the deployed Python app.
4. Only after owner approval to deploy, configure the exact Community Cloud URL,
   Supabase redirect allowlist and fresh Secrets; perform the complete live
   acceptance checks in `README.md`. No deployment command was executed here.

Do not infer public multi-user security, zero-cost account configuration or real
provider availability from the unit test totals alone.
