# Ragonometrics

Ragonometrics is a web app for paper Q&A, structured extraction, and agentic analysis over a local PDF corpus.

## What You Get

- Flask API + React web app at `http://localhost:8590`
- Postgres-backed auth, project scope, workflow lineage, and cache state
- Structured and agentic workflows
- OpenAlex metadata and citation enrichment

## Quick Start

1. Create `.env`:

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

2. Apply migrations:

```bash
docker compose run --rm migrate
```

3. Start services:

```bash
docker compose --profile web up -d --build postgres web rq-worker pgadmin
```

4. Open the app:

- `http://localhost:8590`

## Workflow Run (Structured + Agentic)

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

## Common Commands

Frontend tests:

```bash
python tools/run_frontend_tests.py
```

Rebuild the web container:

```bash
docker compose --profile web up -d --build web
```

Single-paper workflow run:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Your Paper.pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Optional manual OpenAlex link:

```bash
python tools/manual_openalex_link.py \
  --paper "Your Paper.pdf" \
  --openalex-api-url "https://api.openalex.org/W123" \
  --db-url "$DATABASE_URL"
```

## Documentation

- [Docker deployment](docs/deployment/docker.md)
- [UI guide](docs/guides/ui.md)
- [Workflow guide](docs/guides/workflow.md)
- [System architecture](docs/architecture/architecture.md)
- [Workflow architecture](docs/architecture/workflow_architecture.md)
- [Postgres ERD](docs/architecture/data-model-erd.md)
