-- Add email + password reset support, paper notes, and normalized query cache lookups.

BEGIN;

ALTER TABLE auth.streamlit_users
    ADD COLUMN IF NOT EXISTS email TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS auth_streamlit_users_email_ci_idx
    ON auth.streamlit_users ((lower(email)))
    WHERE email IS NOT NULL AND length(trim(email)) > 0;

CREATE TABLE IF NOT EXISTS auth.password_reset_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES auth.streamlit_users(id) ON DELETE CASCADE,
    email TEXT,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    request_ip TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(token_hash)) > 0)
);
CREATE INDEX IF NOT EXISTS auth_password_reset_tokens_user_id_created_idx
    ON auth.password_reset_tokens(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS auth_password_reset_tokens_email_created_idx
    ON auth.password_reset_tokens((lower(email)), created_at DESC);
CREATE INDEX IF NOT EXISTS auth_password_reset_tokens_expires_idx
    ON auth.password_reset_tokens(expires_at DESC);

ALTER TABLE retrieval.query_cache
    ADD COLUMN IF NOT EXISTS query_normalized TEXT;

UPDATE retrieval.query_cache
SET query_normalized = lower(regexp_replace(COALESCE(query, ''), '[^a-zA-Z0-9\\s]+', ' ', 'g'))
WHERE query_normalized IS NULL;

CREATE INDEX IF NOT EXISTS retrieval_query_cache_normalized_idx
    ON retrieval.query_cache(paper_path, model, query_normalized, created_at DESC);

CREATE TABLE IF NOT EXISTS retrieval.paper_notes (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    page_number INTEGER,
    highlight_text TEXT,
    highlight_terms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    note_text TEXT NOT NULL,
    color TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(username)) > 0),
    CHECK (length(trim(paper_id)) > 0),
    CHECK (length(trim(note_text)) > 0)
);
CREATE INDEX IF NOT EXISTS retrieval_paper_notes_user_paper_page_idx
    ON retrieval.paper_notes(user_id, paper_id, page_number, created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_paper_notes_username_paper_page_idx
    ON retrieval.paper_notes((lower(username)), paper_id, page_number, created_at DESC);

COMMIT;
