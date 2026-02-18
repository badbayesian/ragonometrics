Ragonometrics
=============

Docker-first RAG workflow for economics papers with:
- Streamlit chat + structured workstream UI
- Agentic + structured workflow runs
- OpenAlex/CitEc enrichment
- Unified Postgres lineage (`workflow.run_records`)

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

4. Open UI:
- `http://localhost:8585`

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

Core Docs
---------
- Architecture: `docs/architecture/architecture.md`
- Data model ERD: `docs/architecture/data-model-erd.md`
- Workflow architecture: `docs/architecture/workflow_architecture.md`
- Config/env reference: `docs/configuration/configuration.md`
- UI guide: `docs/guides/ui.md`
- Workflow guide: `docs/guides/workflow.md`
- Docker guide: `docs/deployment/docker.md`
- Migrations/backfill: `docs/deployment/migrations.md`
