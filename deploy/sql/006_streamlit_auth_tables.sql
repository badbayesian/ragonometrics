BEGIN;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.streamlit_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(username)) > 0),
    CHECK (length(trim(password_hash)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS auth_streamlit_users_username_ci_idx
    ON auth.streamlit_users ((lower(username)));
CREATE INDEX IF NOT EXISTS auth_streamlit_users_active_idx
    ON auth.streamlit_users (is_active, updated_at DESC);

CREATE TABLE IF NOT EXISTS auth.streamlit_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    user_id BIGINT REFERENCES auth.streamlit_users(id) ON DELETE SET NULL,
    username TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'streamlit_ui',
    authenticated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(session_id)) > 0),
    CHECK (length(trim(username)) > 0)
);
CREATE INDEX IF NOT EXISTS auth_streamlit_sessions_user_id_idx
    ON auth.streamlit_sessions (user_id);
CREATE INDEX IF NOT EXISTS auth_streamlit_sessions_username_ci_idx
    ON auth.streamlit_sessions ((lower(username)), authenticated_at DESC);
CREATE INDEX IF NOT EXISTS auth_streamlit_sessions_authenticated_idx
    ON auth.streamlit_sessions (authenticated_at DESC);

COMMIT;
