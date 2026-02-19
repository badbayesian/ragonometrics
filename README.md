Ragonometrics
=============

Ragonometrics is a web-first research app for paper Q&A, structured extraction, and agentic analysis over a local paper corpus.

It provides:
- Flask API + React web app (`http://localhost:8590`)
- Postgres-backed workflow and cache storage
- Structured and agentic workflow execution
- OpenAlex metadata and citation enrichment

Quick Start
-----------

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

3. Start the web stack:

```bash
docker compose --profile web up -d --build postgres web rq-worker pgadmin
```

4. Open the app:
- `http://localhost:8590`

Structured + Agentic Workflow
-----------------------------

The workflow combines two modes:
- `Structured`: answers a fixed canonical question set for consistent outputs across papers.
- `Agentic`: decomposes complex prompts into sub-questions, retrieves evidence, and synthesizes final answers.

Canonical command (both enabled):

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Useful Commands
---------------

Run frontend tests:

```bash
python tools/run_frontend_tests.py
```

Rebuild web container:

```bash
docker compose --profile web up -d --build web
```

Run workflow for a single paper:

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

Core Docs
---------
- `docs/deployment/docker.md`
- `docs/guides/ui.md`
- `docs/guides/workflow.md`
- `docs/architecture/architecture.md`
