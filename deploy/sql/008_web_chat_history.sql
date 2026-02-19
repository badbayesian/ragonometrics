BEGIN;

CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE TABLE IF NOT EXISTS retrieval.chat_history_turns (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT,
    username TEXT NOT NULL,
    session_id TEXT,
    paper_id TEXT NOT NULL,
    paper_path TEXT,
    model TEXT,
    variation_mode BOOLEAN NOT NULL DEFAULT FALSE,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieval_stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    cache_hit BOOLEAN,
    request_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(username)) > 0),
    CHECK (length(trim(paper_id)) > 0),
    CHECK (length(trim(query)) > 0)
);

CREATE INDEX IF NOT EXISTS retrieval_chat_history_user_paper_created_idx
    ON retrieval.chat_history_turns(user_id, paper_id, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_chat_history_session_paper_created_idx
    ON retrieval.chat_history_turns(session_id, paper_id, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_chat_history_username_paper_created_idx
    ON retrieval.chat_history_turns((lower(username)), paper_id, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_chat_history_request_id_idx
    ON retrieval.chat_history_turns(request_id);

COMMIT;
