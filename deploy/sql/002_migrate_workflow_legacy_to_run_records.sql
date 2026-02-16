-- Migrate legacy workflow tables into the unified workflow.run_records ledger.
-- Safe to run multiple times (idempotent via ON CONFLICT).

BEGIN;

CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE IF NOT EXISTS workflow.run_records (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    step TEXT NOT NULL DEFAULT '',
    record_key TEXT NOT NULL DEFAULT '',
    status TEXT,
    papers_dir TEXT,
    config_hash TEXT,
    workstream_id TEXT,
    arm TEXT,
    parent_run_id TEXT,
    trigger_source TEXT,
    git_sha TEXT,
    git_branch TEXT,
    config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    paper_set_hash TEXT,
    question TEXT,
    report_question_set TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    report_path TEXT,
    agentic_status TEXT,
    report_hash TEXT,
    report_question_count INTEGER,
    confidence_mean DOUBLE PRECISION,
    confidence_label_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    final_answer_hash TEXT,
    question_id TEXT,
    artifact_type TEXT,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (record_kind IN ('run', 'step', 'report', 'question', 'artifact', 'workstream_link')),
    UNIQUE (run_id, record_kind, step, record_key)
);

DO $$
BEGIN
    IF to_regclass('workflow.workflow_runs') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status,
            papers_dir, config_hash, workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
            config_effective_json, paper_set_hash, question, report_question_set,
            started_at, finished_at, created_at, updated_at, payload_json, metadata_json
        )
        SELECT
            wr.run_id, 'run', '', 'main', wr.status,
            wr.papers_dir, wr.config_hash, wr.workstream_id, wr.arm, wr.parent_run_id, wr.trigger_source, wr.git_sha, wr.git_branch,
            COALESCE(wr.config_effective_json, '{}'::jsonb), wr.paper_set_hash, wr.question, wr.report_question_set,
            wr.started_at, wr.finished_at, COALESCE(wr.created_at, NOW()), COALESCE(wr.created_at, NOW()),
            jsonb_build_object('source', 'legacy_workflow_runs'),
            COALESCE(wr.metadata_json, '{}'::jsonb)
        FROM workflow.workflow_runs wr
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = COALESCE(EXCLUDED.status, workflow.run_records.status),
            papers_dir = COALESCE(EXCLUDED.papers_dir, workflow.run_records.papers_dir),
            config_hash = COALESCE(EXCLUDED.config_hash, workflow.run_records.config_hash),
            workstream_id = COALESCE(EXCLUDED.workstream_id, workflow.run_records.workstream_id),
            arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
            parent_run_id = COALESCE(EXCLUDED.parent_run_id, workflow.run_records.parent_run_id),
            trigger_source = COALESCE(EXCLUDED.trigger_source, workflow.run_records.trigger_source),
            git_sha = COALESCE(EXCLUDED.git_sha, workflow.run_records.git_sha),
            git_branch = COALESCE(EXCLUDED.git_branch, workflow.run_records.git_branch),
            config_effective_json = CASE
                WHEN workflow.run_records.config_effective_json = '{}'::jsonb THEN EXCLUDED.config_effective_json
                ELSE workflow.run_records.config_effective_json
            END,
            paper_set_hash = COALESCE(EXCLUDED.paper_set_hash, workflow.run_records.paper_set_hash),
            question = COALESCE(EXCLUDED.question, workflow.run_records.question),
            report_question_set = COALESCE(EXCLUDED.report_question_set, workflow.run_records.report_question_set),
            started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;

    IF to_regclass('workflow.workstream_runs') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status,
            workstream_id, arm, parent_run_id,
            created_at, updated_at, payload_json, metadata_json
        )
        SELECT
            ws.run_id, 'workstream_link', '', COALESCE(ws.workstream_id, ''), NULL,
            ws.workstream_id, ws.arm, ws.parent_run_id,
            COALESCE(ws.created_at, NOW()), COALESCE(ws.created_at, NOW()),
            jsonb_build_object('source_bucket', ws.source_bucket, 'is_baseline', ws.is_baseline),
            COALESCE(ws.metadata_json, '{}'::jsonb)
        FROM workflow.workstream_runs ws
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            workstream_id = COALESCE(EXCLUDED.workstream_id, workflow.run_records.workstream_id),
            arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
            parent_run_id = COALESCE(EXCLUDED.parent_run_id, workflow.run_records.parent_run_id),
            payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;

    IF to_regclass('workflow.workflow_steps') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status,
            started_at, finished_at, created_at, updated_at,
            output_json, metadata_json
        )
        SELECT
            st.run_id, 'step', COALESCE(st.step, ''), 'main', st.status,
            st.started_at, st.finished_at, COALESCE(st.started_at, NOW()), COALESCE(st.finished_at, COALESCE(st.started_at, NOW())),
            COALESCE(st.output_json, '{}'::jsonb),
            jsonb_strip_nulls(
                jsonb_build_object(
                    'step_attempt_id', st.step_attempt_id,
                    'attempt_no', st.attempt_no,
                    'queued_at', st.queued_at,
                    'duration_ms', st.duration_ms,
                    'status_reason', st.status_reason,
                    'error_code', st.error_code,
                    'error_message', st.error_message,
                    'worker_id', st.worker_id,
                    'retry_of_attempt_id', st.retry_of_attempt_id
                )
            )
        FROM workflow.workflow_steps st
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = EXCLUDED.status,
            started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
            output_json = EXCLUDED.output_json,
            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;

    IF to_regclass('workflow.workflow_reports') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status,
            started_at, finished_at, papers_dir, report_path, agentic_status, report_question_set,
            report_hash, report_question_count, confidence_mean, confidence_label_counts_json, final_answer_hash,
            created_at, updated_at, payload_json
        )
        SELECT
            rp.run_id, 'report', 'report', 'main', rp.status,
            rp.started_at, rp.finished_at, rp.papers_dir, rp.report_path, rp.agentic_status, rp.report_questions_set,
            rp.report_hash, rp.report_question_count, rp.confidence_mean, COALESCE(rp.confidence_label_counts_json, '{}'::jsonb), rp.final_answer_hash,
            COALESCE(rp.created_at, NOW()), COALESCE(rp.updated_at, COALESCE(rp.created_at, NOW())), rp.payload
        FROM workflow.workflow_reports rp
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            papers_dir = EXCLUDED.papers_dir,
            report_path = EXCLUDED.report_path,
            agentic_status = EXCLUDED.agentic_status,
            report_question_set = EXCLUDED.report_question_set,
            report_hash = EXCLUDED.report_hash,
            report_question_count = EXCLUDED.report_question_count,
            confidence_mean = EXCLUDED.confidence_mean,
            confidence_label_counts_json = EXCLUDED.confidence_label_counts_json,
            final_answer_hash = EXCLUDED.final_answer_hash,
            payload_json = EXCLUDED.payload_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;

    IF to_regclass('workflow.report_questions') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status, question_id,
            created_at, updated_at, payload_json
        )
        SELECT
            rq.run_id, 'question', 'agentic', COALESCE(rq.question_id, ''), rq.confidence, rq.question_id,
            COALESCE(rq.created_at, NOW()), COALESCE(rq.updated_at, COALESCE(rq.created_at, NOW())),
            jsonb_strip_nulls(
                jsonb_build_object(
                    'id', rq.question_id,
                    'category', rq.category,
                    'question', rq.question,
                    'answer', rq.answer,
                    'confidence', rq.confidence,
                    'confidence_score', rq.confidence_score,
                    'retrieval_method', rq.retrieval_method,
                    'evidence_type', rq.evidence_type,
                    'assumption_flag', rq.assumption_flag,
                    'assumption_notes', rq.assumption_notes,
                    'quote_snippet', rq.quote_snippet,
                    'table_figure', rq.table_figure,
                    'data_source', rq.data_source,
                    'citation_anchors', COALESCE(rq.citation_anchors_json, '[]'::jsonb),
                    'related_questions', COALESCE(rq.related_questions_json, '[]'::jsonb)
                )
            )
        FROM workflow.report_questions rq
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = EXCLUDED.status,
            question_id = EXCLUDED.question_id,
            payload_json = EXCLUDED.payload_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;

    IF to_regclass('workflow.artifacts') IS NOT NULL THEN
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key, status,
            artifact_type, artifact_path, artifact_sha256,
            created_at, updated_at, payload_json
        )
        SELECT
            ar.run_id, 'artifact', 'report', COALESCE(ar.artifact_type, '') || ':' || COALESCE(ar.path, ''), NULL,
            ar.artifact_type, ar.path, ar.sha256,
            COALESCE(ar.created_at, NOW()), COALESCE(ar.created_at, NOW()),
            COALESCE(ar.meta_json, '{}'::jsonb)
        FROM workflow.artifacts ar
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            artifact_type = COALESCE(EXCLUDED.artifact_type, workflow.run_records.artifact_type),
            artifact_path = COALESCE(EXCLUDED.artifact_path, workflow.run_records.artifact_path),
            artifact_sha256 = COALESCE(EXCLUDED.artifact_sha256, workflow.run_records.artifact_sha256),
            payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
            updated_at = GREATEST(workflow.run_records.updated_at, EXCLUDED.updated_at);
    END IF;
END
$$;

COMMIT;
