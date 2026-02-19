BEGIN;

CREATE SCHEMA IF NOT EXISTS enrichment;

CREATE TABLE IF NOT EXISTS enrichment.openalex_citation_graph_cache (
    cache_key TEXT PRIMARY KEY,
    center_work_id TEXT NOT NULL,
    n_hops SMALLINT NOT NULL,
    max_references INTEGER NOT NULL,
    max_citing INTEGER NOT NULL,
    max_nodes INTEGER NOT NULL,
    algo_version TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    summary_json JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    stale_until TIMESTAMPTZ NOT NULL,
    last_accessed_at TIMESTAMPTZ NOT NULL,
    refresh_job_id TEXT,
    refresh_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(center_work_id)) > 0),
    CHECK (n_hops >= 1 AND n_hops <= 5),
    CHECK (max_references >= 0),
    CHECK (max_citing >= 0),
    CHECK (max_nodes >= 1),
    CHECK (length(trim(algo_version)) > 0),
    CHECK (refresh_failures >= 0)
);

CREATE INDEX IF NOT EXISTS enrichment_openalex_citation_graph_cache_center_updated_idx
    ON enrichment.openalex_citation_graph_cache(center_work_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS enrichment_openalex_citation_graph_cache_expires_idx
    ON enrichment.openalex_citation_graph_cache(expires_at);

CREATE INDEX IF NOT EXISTS enrichment_openalex_citation_graph_cache_stale_idx
    ON enrichment.openalex_citation_graph_cache(stale_until);

CREATE INDEX IF NOT EXISTS enrichment_openalex_citation_graph_cache_last_accessed_idx
    ON enrichment.openalex_citation_graph_cache(last_accessed_at DESC);

COMMIT;
