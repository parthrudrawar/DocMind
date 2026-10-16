# DocuMind Cloud deployment package

Separate Google-authenticated Streamlit Community Cloud application. This folder
contains only explicitly allowlisted application files and their source dependencies;
it is not a copy of the Docker repository or any application data.

Community Cloud settings:

- Main file: **`authenticated_cloud/streamlit_app.py`**
- Python: **3.12**
- Dependency lock: **`authenticated_cloud/requirements.txt`** (adjacent to entrypoint)
- Streamlit configuration: **`.streamlit/config.toml`**
- Initial Supabase SQL: **`authenticated_cloud/schema.sql`**, once in a new project
- Secrets reference: **`authenticated_cloud/secrets.toml.example`**, placeholders ONLY

Follow [the application setup guide](authenticated_cloud/README.md) for Supabase,
Google OAuth, private Cloudinary storage, Pinecone Starter and Groq Free. Its source
copying step has already been performed for this folder. Do not upload the original
Docker project's repository, environment files or data.

Enter real values ONLY in Community Cloud's Secrets UI. Do not create or commit
real `.env`/`secrets.toml` files here. Keep `FREE_PLANS_CONFIRMED=false` in the
committed example. Never add service-role credentials or activate billing/trials.

No Git repository has been initialized here, and no commit, push or deployment has
been performed. Review the folder and [packaging report](PACKAGING_REPORT.md) before
authorizing any publication. Do not blindly stage a parent repository.

Packaging checks are not proof of live Google callbacks, PostgreSQL RLS or provider
isolation. The unresolved acceptance checks in the application guide still apply.
