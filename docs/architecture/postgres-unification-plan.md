# Postgres Unification Plan

This document defines the target storage model for consolidating runtime persistence into a single Postgres server, organized by pipeline stage.

## Goals
- Store workflow state, reports, retrieval cache, usage telemetry, and enrichment caches in Postgres.
- Preserve lineage across all artifacts with consistent IDs.
- Keep writes auditable and idempotent.
- Support phased migration with dual-write and safe cutover.

## Canonical Lineage Model
- `run_id`: workflow execution id (text UUID/hex), primary lineage key.
- `doc_id`: deterministic document identifier (file/text hash derived).
- `chunk_id`: deterministic chunk identifier (doc + offsets + content hash).
- `pipeline_run_id`: indexing run surrogate key for index build events.

## Target Schemas
- `ingestion`: extracted paper records and prep manifests.
- `enrichment`: OpenAlex/CitEc cache and external fetch outcomes.
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
- `enrichment.openalex_cache`
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

## Migration Strategy (Phased)

### Phase 0: Schema Bootstrap
- Apply `deploy/sql/001_unified_postgres_schema.sql`.
- Validate extensions (`vector` / `vectorscale`) and schema ownership.

### Phase 1: Dual-Write (No Read Changes)
- Continue current reads.
- Add Postgres writes for:
  - workflow state + reports (`workflow.run_records`)
  - query cache (`retrieval.query_cache`)
  - token usage (`observability.token_usage`)
  - enrichment caches (`enrichment.openalex_cache`, `enrichment.citec_cache`)
- Keep SQLite writes as fallback while parity is validated.

### Phase 2: Backfill
- Backfill historical sqlite/report artifacts into Postgres:
  - workflow json reports -> `workflow.run_records` (`record_kind=report`)
  - sqlite workflow state -> `workflow.run_records` (`record_kind=run|step`)
  - sqlite query cache -> `retrieval.query_cache`
  - sqlite token usage -> `observability.token_usage`
  - sqlite enrichment caches -> `enrichment.*_cache`
- Validate counts and sample-row parity.

### Phase 3: Read Cutover
- Flip reads to Postgres for state/cache/usage/enrichment.
- Keep dual-write for one release window.

### Phase 4: SQLite Decommission
- Remove SQLite writers/readers from active runtime paths.
- Keep one-time import tooling for offline historical data only.

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
- Add migration version tracking (Alembic or equivalent) before broad refactors.

## Acceptance Criteria
1. One Postgres server stores all active runtime persistence.
2. Workflow reports/state/cache/usage are queryable without SQLite.
3. Existing CLI/UI/workflow behavior remains functionally unchanged after cutover.
4. Audit lineage (`run_id`, `doc_id`, `chunk_id`) is preserved end-to-end.
