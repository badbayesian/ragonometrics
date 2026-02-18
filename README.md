Ragonometrics
=============

Docker-first RAG workflow for economics papers with:
- Streamlit chat + structured workstream UI
- Flask API + React SPA web surface (`/api/v1/*`)
- Agentic + structured workflow runs
- OpenAlex/CitEc enrichment
- Unified Postgres lineage (`workflow.run_records`)

Web App Highlights
------------------
- Auth: login with email/username, create account, forgot/reset password.
- Project + persona selectors in top bar (when available in your account context).
- Paper Finder: type-to-search with author/venue/year filters and deduped paper titles.
- Chat: conversation timeline, suggested prompts, queue support (`Ask`/`Ask (Stream)`), evidence preview (top 3 chunks), provenance score badges, and robot assistant avatar.
- Paper Viewer: PDF view with highlight/notes workflow from chat evidence.
- Structured Workstream: cache refresh, generate missing, compact/full exports.
- OpenAlex Metadata: link-rich entities and manual OpenAlex link override tool.
- Citation Network: interactive graph with hop controls, auto-reload, reset, and cache metadata.
- Usage: account-scoped usage by default (with session-only toggle).
- Compare: cache-first multi-paper comparison runs with fill-missing and export.
- Debug mode toggle hides/shows `Workflow Cache` and `Cache Inspector` tabs.

Quick Start (Docker)
--------------------
1. Create `.env`:

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

Optional Streamlit login bootstrap:

```bash
STREAMLIT_USERS_JSON={"admin":"REDACTED_STREAMLIT_PASSWORD","tester":"REDACTED_STREAMLIT_PASSWORD"}
```

2. Apply migrations:

```bash
docker compose run --rm migrate
```

3. Start core services:

```bash
docker compose up -d --build
```

This starts: `postgres`, `streamlit`, `rq-worker`, `pgadmin`.
Optional web app (Flask + React SPA):

```bash
docker compose --profile web up -d --build web
```

Streamlit is still fully supported and remains active; the Flask web app is additive during migration.

4. Open UI:
- `http://localhost:8585`
- `http://localhost:8590`

5. Verify papers mount:

```bash
docker compose exec -T streamlit ls -la /app/papers
```

Run Workflow
------------
All papers:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Single paper:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async enqueue:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --report-question-set both \
  --async \
  --queue-db-url "$DATABASE_URL" \
  --meta-db-url "$DATABASE_URL"
```

Streamlit Structured Export
---------------------------
In **Structured Workstream**:
- `Compact` export: minimal Q/A view
- `Full` export: includes confidence, retrieval method, citation anchors, and workflow metadata when available

If older rows are compact-only:
- Use **Regenerate Missing Full Fields (Export Scope)** in Streamlit, or
- Run:

```bash
python tools/backfill_structured_question_fields.py --db-url "$DATABASE_URL" --apply
```

Flask Web API
-------------
Run locally:

```bash
ragonometrics web --host 0.0.0.0 --port 8590
```

API base: `http://localhost:8590/api/v1`

Core endpoints:
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `GET /api/v1/papers`
- `GET /api/v1/chat/suggestions`
- `GET /api/v1/chat/history`
- `DELETE /api/v1/chat/history`
- `POST /api/v1/chat/turn`
- `POST /api/v1/chat/turn-stream` (NDJSON)
- `GET /api/v1/openalex/metadata`
- `POST /api/v1/openalex/metadata/manual-link`
- `GET /api/v1/openalex/citation-network`
- `GET /api/v1/structured/questions`
- `POST /api/v1/structured/generate`
- `POST /api/v1/structured/generate-missing`
- `POST /api/v1/structured/export`
- `GET /api/v1/usage/summary`
- `GET /api/v1/compare/similar-papers`
- `POST /api/v1/compare/runs`
- `POST /api/v1/compare/runs/<comparison_id>/fill-missing`
- `POST /api/v1/compare/runs/<comparison_id>/export`

Operational reference: `docs/guides/web_button_matrix.md`

Useful Commands
---------------
Migrate schema:

```bash
ragonometrics db migrate --db-url "$DATABASE_URL"
```

Backfill sqlite legacy data:

```bash
python tools/backfill_sqlite_to_postgres.py --db-url "$DATABASE_URL"
python tools/validate_backfill_parity.py --db-url "$DATABASE_URL"
```

Usage rollup:

```bash
ragonometrics usage --db-url "$DATABASE_URL" --run-id "<run_id>"
```

OpenAlex metadata store pass:

```bash
ragonometrics store-openalex-metadata --papers-dir papers --meta-db-url "$DATABASE_URL"
```

Manual OpenAlex link (when auto-match fails):

```bash
python tools/manual_openalex_link.py \
  --paper "Impact of Restaurant Hygiene Grade Cards on Foodborne-Disease Hospitalizations in Los Angeles County - Simon et al. (2005).pdf" \
  --openalex-api-url "https://api.openalex.org/w28470166" \
  --db-url "$DATABASE_URL"
```

Concurrent web cache benchmark (many users reading structured cached questions):

```bash
ragonometrics benchmark-web-cache --base-url http://localhost:8590 --identifier admin --password "$WEB_PASSWORD" --users 50 --iterations 10
```

Concurrent web tab benchmark (Chat/Structured/OpenAlex/Network/Usage endpoint reads):

```bash
ragonometrics benchmark-web-tabs --base-url http://localhost:8590 --identifier admin --password "$WEB_PASSWORD" --users 30 --iterations 5
```

Concurrent web chat benchmark (chat turns + cache-hit ratio):

```bash
ragonometrics benchmark-web-chat --base-url http://localhost:8590 --identifier admin --password "$WEB_PASSWORD" --users 20 --iterations 5 --question "What is the main contribution?"
```

Frontend tests (works without local Node/npm):

```bash
python tools/run_frontend_tests.py
```

Rebuild web container after frontend changes:

```bash
docker compose --profile web up -d --build web
```

Core Docs
---------
- Architecture: `docs/architecture/architecture.md`
- Data model ERD: `docs/architecture/data-model-erd.md`
- Workflow architecture: `docs/architecture/workflow_architecture.md`
- Config/env reference: `docs/configuration/configuration.md`
- UI guide: `docs/guides/ui.md`
- Web migration checklist: `docs/guides/web_migration_checklist.md`
- Workflow guide: `docs/guides/workflow.md`
- Docker guide: `docs/deployment/docker.md`
- Migrations/backfill: `docs/deployment/migrations.md`
- Archived docs: `docs/archive/README.md`
