# Docker Deployment Guide

This is the canonical Docker runbook for Ragonometrics web deployment.

## Required `.env`

Set these first so containers can access keys, papers, and Postgres.

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

Notes:

- `PAPERS_HOST_DIR` is mounted to `/app/papers`.
- `CONTAINER_DATABASE_URL` should use host `postgres` for container-to-container connectivity.

## Start Services

Run these in order to initialize schema and then start the runtime services.

Apply migrations first:

```bash
docker compose run --rm migrate
```
On a cold start, Postgres is empty; this step creates the app schema so `web` and `rq-worker` do not fail with missing-table errors.

Start web runtime:

```bash
docker compose --profile web up -d --build postgres web rq-worker pgadmin
```

Open the app:

- `http://localhost:8590`

## Workflow Runs

Use these commands to execute workflow jobs in the batch profile.

All papers:
Runs one workflow pass across every PDF under `/app/papers`.

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Single paper:
Runs the same workflow for one specific paper path.

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Your Paper.pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async enqueue:
Queues the workflow for background processing instead of blocking the terminal.

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

## Frontend Test and Rebuild

Use this section to validate UI behavior and roll out web-only code changes.

Run frontend tests:
Checks frontend behavior before deploys.

```bash
python tools/run_frontend_tests.py
```

Rebuild web container:
Rebuilds and restarts just the `web` service.

```bash
docker compose --profile web up -d --build web
```

## Health Checks

Use these commands to confirm services are up and diagnose runtime issues.

List services:
Shows container status and port mappings.

```bash
docker compose ps
```

Tail web logs:
Streams API/web server logs.

```bash
docker compose logs -f web
```

Tail worker logs:
Streams async worker logs.

```bash
docker compose logs -f rq-worker
```

## Related Documents

- [UI guide](../guides/ui.md)
- [Workflow guide](../guides/workflow.md)
- [System architecture](../architecture/architecture.md)
