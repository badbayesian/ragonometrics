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

Use these entrypoints to run the same workflow in full-corpus, single-paper, or queued mode.

All papers:
Runs one structured+agentic workflow pass for every paper in the mounted corpus.

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers /app/papers \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Single paper:
Runs the workflow for one specific paper path to speed up targeted reruns.

```bash
docker compose --profile batch run --rm workflow \
  ragonometrics workflow \
  --papers "/app/papers/Your Paper.pdf" \
  --agentic \
  --agentic-citations \
  --report-question-set both
```

Async enqueue:
Creates a queued job so execution continues in a background worker.

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

| Flag | What it controls | Typical use |
| --- | --- | --- |
| `--papers <dir-or-file>` | Input scope for the run (one PDF or a directory of PDFs). | `--papers /app/papers` for full corpus, or a single file path for targeted reruns. |
| `--agentic` | Enables agentic planning/sub-question synthesis stage. | Use when you want open-ended reasoning beyond fixed structured questions. |
| `--question "<prompt>"` | Overrides the default main agentic question. | Use for focused investigations, for example one policy or method question. |
| `--agentic-model <model>` | Model override used by the agentic step. | A/B model quality/cost on the same papers without changing global config. |
| `--agentic-citations` | Enriches agentic context with citation API evidence. | Use when citation grounding quality matters more than runtime cost. |
| `--report-question-set structured\|agentic\|both\|none` | Controls which report question bundles are emitted. | `both` for production comparability, `structured` for low-cost runs, `none` for minimal metadata-only runs. |
| `--meta-db-url <postgres-url>` | Postgres URL for run metadata and workflow record writes. | Use to point a run at a specific DB (dev/staging/prod). |
| `--queue-db-url <postgres-url>` | Postgres URL for async queue storage (falls back to `--meta-db-url` or `DATABASE_URL`). | Set explicitly when queue DB differs from metadata DB. |
| `--async` | Enqueues a job instead of running immediately in the current process. | Use for non-blocking execution from CI, API-triggered jobs, or batch orchestration. |
| `--workstream-id <id>` | Logical grouping key across related runs. | Group a campaign of runs (for example: `wk-2026-02-citation-refresh`). |
| `--arm <label>` | Variant label for experimental branch/config/model. | Track comparisons like `baseline`, `gpt-5-nano`, `rerank-v2`. |
| `--parent-run-id <run_id>` | Links a run to a baseline/parent run for lineage. | Use for reruns, remediation runs, or branch comparisons. |
| `--trigger-source <label>` | Source tag for run initiation. | Standardize labels like `cli`, `api`, `queue`, `cron`, `ci`. |

## Outputs

A completed run writes artifacts to both filesystem and Postgres.

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
