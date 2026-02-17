-- Add deterministic idempotency/reuse lineage fields and usage rollup view.

ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS input_hash TEXT;

ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS reuse_source_run_id TEXT;

ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS reuse_source_record_key TEXT;

CREATE INDEX IF NOT EXISTS workflow_run_records_idempotency_idx
    ON workflow.run_records(record_kind, step, idempotency_key);

CREATE INDEX IF NOT EXISTS workflow_run_records_input_hash_idx
    ON workflow.run_records(record_kind, step, input_hash);

CREATE INDEX IF NOT EXISTS workflow_run_records_reuse_source_idx
    ON workflow.run_records(reuse_source_run_id, reuse_source_record_key);

CREATE OR REPLACE VIEW observability.token_usage_rollup AS
SELECT
    COALESCE(run_id, '') AS run_id,
    COALESCE(step, '') AS step,
    COALESCE(model, '') AS model,
    COALESCE(question_id, '') AS question_id,
    COUNT(*) AS call_count,
    COALESCE(SUM(COALESCE(input_tokens, 0)), 0) AS input_tokens,
    COALESCE(SUM(COALESCE(output_tokens, 0)), 0) AS output_tokens,
    COALESCE(SUM(COALESCE(total_tokens, 0)), 0) AS total_tokens,
    COALESCE(SUM(COALESCE(cost_usd_input, 0.0)), 0.0) AS cost_usd_input,
    COALESCE(SUM(COALESCE(cost_usd_output, 0.0)), 0.0) AS cost_usd_output,
    COALESCE(SUM(COALESCE(cost_usd_total, 0.0)), 0.0) AS cost_usd_total,
    MIN(created_at) AS first_seen_at,
    MAX(created_at) AS last_seen_at
FROM observability.token_usage
GROUP BY COALESCE(run_id, ''), COALESCE(step, ''), COALESCE(model, ''), COALESCE(question_id, '');

