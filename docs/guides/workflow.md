# Workflow Guide

This guide covers `ragonometrics workflow` execution and outputs.

## Workflow Stages

A standard run executes:
1. `prep`: corpus validation and manifest inputs
2. `ingest`: PDF extraction and chunking
3. `enrich`: OpenAlex/CitEc metadata enrichment
4. `agentic`: optional decomposition + synthesis
5. `index`: optional index writes
6. `evaluate`: run-level quality/usage accounting
7. `report`: workflow report artifacts

Run lineage is persisted in `workflow.run_records`.

## Structured vs Agentic

- `Structured` focuses on canonical fixed questions for comparability and caching.
- `Agentic` handles open-ended reasoning by planning sub-questions and synthesizing evidence-backed answers.
- Recommended production runs use both via `--report-question-set both` with `--agentic`.

## Core Commands

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

## Important Flags

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
- `reports/workflow-report-<run_id>.json`
- `reports/prep-manifest-<run_id>.json`
- optional audit artifacts under `reports/`

Postgres:
- `workflow.run_records`
- `retrieval.query_cache`
- `observability.token_usage`
- indexing/ingestion tables when indexing is enabled
