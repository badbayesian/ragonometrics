# Postgres Unification Plan

This document defines the target storage model for consolidating runtime persistence into a single Postgres server, organized by pipeline stage.

## Goals
- Store workflow state, reports, retrieval cache, usage telemetry, and enrichment caches in Postgres.
- Preserve lineage across all artifacts with consistent IDs.
- Keep writes auditable and idempotent.
- Keep migration/backfill procedures explicit and auditable.

## Canonical Lineage Model
- `run_id`: workflow execution id (text UUID/hex), primary lineage key.
- `doc_id`: deterministic document identifier (file/text hash derived).
- `chunk_id`: deterministic chunk identifier (doc + offsets + content hash).
- `pipeline_run_id`: indexing run surrogate key for index build events.

## Target Schemas
- `ingestion`: extracted paper records and prep manifests.
- `enrichment`: OpenAlex/CitEc cache, title overrides, and external fetch outcomes.
- `indexing`: vector/index metadata and shard/version records.
- `workflow`: unified workflow ledger (`run_records`) and async jobs queue.
- `retrieval`: query cache and retrieval telemetry.
- `observability`: token usage and operational failures.

## Target Tables (Summary)

### `ingestion`
- `ingestion.documents`
- `ingestion.paper_metadata`
- `ingestion.prep_manifests`

### `enrichment`
- `enrichment.openalex_http_cache` (request/response cache)
- `enrichment.paper_openalex_metadata` (canonical per-paper OpenAlex metadata)
- `enrichment.openalex_title_overrides` (manual/curated title matching overrides)
- `enrichment.citec_cache`

### `indexing`
- `indexing.pipeline_runs`
- `indexing.vectors`
- `indexing.index_versions`
- `indexing.index_shards`

### `workflow`
- `workflow.run_records`
- `workflow.async_jobs`

### `retrieval`
- `retrieval.query_cache`
- `retrieval.retrieval_events` (optional now, recommended for diagnostics)

### `observability`
- `observability.token_usage`
- `observability.request_failures`

## DDL
The concrete DDL is tracked in:
- `deploy/sql/001_unified_postgres_schema.sql`

## Migration and Runtime Ownership

### Current State
- Runtime persistence is Postgres-only.
- Alembic (`alembic/versions/*`) is the schema source of truth.
- Runtime modules do not perform hot-path DDL.
- Historical SQLite data can be imported using backfill tools, but SQLite is not part of active runtime paths.

### Bootstrap
1. Run migrations to head:
   - `ragonometrics db migrate --db-url "$DATABASE_URL"`
2. Validate required extensions (`vector` / `vectorscale`) and schema presence.
3. Start runtime services.

### Historical Backfill (Optional)
- Import historical sqlite/report artifacts into Postgres:
  - workflow json reports -> `workflow.run_records` (`record_kind=report`)
  - sqlite workflow state -> `workflow.run_records` (`record_kind=run|step`)
  - sqlite query cache -> `retrieval.query_cache`
  - sqlite token usage -> `observability.token_usage`
  - sqlite enrichment caches -> `enrichment.citec_cache` (OpenAlex request caching uses `enrichment.openalex_http_cache`)
- Validate counts and sampled checksums after import.

## Constraints and Idempotency
- Ensure uniqueness where natural keys exist:
  - `workflow.run_records(run_id, record_kind, step, record_key)`
  - `retrieval.query_cache(cache_key)`
  - `ingestion.documents(doc_id)`
  - `indexing.vectors(id)` and `indexing.vectors(chunk_id)`
- Preserve upsert semantics for all caches and report rows.

## Operational Recommendations
- Partition heavy append tables (`observability.token_usage`, optionally `indexing.vectors` at scale).
- Add retention policy for low-value telemetry tables.
- Migration version tracking is implemented with Alembic (`alembic/versions/*`).

## Acceptance Criteria
1. One Postgres server stores all active runtime persistence.
2. Workflow reports/state/cache/usage are queryable without SQLite.
3. Existing CLI/UI/workflow behavior remains functionally unchanged under Postgres-only runtime.
4. Audit lineage (`run_id`, `doc_id`, `chunk_id`) is preserved end-to-end.
