Ragonometrics
=============

Docker-first RAG workflow for economics papers.

What The Workflow Achieves
--------------------------
A single `ragonometrics workflow` run does all of this with one `run_id`:
- Profiles the corpus and writes a prep manifest.
- Extracts and chunks paper text.
- Enriches metadata (OpenAlex/CitEc when available).
- Runs agentic + structured research questions (optional/controlled by flags).
- Builds/updates index metadata.
- Writes auditable artifacts to `reports/`.
- Persists run/step/report lineage to Postgres (`workflow.run_records`).

Quick Start (Docker)
--------------------
1. Create/update `.env` (minimum):

```bash
OPENAI_API_KEY=your_key_here
PAPERS_HOST_DIR=./papers
CONTAINER_DATABASE_URL=postgres://postgres:postgres@postgres:5432/ragonometrics
```

2. Apply DB migrations (required once per environment):

```bash
docker compose run --rm migrate
```

3. Start core services:

```bash
docker compose up -d --build
```

By default this starts the core runtime only: `postgres`, `streamlit`, `rq-worker`, and `pgadmin`.

Batch services are profile-gated and started explicitly:

```bash
docker compose --profile batch up -d worker indexer workflow
```

4. Open Streamlit:
- `http://localhost:8585`

5. Verify papers are mounted:

```bash
docker compose exec -T streamlit ls -la /app/papers
```

Run The Full Workflow
---------------------
Run all papers in `/app/papers`:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both \
  --question "What are the paper's main contribution, identification strategy, key results, and limitations?"
```

Run one paper:

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async mode (queue-backed):

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

Outputs
-------
Filesystem artifacts:
- `reports/workflow-report-<run_id>.json` (default path)
- `reports/prep-manifest-<run_id>.json` (default path)
- `reports/audit-workflow-report-<run_id>.md` (if audit rendering enabled)
- `reports/audit-workflow-report-<run_id>-latex.pdf` (if PDF rendering enabled)

Note: teams can post-process or move artifacts into `reports/workflow/`, `reports/prep/`, and `reports/audit/`; runtime writes to `reports/` by default.
Generated LaTeX intermediates (for example `.tex` and `.xdv`) are local build artifacts and are not tracked in git.

Primary Postgres tables:
- `workflow.run_records`
- `workflow.async_jobs`
- `retrieval.query_cache`
- `observability.token_usage`
- `enrichment.paper_openalex_metadata`
- `ingestion.documents`, `ingestion.paper_metadata`
- `indexing.pipeline_runs`, `indexing.vectors`, `indexing.index_shards`, `indexing.index_versions`

Schema ownership:
- Alembic revisions under `alembic/versions/*` are the source of truth.
- Runtime modules no longer create/alter tables in hot paths.
- Postgres is the only supported runtime persistence backend.

Migration command:

```bash
ragonometrics db migrate --db-url "$DATABASE_URL"
```

Historical import/backfill (optional):

```bash
python tools/backfill_sqlite_to_postgres.py --db-url "$DATABASE_URL"
python tools/validate_backfill_parity.py --db-url "$DATABASE_URL"
```

Usage rollups:

```bash
ragonometrics usage --db-url "$DATABASE_URL" --run-id "<run_id>"
```

OpenAlex title+author metadata process:

```bash
ragonometrics store-openalex-metadata --papers-dir papers --meta-db-url "$DATABASE_URL"
```

Database Browser (pgAdmin)
--------------------------
Start pgAdmin:

```bash
python tools/standup_pgadmin.py
```

Then open the printed URL (default `http://localhost:5052`).

Key Docs
--------
- Architecture: [docs/architecture/architecture.md](docs/architecture/architecture.md)
- Data model ERD: [docs/architecture/data-model-erd.md](docs/architecture/data-model-erd.md)
- Workflow architecture: [docs/architecture/workflow_architecture.md](docs/architecture/workflow_architecture.md)
- Docker guide: [docs/deployment/docker.md](docs/deployment/docker.md)
- Migrations/backfill: [docs/deployment/migrations.md](docs/deployment/migrations.md)
- Workflow CLI guide: [docs/guides/workflow.md](docs/guides/workflow.md)
