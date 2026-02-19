BEGIN;

CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE TABLE IF NOT EXISTS retrieval.paper_comparison_runs (
    comparison_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by_user_id BIGINT,
    created_by_username TEXT NOT NULL,
    model TEXT NOT NULL,
    compute_mode TEXT NOT NULL DEFAULT 'cache_only',
    visibility TEXT NOT NULL DEFAULT 'shared',
    status TEXT NOT NULL DEFAULT 'ready',
    paper_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    paper_paths_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    seed_paper_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(created_by_username)) > 0),
    CHECK (length(trim(model)) > 0),
    CHECK (compute_mode IN ('cache_only')),
    CHECK (visibility IN ('shared')),
    CHECK (status IN ('ready', 'filling', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_runs_created_idx
    ON retrieval.paper_comparison_runs(created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_runs_username_idx
    ON retrieval.paper_comparison_runs((lower(created_by_username)), created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_runs_status_idx
    ON retrieval.paper_comparison_runs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS retrieval.paper_comparison_cells (
    id BIGSERIAL PRIMARY KEY,
    comparison_id TEXT NOT NULL REFERENCES retrieval.paper_comparison_runs(comparison_id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL,
    paper_path TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question_normalized TEXT NOT NULL,
    model TEXT NOT NULL,
    cell_status TEXT NOT NULL,
    answer TEXT,
    answer_source TEXT,
    cache_hit_layer TEXT,
    cache_key TEXT,
    context_hash TEXT,
    structured_fields_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (cell_status IN ('cached', 'missing', 'generated', 'failed')),
    CHECK (answer_source IS NULL OR answer_source IN ('query_cache', 'generated')),
    CHECK (cache_hit_layer IS NULL OR cache_hit_layer IN ('normalized', 'strict', 'none'))
);

CREATE UNIQUE INDEX IF NOT EXISTS retrieval_paper_comparison_cells_unique_idx
    ON retrieval.paper_comparison_cells(comparison_id, paper_id, question_id);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_cells_status_idx
    ON retrieval.paper_comparison_cells(comparison_id, cell_status, updated_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_cells_question_idx
    ON retrieval.paper_comparison_cells(comparison_id, question_id, paper_id);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_cells_paper_idx
    ON retrieval.paper_comparison_cells(comparison_id, paper_id, question_id);

COMMIT;
