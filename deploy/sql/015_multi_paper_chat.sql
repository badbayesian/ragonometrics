BEGIN;

CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE TABLE IF NOT EXISTS retrieval.multi_chat_sessions (
    conversation_id TEXT PRIMARY KEY,
    user_id BIGINT,
    username TEXT NOT NULL,
    project_id TEXT,
    persona_id TEXT,
    session_id TEXT,
    seed_paper_id TEXT,
    paper_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    paper_paths_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(conversation_id)) > 0),
    CHECK (length(trim(username)) > 0)
);

CREATE INDEX IF NOT EXISTS retrieval_multi_chat_sessions_project_user_created_idx
    ON retrieval.multi_chat_sessions(project_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_multi_chat_sessions_project_username_created_idx
    ON retrieval.multi_chat_sessions(project_id, (lower(username)), created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_multi_chat_sessions_updated_idx
    ON retrieval.multi_chat_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS retrieval.multi_chat_turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES retrieval.multi_chat_sessions(conversation_id) ON DELETE CASCADE,
    user_id BIGINT,
    username TEXT NOT NULL,
    project_id TEXT,
    persona_id TEXT,
    session_id TEXT,
    model TEXT,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    paper_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    paper_answers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    comparison_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    aggregate_provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    suggested_papers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(username)) > 0),
    CHECK (length(trim(query)) > 0),
    CHECK (length(trim(answer)) > 0)
);

CREATE INDEX IF NOT EXISTS retrieval_multi_chat_turns_conversation_created_idx
    ON retrieval.multi_chat_turns(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_multi_chat_turns_project_user_created_idx
    ON retrieval.multi_chat_turns(project_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_multi_chat_turns_project_username_created_idx
    ON retrieval.multi_chat_turns(project_id, (lower(username)), created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_multi_chat_turns_request_id_idx
    ON retrieval.multi_chat_turns(request_id);

COMMIT;
