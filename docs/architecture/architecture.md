# System Architecture

This document is the architecture source of truth for the Ragonometrics web app.

## Runtime Components

| Component | Purpose | Location |
| --- | --- | --- |
| Web API | Auth, project scoping, chat, workflow and export routes | `ragonometrics/web/` |
| Web UI | Browser client for chat, viewer, structured workflow, compare, usage | `webapp/` |
| Workflow runtime | Batch and async workflow execution (prep, ingest, enrich, agentic, evaluate, report) | `ragonometrics/` |
| Queue worker | Processes async jobs and refresh tasks | `rq-worker` container |
| Postgres | System of record for users, projects, cache, workflow lineage, enrichment, usage | `postgres` container |
| Papers directory | Source PDF corpus mounted into containers | `/app/papers` |

## Request Lifecycle (Chat)

1. User sends a question from the web UI.
2. API authenticates session and enforces project/paper scope.
3. Retrieval and cache services resolve evidence and candidate answers.
4. LLM response is generated (or served from cache).
5. Response, provenance metadata, and usage telemetry are persisted.
6. UI receives final answer and citations via JSON or NDJSON stream.

## Workflow Lifecycle

1. A workflow run is started from CLI, API, or queue.
2. Stage records are written to `workflow.run_records`.
3. Structured answers and agentic outputs are persisted as run artifacts.
4. Reports are written to `reports/` and linked back to run records.

## Data Boundaries

- Postgres is authoritative for runtime state.
- Filesystem stores source PDFs and generated report files.
- Cache layers are scoped by paper, project, and model context.

## Related Documents

- [Workflow architecture](workflow_architecture.md)
- [Postgres ERD](data-model-erd.md)
- [UI guide](../guides/ui.md)
- [Workflow guide](../guides/workflow.md)
- [Deployment guide](../deployment/docker.md)
