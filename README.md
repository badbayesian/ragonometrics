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

2. Start core services:

```bash
docker compose up -d --build postgres streamlit rq-worker
```

3. Open Streamlit:
- `http://localhost:8585`

4. Verify papers are mounted:

```bash
docker compose exec -T streamlit ls -la /app/papers
```

Run The Full Workflow
---------------------
Run all papers in `/app/papers`:

```bash
docker compose run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both \
  --question "What are the paper's main contribution, identification strategy, key results, and limitations?"
```

Run one paper:

```bash
docker compose run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async mode (queue-backed):

```bash
docker compose run --rm workflow \
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
- `reports/workflow/workflow-report-<run_id>.json`
- `reports/prep/prep-manifest-<run_id>.json`
- `reports/audit/audit-workflow-report-<run_id>.md`
- `reports/audit/audit-workflow-report-<run_id>-latex.pdf` (if PDF rendering enabled)

Primary Postgres tables:
- `workflow.run_records`
- `workflow.async_jobs`
- `retrieval.query_cache`
- `observability.token_usage`
- `ingestion.documents`, `ingestion.paper_metadata`
- `indexing.pipeline_runs`, `indexing.vectors`, `indexing.index_shards`, `indexing.index_versions`

Database Browser (pgAdmin)
--------------------------
Start pgAdmin:

```bash
python tools/standup_pgadmin.py
```

Then open the printed URL (default `http://localhost:5052`).

Key Docs
--------
- Architecture: `docs/architecture/architecture.md`
- Data model ERD: `docs/architecture/data-model-erd.md`
- Workflow architecture: `docs/architecture/workflow_architecture.md`
- Docker guide: `docs/deployment/docker.md`
- Workflow CLI guide: `docs/guides/workflow.md`
