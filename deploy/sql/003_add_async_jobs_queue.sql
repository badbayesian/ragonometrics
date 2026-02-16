-- Add Postgres-backed async queue table for workflow/index jobs.
-- Safe to run multiple times.

BEGIN;

CREATE SCHEMA IF NOT EXISTS workflow;

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

COMMIT;
