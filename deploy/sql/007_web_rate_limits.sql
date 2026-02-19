BEGIN;

CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.request_rate_limits (
    id BIGSERIAL PRIMARY KEY,
    subject_key TEXT NOT NULL,
    route TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_seconds INTEGER NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(subject_key)) > 0),
    CHECK (length(trim(route)) > 0),
    CHECK (window_seconds > 0),
    CHECK (request_count >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS auth_request_rate_limits_unique_idx
    ON auth.request_rate_limits(subject_key, route, window_start);

CREATE INDEX IF NOT EXISTS auth_request_rate_limits_route_window_idx
    ON auth.request_rate_limits(route, window_start DESC);

COMMIT;

