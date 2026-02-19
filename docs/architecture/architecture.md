# Ragonometrics Architecture

This document is the single architecture source of truth for the web-first Ragonometrics system.

## Runtime Components

- Web app: Flask API + React SPA (`ragonometrics/web`, `webapp/`).
- Workflow engine: `ragonometrics workflow` for prep, ingest, enrich, agentic, index, evaluate, report.
- Queue worker: `rq-worker` consumes async workflow/chat refresh jobs from Postgres-backed queues.
- Data store: Postgres for auth/session, workflow lineage, retrieval cache, usage telemetry, and enrichment caches.
- Paper corpus: local mounted PDF directory (`/app/papers`).

## Data Flow

1. User selects a paper and asks a question in the web UI.
2. API resolves paper scope and loads retrieval context.
3. Query cache is checked before generation.
4. LLM generates answer with citations/provenance metadata.
5. Response, usage, and cache artifacts are persisted.
6. Structured and agentic workflow runs write stage outputs and reports to Postgres and `reports/`.

## Workflow Summary

Main workflow stages:
1. `prep`
2. `ingest`
3. `enrich`
4. `agentic` (optional)
5. `index` (optional)
6. `evaluate`
7. `report`

Each stage writes run-linked records keyed by `run_id` in `workflow.run_records`.

## Data Model Summary (Inline)

Primary tables by responsibility:
- Auth/session:
  - `auth` user table
  - `auth` session table
  - `auth.request_rate_limits`
- Workflow lineage:
  - `workflow.run_records`
  - `workflow.async_jobs`
- Retrieval and usage:
  - `retrieval.query_cache`
  - `observability.token_usage`
  - `observability.request_failures`
- Enrichment:
  - `enrichment.openalex_http_cache`
  - `enrichment.paper_openalex_metadata`
  - `enrichment.openalex_citation_graph_cache`
  - `enrichment.citec_cache`
- Indexing/ingestion:
  - `ingestion.documents`, `ingestion.paper_metadata`
  - `indexing.vectors`, `indexing.index_shards`, `indexing.index_versions`

## Cache Layers and Boundaries

- Query/answer cache (`retrieval.query_cache`): first stop for repeated chat/structured prompts.
- OpenAlex HTTP cache (`enrichment.openalex_http_cache`): upstream API response cache.
- Citation graph derived cache (`enrichment.openalex_citation_graph_cache`): n-hop graph snapshots with freshness metadata.
- Structured workflow cache: canonical structured answers read from `workflow.run_records` question payloads.

## Operational Boundaries and Tradeoffs

- Postgres is the system of record for runtime state and lineage.
- Local filesystem stores reports and optional index artifacts; database stores authoritative metadata.
- Structured mode is more deterministic and cache-friendly; agentic mode is richer but higher latency/cost.
- Multi-user web usage requires strict paper scoping and session-aware access checks in API routes.
