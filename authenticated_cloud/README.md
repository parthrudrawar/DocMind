# DocuMind accounts — separate ₹0 Community Cloud demo

Entrypoint: `authenticated_cloud/streamlit_app.py`, Python **3.12**.
This does not replace `community_cloud/streamlit_app.py` or any Docker application.
No FastAPI, Celery, Redis, SQLite, FAISS, deployment Compose files or existing secrets
are used. No cloud resources have been created and no live provider tests have run.

## Feasibility checked; not a live deployment certificate

- [Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud) hosts Python
  apps for free at a `streamlit.app` HTTPS address; no purchased domain is needed.
- [Supabase Free](https://supabase.com/pricing): Auth/PostgreSQL, RLS, 500 MB DB,
  50,000 MAU, 5 GB egress; two active Free projects. Free projects can pause after
  a week of inactivity. No automatic daily backups on Free.
- [Google via Supabase](https://supabase.com/docs/guides/auth/social-login/auth-google)
  uses Google accounts, not Supabase email signup; custom SMTP is unnecessary for
  this flow. No Gmail/Drive/paid Google APIs, custom domains or billing are needed.
  In Google's Testing audience only listed test users can sign in. Publishing the
  consent screen is a separate owner action; Google may require branding review.
- [Cloudinary Free](https://cloudinary.com/pricing) is an ongoing Free plan, not a trial.
  Use **raw + private** originals only. Raw files do not get image transformations.
  [Private download](https://cloudinary.com/documentation/control_access_to_media)
  uses a signed, expiring authenticated API request, fetched ONLY by Python.
  It costs twice the delivery bandwidth. Never use public upload URLs or paid CDN
  token-auth features. Free accounts must enable
  [Allow delivery of PDF and ZIP files](https://cloudinary.com/documentation/image_delivery_options).
  That format switch does NOT change our `type=private` upload policy.
- [Pinecone Starter](https://www.pinecone.io/pricing/): AWS us-east-1, up to 2 GB,
  100 namespaces/index, 2M write units/month, 1M read units/month, 1 GB egress/month.
  Use your own vectors, NOT paid inference/Assistant features or a trial.
- [Groq Free](https://console.groq.com/docs/billing-faqs): rate limits apply. No paid
  fallback, retries or billing upgrade is implemented. Model availability may change.

Stay on Free/Starter; do not add payment methods, buy a domain, enable trials or
upgrade. `FREE_PLANS_CONFIRMED=true` is an owner's attestation, NOT an API check of
account billing. A limited, low-traffic demo is feasible; unlimited usage, uptime,
guaranteed email/delivery and permanent free-provider availability are not promised.
If private raw PDF upload/download fails for your account, STOP: no public fallback.

## Security and persistence

1. The bundled component opens a same-origin blank sign-in tab on a user click,
   stores its per-tab binding in `sessionStorage`, severs `window.opener`, then
   navigates THAT tab to Supabase's Google authorize URL. Continue in that tab;
   allow the sign-in popup if your browser blocks it. This uses Streamlit's supported
   popup sandbox policy, never prohibited parent/top navigation. A server-held 256-bit PKCE verifier,
   S256 challenge, random state and a matching per-tab proof are required. Flows
   expire after five minutes and are consumed once. Supabase handles Google's own
   OAuth state/nonce checks. Do not disable nonce checks in provider settings.
2. The callback returns an authorization **code**, never access/refresh tokens in
   a URL. Python exchanges it with the server-held verifier. `/auth/v1/user` verifies
   the Supabase UUID, confirmed email and Google identity. Browser-supplied IDs or
   decoded-but-unverified JWT claims are never ownership evidence.
3. The browser receives only a random opaque app capability, not Supabase/Pinecone/
   Cloudinary/Groq credentials. The server indexes app capabilities by SHA-256.
   Access/refresh tokens stay in the broker, never in component args or global
   mutable per-user HTTP headers. Every operation verifies `/auth/v1/user` again.
4. Access tokens refresh near expiry with rotation; a refreshed identity must
   match the original account. App sessions have an eight-hour maximum. Logout
   immediately revokes that app capability and requests Supabase local signout.
   Other independent sessions remain signed in. A failed remote logout is not
   claimed successful. Google itself is not signed out. Supabase JWTs already
   issued can remain valid until expiry, but revoked app capabilities cannot be used.
5. Per-tab refresh/reconnect survives through `sessionStorage`. Browser restart,
   closed tabs and app restart require Google sign-in again. The account's SQL
   registry and chats remain, and the same Supabase UUID restores them without
   re-uploading or re-embedding. No claim of seamless cross-restart login is made.
6. SQL SELECT uses the user's JWT and RLS `owner=auth.uid()`. All mutations require
   the user's JWT AND an independent server-only write key. This prevents direct
   client RPCs from forging indexing completion or destroying cleanup records.
   There is **no service-role key** in this app. Security-definer functions have
   fixed search paths, explicit ownership predicates and restricted grants.
7. Cloudinary IDs are derived as `documind/dm-<verified UUID hex>/<PDF SHA256>.pdf`.
   Pinecone uses `dm-<UUID hex>` namespaces plus owner/doc filters; results are
   checked again before citations. UI-supplied document IDs must exist in the
   user's RLS-scoped registry. Deletes use exact vector IDs, never delete-all.
8. Answers reuse escaped Markdown/citation rendering. PDF filenames are text,
   excerpts are escaped, and documents are explicitly untrusted prompt evidence.
   Chat history is persistent per account, but prior turns are not sent as model
   context: each question must be self-contained in this minimal version.

The component uses supported Streamlit component messages, not parent DOM hacks.
Its frame must be same-origin; foreign parents cannot receive stored capabilities.
No browser localStorage, third-party JS/CDN, recovery keys, manual workspace IDs or
IP-address ownership is used. This is a bearer-session design: XSS/token theft is
still a threat; do not weaken Markdown escaping or expose component/session values.
There is deliberately no original-PDF download/preview widget: Streamlit's media
URLs do not run our account authorization on every download. The private retrieval
helper remains server-side for authorized verification, not a public route.

## Resource policy and partial failures

- 50 permanent accounts, 24 active/pending PDFs deployment-wide.
- 3 PDFs/account; 2 MB, 10 pages/file; 20 pages and 200 chunks/account.
- 6 new uploads/account/day and 20 deployment-wide/day, plus **100 lifetime uploads**.
- 5 AI/original-download requests/account/day, 40 deployment-wide/day, one/minute
  across the demo; **200 lifetime requests**. Failed requests consume reservations.
- Up to 50 stored turns/account. Logout/deleting files does not reset budgets.
  UTC-day budgets reset naturally; lifetime limits deliberately require owner
  review, not an automatic reset/upgrade. All limits are PostgreSQL-atomic.
- One heavy operation per Python process; concurrent operations get a friendly
  busy response rather than competing for model memory. MiniLM is cached once.
  Dense Pinecone retrieval replaces local FAISS/BM25 ONLY in this separate app;
  chunking, embedding model/dimensions, bounded Groq call and citations are reused.
- Synchronous only: no OCR, streaming, advanced analysis, image parsing or background
  jobs. Model startup, PyMuPDF parsing and ONNX memory may exceed free-host capacity.
  Never upload sensitive documents to a portfolio demo.

The SQL pending record is written BEFORE remote writes, with deterministic IDs.
Ready status is published only after both private PDF and vectors succeed.
Failure leaves a visible pending record. Wait ten minutes for the writer lease,
then use **Delete / retry cleanup**. Deletion first hides the document in SQL,
then removes scoped vectors/PDF; only confirmed success removes the ledger and
chats citing that PDF. Failed deletion remains `deleting` and can be retried after
logout/restart. Pinecone is eventually consistent; a newly indexed PDF may take
a short time to retrieve. Non-ready/foreign documents are never eligible.

Persistent documents have **no automatic expiry** in this account version.
Cleanup is user-initiated and retryable on next login; abandoned accounts or
offlined apps can leave bounded pending data. There is no claim of scheduled
cleanup, guaranteed erasure time, multi-region safety or disaster recovery.
Provider-side backups, retention and deletion delays still apply. The lifetime
caps bound orphan storage but are NOT a substitute for monitoring/backup.

## Manual setup — do NOT reuse staging/private-demo secrets

1. Create a **new Supabase Free project** in a Free organization. No billing upgrade.
   Use a new empty project, NOT any existing DocuMind database. In SQL Editor run
   `authenticated_cloud/schema.sql` once. It creates only `cloud_*` objects and
   commits atomically; it intentionally is not a destructive reset script.
2. Generate a fresh 32-byte lowercase hex `CLOUD_WRITE_KEY` with a password manager.
   Keep the original ONLY in Streamlit Secrets. Compute its SHA-256 locally without
   logging the key (hash the UTF-8 hex string, not decoded bytes). In SQL Editor run:

   ```sql
   insert into public.cloud_server_settings(singleton,write_key_hash)
   values(true,'PASTE_ONLY_THE_64_CHARACTER_SHA256_DIGEST');
   ```

   Only the digest goes to SQL; do not publish either value. Until configured,
   mutations fail closed. Obtain the new `sb_publishable_...` project API key.
   Never use `service_role`, JWT signing secrets or legacy secret API keys here.
3. Reserve the desired `https://YOUR-APP.streamlit.app/` app name/URL in your future
   deployment settings. Supabase Auth URL Configuration: Site URL = that exact
   HTTPS origin/root. Redirect allowlist = `https://YOUR-APP.streamlit.app/**`
   (only this app host; no host wildcards). Dynamic `oauth_state` and Supabase's
   code return to the root page. Do not use an implicit-token flow.
4. Google Cloud Console -> create/select a project, **do not activate billing**.
   Google Auth Platform -> Branding/Audience: External, Testing, add two separate
   Google test accounts. Data Access: only `openid`, email, profile.
   Clients -> OAuth client ID -> Web application. Authorized origin = your app's
   HTTPS origin. Authorized redirect URI =
   `https://YOUR-PROJECT.supabase.co/auth/v1/callback`, exactly as displayed by
   Supabase's Google provider. NOT Streamlit `/oauth2callback`.
5. Put Google's Client ID + Client Secret into **Supabase Auth -> Google provider**;
   enable Google, keep nonce checking ON. Disable email/password and anonymous
   sign-in providers for this app. Do not disable Google email verification checks.
   No Google client secret needs to be put into Streamlit or GitHub.
6. Create/select dedicated Cloudinary Free environment and Pinecone Starter index,
   separate from legacy/staging. Cloudinary: enable PDF delivery, no unsigned
   upload preset, no conversion/AI add-ons. Pinecone index: dense, **384 dimensions,
   cosine**, AWS **us-east-1**, bring your own vectors. Copy its exact data-plane
   host. The app validates index dimensions/metric/host/region; it never creates,
   resizes or deletes indexes. Use dedicated provider keys.
7. Use a Groq **Free** account/key. The reused generation model is
   `openai/gpt-oss-120b`; verify it is available in your own Free account. No fallback.
8. In a separate clean deployment repository, copy only:
   - `authenticated_cloud/` excluding any real secrets or test outputs;
   - `community_cloud/__init__.py` and `community_cloud/service.py`;
   - `app/__init__.py`, `app/ingestion/{__init__.py,chunker.py}`,
     `app/reasoning/{__init__.py,citations.py,prompt_safety.py}`,
     `app/retrieval/{__init__.py,rank_fusion.py}`;
   - `frontend/__init__.py`, `frontend/citations.py`.
   Add `**/.streamlit/secrets.toml`, `.env*`, `.staging/`, data/model directories
   and test artifacts to that repository's `.gitignore` BEFORE any Git operation.
   Do not bulk-upload this dirty working tree, PDFs or local environment files.
   Copy `community_cloud/cloud_config.toml` to that deployment repo's
   `.streamlit/config.toml` (upload limits, XSRF/CORS on, errors hidden, dark theme).
9. After reviewing files and separately authorizing publication, use GitHub's
   repository workflow yourself; no commit/push has been performed by this task.
   Community Cloud -> Create app -> select that repo/branch -> main file
   `authenticated_cloud/streamlit_app.py` -> Python **3.12**. It picks the adjacent
   fully pinned `requirements.txt`. Copy `secrets.toml.example` into the Secrets UI,
   replace placeholders with fresh values, then set `FREE_PLANS_CONFIRMED=true`.
   Do not store real secrets in GitHub or paste them into chat. Do not deploy yet
   if prerequisite verification below has not been planned.

## Required live acceptance checks (NOT executed here)

1. Inspect Supabase tables with anon/publishable-only requests: no rows. With each
   of two genuine user JWTs, SELECT sees only that owner. Mutating RPCs without
   the server write key fail; wrong owner IDs fail even with a valid session.
   Run Supabase Security Advisor; verify RLS/grants in the actual database.
2. On the actual Community Cloud URL, two independent browser profiles authenticate
   as different Google accounts. Test cancellation, altered/missing state, replay,
   callbacks opened in the other browser, five-minute expiry and unavailable
   sessionStorage. Login must fail closed. Browser URLs must not contain JWTs.
3. A uploads A.pdf, B initially sees an empty library, then uploads B.pdf. Query
   each PDF; inspect actual filenames/pages/excerpts. Attempt foreign document
   selection, download and deletion using modified component/API inputs. No
   cross-user rows, evidence, PDFs, vectors or chat history may be returned.
4. Verify an unsigned Cloudinary private-raw URL is denied while Python's signed
   download succeeds. Test image/transform URL variants are unavailable. Confirm
   no provider keys, signed URLs or JWTs appear in the browser UI/network values.
5. Logout/relogin restores only that account's PDFs/chats; restart the app and
   repeat without uploading again. Refresh a tab and let an access token expire:
   verify refresh/rotation; revoke it and ensure the app stops access. An eight-hour
   app session must require fresh sign-in. Confirm logout removes tab storage.
6. Test simultaneous uploads/questions, SQL-atomic quotas, failed Groq consumption,
   Redis-free/DB-outage fail-closed behavior, denied foreign direct requests, and
   provider failure -> pending record -> safe deletion retry after lease expiry.
7. Measure memory/latency with real MiniLM on Free hosting. Export SQL/track private
   resource inventories through owner tools for backup; no automated backup or
   restore procedure is claimed by this minimal account demo.

Mocked unit/browser tests are NOT substitutes for these checks. In particular,
the SQL grammar parser does not execute PL/pgSQL, migrations, roles or RLS, and
local component fixtures do not prove Community Cloud proxy/callback behavior.
