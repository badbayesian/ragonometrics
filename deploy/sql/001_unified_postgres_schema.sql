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

CREATE TABLE IF NOT EXISTS enrichment.citec_cache (
    cache_key TEXT PRIMARY KEY,
    repec_handle TEXT,
    response JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS enrichment_citec_cache_fetched_at_idx
    ON enrichment.citec_cache(fetched_at DESC);

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
CREATE TABLE IF NOT EXISTS workflow.workflow_runs (
    run_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
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
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS workflow.workstreams (
    workstream_id TEXT PRIMARY KEY,
    name TEXT,
    objective TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS workflow.workstream_runs (
    workstream_id TEXT NOT NULL REFERENCES workflow.workstreams(workstream_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
    arm TEXT,
    source_bucket TEXT,
    is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
    parent_run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workstream_id, run_id)
);
CREATE INDEX IF NOT EXISTS workflow_runs_created_at_idx
    ON workflow.workflow_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS workflow_runs_workstream_idx
    ON workflow.workflow_runs(workstream_id);
CREATE INDEX IF NOT EXISTS workflow_workstream_runs_run_idx
    ON workflow.workstream_runs(run_id);

CREATE TABLE IF NOT EXISTS workflow.workflow_steps (
    run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT,
    step_attempt_id TEXT,
    attempt_no INTEGER,
    queued_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    status_reason TEXT,
    error_code TEXT,
    error_message TEXT,
    worker_id TEXT,
    retry_of_attempt_id TEXT,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS workflow.workflow_reports (
    run_id TEXT PRIMARY KEY REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
    status TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    papers_dir TEXT,
    report_path TEXT,
    agentic_status TEXT,
    report_questions_set TEXT,
    report_hash TEXT,
    report_question_count INTEGER,
    confidence_mean DOUBLE PRECISION,
    confidence_label_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_answer_hash TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS workflow.report_questions (
    run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,
    category TEXT,
    question TEXT,
    answer TEXT,
    confidence TEXT,
    confidence_score DOUBLE PRECISION,
    retrieval_method TEXT,
    evidence_type TEXT,
    assumption_flag BOOLEAN,
    assumption_notes TEXT,
    quote_snippet TEXT,
    table_figure TEXT,
    data_source TEXT,
    citation_anchors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    related_questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, question_id)
);
CREATE TABLE IF NOT EXISTS workflow.artifacts (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, artifact_type, path)
);
CREATE INDEX IF NOT EXISTS workflow_reports_status_idx
    ON workflow.workflow_reports(status);
CREATE INDEX IF NOT EXISTS workflow_reports_started_at_idx
    ON workflow.workflow_reports(started_at DESC);
CREATE INDEX IF NOT EXISTS workflow_reports_finished_at_idx
    ON workflow.workflow_reports(finished_at DESC);
CREATE INDEX IF NOT EXISTS workflow_reports_question_set_idx
    ON workflow.workflow_reports(report_questions_set);
CREATE INDEX IF NOT EXISTS workflow_reports_report_hash_idx
    ON workflow.workflow_reports(report_hash);
CREATE INDEX IF NOT EXISTS workflow_report_questions_conf_idx
    ON workflow.report_questions(confidence);
CREATE INDEX IF NOT EXISTS workflow_reports_payload_gin_idx
    ON workflow.workflow_reports USING GIN(payload);
CREATE INDEX IF NOT EXISTS workflow_reports_payload_path_gin_idx
    ON workflow.workflow_reports USING GIN(payload jsonb_path_ops);

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
