# Workflow Guide

This page focuses on the end-to-end `ragonometrics workflow` command.

## What a workflow run does

A run executes:
1. `prep` (corpus/profile manifest)
2. `ingest` (PDF extraction + chunking)
3. `enrich` (OpenAlex/CitEc, when available)
4. `agentic` (optional, with structured questions)
5. `index` (optional metadata/index writes)
6. `evaluate`
7. `report` (JSON + optional audit artifacts)

All run lineage is persisted to `workflow.run_records` keyed by `run_id`.

## Docker-first usage

All papers in mounted `/app/papers`:

```bash
docker compose run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both \
  --question "What are the paper's main contribution, identification strategy, key results, and limitations?"
```

Single paper:

```bash
docker compose run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async enqueue:

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

`rq-worker` consumes jobs from `workflow.async_jobs`.

## Important flags

- `--papers <dir-or-file>`
- `--agentic`
- `--agentic-citations`
- `--report-question-set structured|agentic|both|none`
- `--question "<prompt>"`
- `--workstream-id <id>`
- `--arm <label>`
- `--parent-run-id <run_id>`
- `--trigger-source <label>`
- `--async`

## Outputs

Filesystem:
- `reports/workflow-report-<run_id>.json` (default path)
- `reports/prep-manifest-<run_id>.json` (default path)
- `reports/audit-workflow-report-<run_id>.md` (if enabled)
- `reports/audit-workflow-report-<run_id>-latex.pdf` (if enabled)

Optional organization:
- You may move generated files into `reports/workflow/`, `reports/prep/`, and `reports/audit/` after runs.

Postgres:
- `workflow.run_records` (run/step/report/question/artifact lineage)
- `observability.token_usage`
- `retrieval.query_cache`
- indexing and ingestion tables when indexing is enabled

## Related docs

- `docs/deployment/docker.md`
- `docs/architecture/workflow_architecture.md`
- `docs/architecture/data-model-erd.md`
