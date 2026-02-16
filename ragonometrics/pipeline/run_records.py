"""Unified workflow record store helpers.

This module defines the consolidated `workflow.run_records` table that replaces
separate workflow run/step/report/question/artifact tables.
"""

from __future__ import annotations


def ensure_run_records_table(conn) -> None:
    """Create the unified workflow ledger table and indexes."""
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS workflow")
    cur.execute(
        """
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
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_run_kind_idx
        ON workflow.run_records (run_id, record_kind)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_created_at_idx
        ON workflow.run_records (created_at DESC)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_step_idx
        ON workflow.run_records (step)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_status_idx
        ON workflow.run_records (status)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_workstream_idx
        ON workflow.run_records (workstream_id, arm)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_question_idx
        ON workflow.run_records (question_id)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS workflow_run_records_report_hash_idx
        ON workflow.run_records (report_hash)
        """
    )
    conn.commit()
