BEGIN;

CREATE TABLE IF NOT EXISTS retrieval.project_query_cache (
    project_cache_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    query TEXT,
    query_normalized TEXT,
    paper_path TEXT,
    model TEXT,
    context_hash TEXT,
    answer TEXT,
    paper_fingerprint TEXT,
    prompt_profile_hash TEXT,
    retrieval_profile_hash TEXT,
    persona_profile_hash TEXT,
    persona_hash TEXT,
    safety_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS retrieval_project_query_cache_lookup_idx
    ON retrieval.project_query_cache(project_id, paper_path, model, query_normalized, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_project_query_cache_fingerprint_idx
    ON retrieval.project_query_cache(
        project_id,
        paper_fingerprint,
        model,
        query_normalized,
        prompt_profile_hash,
        retrieval_profile_hash,
        persona_profile_hash,
        created_at DESC
    );

ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS paper_fingerprint TEXT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS prompt_profile_hash TEXT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS retrieval_profile_hash TEXT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS persona_profile_hash TEXT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS share_eligible BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS source_project_id TEXT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS source_user_id BIGINT;
ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS safety_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE retrieval.query_cache
SET share_eligible = TRUE
WHERE share_eligible IS NULL;

CREATE INDEX IF NOT EXISTS retrieval_query_cache_shared_guardrail_idx
    ON retrieval.query_cache(
        paper_fingerprint,
        model,
        query_normalized,
        prompt_profile_hash,
        retrieval_profile_hash,
        persona_profile_hash,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS retrieval_query_cache_share_eligible_idx
    ON retrieval.query_cache(share_eligible, created_at DESC);

COMMIT;

