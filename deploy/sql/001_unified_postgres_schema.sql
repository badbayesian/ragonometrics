-- Unified Postgres storage model organized by pipeline stage.
-- Apply with a privileged role on the target Postgres server.

BEGIN;

-- Extensions used by indexing vectors.
CREATE EXTENSION IF NOT EXISTS vector;
-- Optional on some deployments; safe to ignore if unavailable.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
END
$$;

-- Stage-oriented schemas.
CREATE SCHEMA IF NOT EXISTS ingestion;
CREATE SCHEMA IF NOT EXISTS enrichment;
CREATE SCHEMA IF NOT EXISTS indexing;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS retrieval;
CREATE SCHEMA IF NOT EXISTS observability;

-- =========================
-- ingestion
-- =========================
CREATE TABLE IF NOT EXISTS ingestion.documents (
    doc_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    title TEXT,
    author TEXT,
    extracted_at TIMESTAMPTZ,
    file_hash TEXT,
    text_hash TEXT
);

CREATE TABLE IF NOT EXISTS ingestion.paper_metadata (
    doc_id TEXT PRIMARY KEY REFERENCES ingestion.documents(doc_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    title TEXT,
    author TEXT,
    authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    primary_doi TEXT,
    dois_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    openalex_id TEXT,
    openalex_doi TEXT,
    publication_year INTEGER,
    venue TEXT,
    repec_handle TEXT,
    source_url TEXT,
    openalex_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    citec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ingestion_paper_metadata_primary_doi_idx
    ON ingestion.paper_metadata(primary_doi);
CREATE INDEX IF NOT EXISTS ingestion_paper_metadata_path_idx
    ON ingestion.paper_metadata(path);

CREATE TABLE IF NOT EXISTS ingestion.prep_manifests (
    run_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    corpus_hash TEXT,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestion_prep_manifests_created_at_idx
    ON ingestion.prep_manifests(created_at DESC);

-- =========================
-- enrichment
-- =========================
CREATE TABLE IF NOT EXISTS enrichment.openalex_cache (
    cache_key TEXT PRIMARY KEY,
    work_id TEXT,
    query TEXT,
    response JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS enrichment_openalex_cache_fetched_at_idx
    ON enrichment.openalex_cache(fetched_at DESC);

CREATE TABLE IF NOT EXISTS enrichment.openalex_http_cache (
    request_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    params_json JSONB NOT NULL,
    status_code INTEGER NOT NULL,
    response JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS enrichment_openalex_http_cache_fetched_at_idx
    ON enrichment.openalex_http_cache(fetched_at DESC);
CREATE INDEX IF NOT EXISTS enrichment_openalex_http_cache_status_idx
    ON enrichment.openalex_http_cache(status_code);

CREATE TABLE IF NOT EXISTS enrichment.openalex_title_overrides (
    id BIGSERIAL PRIMARY KEY,
    title_pattern TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'contains',
    openalex_work_id TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (match_type IN ('contains', 'exact'))
);
CREATE UNIQUE INDEX IF NOT EXISTS enrichment_openalex_title_overrides_unique_idx
    ON enrichment.openalex_title_overrides(title_pattern, match_type, openalex_work_id);
CREATE INDEX IF NOT EXISTS enrichment_openalex_title_overrides_enabled_idx
    ON enrichment.openalex_title_overrides(enabled, priority DESC, updated_at DESC);

INSERT INTO enrichment.openalex_title_overrides (
    title_pattern, match_type, openalex_work_id, priority, note, enabled
)
SELECT
    'use of cumulative sums of squares',
    'contains',
    'https://api.openalex.org/w2075304461',
    100,
    'Canonical override for Inclan and Tiao cumulative sums paper.',
    TRUE
WHERE NOT EXISTS (
    SELECT 1
    FROM enrichment.openalex_title_overrides
    WHERE title_pattern = 'use of cumulative sums of squares'
      AND match_type = 'contains'
      AND openalex_work_id = 'https://api.openalex.org/w2075304461'
);

CREATE TABLE IF NOT EXISTS enrichment.citec_cache (
    cache_key TEXT PRIMARY KEY,
    repec_handle TEXT,
    response JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS enrichment_citec_cache_fetched_at_idx
    ON enrichment.citec_cache(fetched_at DESC);

CREATE TABLE IF NOT EXISTS enrichment.paper_openalex_metadata (
    paper_path TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    query_title TEXT NOT NULL,
    query_authors TEXT NOT NULL,
    query_year INTEGER,
    openalex_id TEXT,
    openalex_doi TEXT,
    openalex_title TEXT,
    openalex_publication_year INTEGER,
    openalex_authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    openalex_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    match_status TEXT NOT NULL,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (match_status IN ('matched', 'not_found', 'error'))
);
CREATE INDEX IF NOT EXISTS enrichment_paper_openalex_metadata_updated_idx
    ON enrichment.paper_openalex_metadata(updated_at DESC);
CREATE INDEX IF NOT EXISTS enrichment_paper_openalex_metadata_openalex_id_idx
    ON enrichment.paper_openalex_metadata(openalex_id);

-- =========================
-- indexing
-- =========================
CREATE TABLE IF NOT EXISTS indexing.pipeline_runs (
    id BIGSERIAL PRIMARY KEY,
    workflow_run_id TEXT,
    workstream_id TEXT,
    arm TEXT,
    paper_set_hash TEXT,
    index_build_reason TEXT,
    git_sha TEXT,
    extractor_version TEXT,
    embedding_model TEXT,
    chunk_words INTEGER,
    chunk_overlap INTEGER,
    normalized BOOLEAN,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS indexing_pipeline_runs_workflow_run_idx
    ON indexing.pipeline_runs(workflow_run_id);
CREATE INDEX IF NOT EXISTS indexing_pipeline_runs_workstream_idx
    ON indexing.pipeline_runs(workstream_id);

CREATE TABLE IF NOT EXISTS indexing.index_versions (
    index_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    embedding_model TEXT,
    chunk_words INTEGER,
    chunk_overlap INTEGER,
    corpus_fingerprint TEXT,
    index_path TEXT,
    shard_path TEXT
);

CREATE TABLE IF NOT EXISTS indexing.index_shards (
    id BIGSERIAL PRIMARY KEY,
    shard_name TEXT UNIQUE,
    path TEXT,
    pipeline_run_id BIGINT REFERENCES indexing.pipeline_runs(id),
    index_id TEXT REFERENCES indexing.index_versions(index_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS indexing_index_shards_active_idx
    ON indexing.index_shards(is_active, created_at DESC);

CREATE TABLE IF NOT EXISTS indexing.vectors (
    id BIGINT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES ingestion.documents(doc_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL UNIQUE,
    chunk_hash TEXT,
    paper_path TEXT,
    page INTEGER,
    start_word INTEGER,
    end_word INTEGER,
    text TEXT,
    embedding VECTOR,
    pipeline_run_id BIGINT REFERENCES indexing.pipeline_runs(id),
    created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS indexing_vectors_doc_id_idx
    ON indexing.vectors(doc_id);
CREATE INDEX IF NOT EXISTS indexing_vectors_pipeline_run_id_idx
    ON indexing.vectors(pipeline_run_id);

-- Best-effort ANN index creation.
DO $$
BEGIN
    BEGIN
        CREATE INDEX IF NOT EXISTS indexing_vectors_embedding_diskann_idx
            ON indexing.vectors USING diskann (embedding vector_cosine_ops);
    EXCEPTION
        WHEN OTHERS THEN
            BEGIN
                CREATE INDEX IF NOT EXISTS indexing_vectors_embedding_ivfflat_idx
                    ON indexing.vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
            EXCEPTION
                WHEN OTHERS THEN
                    NULL;
            END;
    END;
END
$$;

-- =========================
-- workflow
-- =========================
CREATE TABLE IF NOT EXISTS workflow.run_records (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    step TEXT NOT NULL DEFAULT '',
    record_key TEXT NOT NULL DEFAULT '',
    status TEXT,
    papers_dir TEXT,
    config_hash TEXT,
    workstream_id TEXT,
    arm TEXT,
    parent_run_id TEXT,
    trigger_source TEXT,
    git_sha TEXT,
    git_branch TEXT,
    config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    paper_set_hash TEXT,
    question TEXT,
    report_question_set TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    report_path TEXT,
    agentic_status TEXT,
    report_hash TEXT,
    report_question_count INTEGER,
    confidence_mean DOUBLE PRECISION,
    confidence_label_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_answer_hash TEXT,
    question_id TEXT,
    artifact_type TEXT,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    idempotency_key TEXT,
    input_hash TEXT,
    reuse_source_run_id TEXT,
    reuse_source_record_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (record_kind IN ('run', 'step', 'report', 'question', 'artifact', 'workstream_link')),
    UNIQUE (run_id, record_kind, step, record_key)
);

ALTER TABLE workflow.run_records ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE workflow.run_records ADD COLUMN IF NOT EXISTS input_hash TEXT;
ALTER TABLE workflow.run_records ADD COLUMN IF NOT EXISTS reuse_source_run_id TEXT;
ALTER TABLE workflow.run_records ADD COLUMN IF NOT EXISTS reuse_source_record_key TEXT;
CREATE INDEX IF NOT EXISTS workflow_run_records_run_kind_idx
    ON workflow.run_records(run_id, record_kind);
CREATE INDEX IF NOT EXISTS workflow_run_records_created_at_idx
    ON workflow.run_records(created_at DESC);
CREATE INDEX IF NOT EXISTS workflow_run_records_status_idx
    ON workflow.run_records(status);
CREATE INDEX IF NOT EXISTS workflow_run_records_step_idx
    ON workflow.run_records(step);
CREATE INDEX IF NOT EXISTS workflow_run_records_workstream_idx
    ON workflow.run_records(workstream_id, arm);
CREATE INDEX IF NOT EXISTS workflow_run_records_question_idx
    ON workflow.run_records(question_id);
CREATE INDEX IF NOT EXISTS workflow_run_records_idempotency_idx
    ON workflow.run_records(record_kind, step, idempotency_key);
CREATE INDEX IF NOT EXISTS workflow_run_records_input_hash_idx
    ON workflow.run_records(record_kind, step, input_hash);
CREATE INDEX IF NOT EXISTS workflow_run_records_reuse_source_idx
    ON workflow.run_records(reuse_source_run_id, reuse_source_record_key);
CREATE INDEX IF NOT EXISTS workflow_run_records_payload_gin_idx
    ON workflow.run_records USING GIN(payload_json);

CREATE TABLE IF NOT EXISTS workflow.async_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    queue_name TEXT NOT NULL DEFAULT 'default',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_text TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    retry_delay_seconds INTEGER NOT NULL DEFAULT 10,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    worker_id TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('queued', 'retry', 'running', 'completed', 'failed')),
    CHECK (job_type IN ('workflow', 'index'))
);
CREATE INDEX IF NOT EXISTS workflow_async_jobs_status_available_idx
    ON workflow.async_jobs(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS workflow_async_jobs_queue_status_idx
    ON workflow.async_jobs(queue_name, status, available_at);
CREATE INDEX IF NOT EXISTS workflow_async_jobs_worker_idx
    ON workflow.async_jobs(worker_id, status);

-- =========================
-- retrieval
-- =========================
CREATE TABLE IF NOT EXISTS retrieval.query_cache (
    cache_key TEXT PRIMARY KEY,
    query TEXT,
    paper_path TEXT,
    model TEXT,
    context_hash TEXT,
    answer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS retrieval_query_cache_created_at_idx
    ON retrieval.query_cache(created_at DESC);

CREATE TABLE IF NOT EXISTS retrieval.retrieval_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id TEXT,
    request_id TEXT,
    query TEXT,
    method TEXT,
    top_k INTEGER,
    stats_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS retrieval_events_created_at_idx
    ON retrieval.retrieval_events(created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_events_request_id_idx
    ON retrieval.retrieval_events(request_id);

-- =========================
-- observability
-- =========================
CREATE TABLE IF NOT EXISTS observability.token_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model TEXT,
    operation TEXT,
    step TEXT,
    question_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    session_id TEXT,
    request_id TEXT,
    provider_request_id TEXT,
    latency_ms INTEGER,
    cache_hit BOOLEAN,
    cost_usd_input DOUBLE PRECISION,
    cost_usd_output DOUBLE PRECISION,
    cost_usd_total DOUBLE PRECISION,
    run_id TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS observability_token_usage_created_at_idx
    ON observability.token_usage(created_at DESC);
CREATE INDEX IF NOT EXISTS observability_token_usage_session_idx
    ON observability.token_usage(session_id);
CREATE INDEX IF NOT EXISTS observability_token_usage_request_idx
    ON observability.token_usage(request_id);
CREATE INDEX IF NOT EXISTS observability_token_usage_run_idx
    ON observability.token_usage(run_id);
CREATE INDEX IF NOT EXISTS observability_token_usage_step_idx
    ON observability.token_usage(step);
CREATE INDEX IF NOT EXISTS observability_token_usage_question_idx
    ON observability.token_usage(question_id);

CREATE OR REPLACE VIEW observability.token_usage_rollup AS
SELECT
    COALESCE(run_id, '') AS run_id,
    COALESCE(step, '') AS step,
    COALESCE(model, '') AS model,
    COALESCE(question_id, '') AS question_id,
    COUNT(*) AS call_count,
    COALESCE(SUM(COALESCE(input_tokens, 0)), 0) AS input_tokens,
    COALESCE(SUM(COALESCE(output_tokens, 0)), 0) AS output_tokens,
    COALESCE(SUM(COALESCE(total_tokens, 0)), 0) AS total_tokens,
    COALESCE(SUM(COALESCE(cost_usd_input, 0.0)), 0.0) AS cost_usd_input,
    COALESCE(SUM(COALESCE(cost_usd_output, 0.0)), 0.0) AS cost_usd_output,
    COALESCE(SUM(COALESCE(cost_usd_total, 0.0)), 0.0) AS cost_usd_total,
    MIN(created_at) AS first_seen_at,
    MAX(created_at) AS last_seen_at
FROM observability.token_usage
GROUP BY COALESCE(run_id, ''), COALESCE(step, ''), COALESCE(model, ''), COALESCE(question_id, '');

CREATE TABLE IF NOT EXISTS observability.request_failures (
    id BIGSERIAL PRIMARY KEY,
    component TEXT,
    error TEXT,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS observability_request_failures_created_at_idx
    ON observability.request_failures(created_at DESC);

COMMIT;
