# Docker Deployment Guide

This project is intended to run primarily via Docker Compose.

## Required `.env` values

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

Notes:
- `PAPERS_HOST_DIR` is mounted to `/app/papers` in app containers.
- `CONTAINER_DATABASE_URL` is used inside containers and should use host `postgres` (not `localhost`).
- Optional Streamlit auth bootstrap:
  - `STREAMLIT_USERS_JSON={"admin":"pass","tester":"pass"}`
  - `STREAMLIT_AUTH_BOOTSTRAP_FROM_ENV=1`

## Start services

Apply migrations first:

```bash
docker compose run --rm migrate
```

Core stack (recommended):

```bash
docker compose up -d --build
```

Default startup brings up core runtime services only:
- `postgres`
- `streamlit`
- `rq-worker`
- `pgadmin`

Batch services are profile-gated and started on demand:

```bash
docker compose --profile batch up -d worker indexer workflow
```

Check status:

```bash
docker compose ps
```

Open Streamlit:
- `http://localhost:8585`
- UI tabs include Chat, Structured Workstream, OpenAlex Metadata, Citation Network, and Usage.

## Run workflow from Docker

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

The `rq-worker` service consumes jobs from `workflow.async_jobs`.

## Artifacts and data

Filesystem:
- `reports/workflow-report-<run_id>.json` (default path)
- `reports/prep-manifest-<run_id>.json` (default path)
- `reports/audit-workflow-report-<run_id>.md` (if audit rendering enabled)
- `reports/audit-workflow-report-<run_id>-latex.pdf` (if PDF rendering enabled)

Optional organization:
- You can move artifacts into subfolders such as `reports/workflow/`, `reports/prep/`, and `reports/audit/` for browsing.

Postgres:
- Workflow lineage: `workflow.run_records`
- Queue: `workflow.async_jobs`
- Retrieval cache: `retrieval.query_cache`
- Usage telemetry: `observability.token_usage`

Migration ownership:
- Alembic (`alembic/versions/*`) is the schema source of truth.
- Runtime modules do not run hot-path DDL.
- Postgres is the only supported runtime persistence backend.

## pgAdmin

Start:

```bash
python tools/standup_pgadmin.py
```

Default URL:
- `http://localhost:5052`
