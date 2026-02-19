# Workflow Guide

This guide covers `ragonometrics workflow` execution and outputs.

## Workflow Stages

A standard run executes:

1. `prep`: validate corpus and build run inputs
2. `ingest`: extract text and chunk paper content
3. `enrich`: fetch and store metadata enrichment
4. `agentic`: optional decomposition and synthesis
5. `index`: optional index writes
6. `evaluate`: run-level quality and usage accounting
7. `report`: persist report artifacts

Run lineage is persisted in `workflow.run_records`.

## Structured vs Agentic

- `Structured`: fixed canonical questions for comparability and cache reuse.
- `Agentic`: open-ended reasoning through planning, retrieval, and synthesis.
- Common production mode: `--report-question-set both` with `--agentic`.

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
- optional artifacts under `reports/`

Postgres:

- `workflow.run_records`
- `retrieval.query_cache`
- `observability.token_usage`
- ingestion and indexing tables when indexing is enabled

## Related Documents

- [UI guide](ui.md)
- [Workflow architecture](../architecture/workflow_architecture.md)
- [Postgres ERD](../architecture/data-model-erd.md)
