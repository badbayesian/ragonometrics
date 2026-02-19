# Docker Deployment Guide

This is the canonical Docker runbook for Ragonometrics web deployment.

## Required `.env`

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

Notes:
- `PAPERS_HOST_DIR` is mounted to `/app/papers`.
- `CONTAINER_DATABASE_URL` is for container-to-container connectivity and should use host `postgres`.

## Start Services

Apply migrations first:

```bash
docker compose run --rm migrate
```

Start web runtime:

```bash
docker compose --profile web up -d --build postgres web rq-worker pgadmin
```

Open the app:
- `http://localhost:8590`

## Workflow Runs

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
  --papers "/app/papers/Your Paper.pdf" \
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

## Frontend Test and Rebuild

Run frontend tests:

```bash
python tools/run_frontend_tests.py
```

Rebuild web container:

```bash
docker compose --profile web up -d --build web
```

## Health Checks

List services:

```bash
docker compose ps
```

Tail web logs:

```bash
docker compose logs -f web
```

Tail worker logs:

```bash
docker compose logs -f rq-worker
```
