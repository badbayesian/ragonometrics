"""Workflow state persistence for multi-step agentic runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2

# Kept for call-site compatibility; runtime persistence now uses Postgres.
DEFAULT_STATE_DB = Path("postgres_workflow_state")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_url() -> str:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for workflow state persistence.")
    return db_url


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _connect(_db_path: Path) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS workflow")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow.workstreams (
            workstream_id TEXT PRIMARY KEY,
            name TEXT,
            objective TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow.workflow_runs (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            status TEXT,
            papers_dir TEXT,
            config_hash TEXT,
            workstream_id TEXT REFERENCES workflow.workstreams(workstream_id),
            arm TEXT,
            parent_run_id TEXT,
            trigger_source TEXT,
            git_sha TEXT,
            git_branch TEXT,
            config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            paper_set_hash TEXT,
            question TEXT,
            report_question_set TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS workstream_id TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS arm TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS trigger_source TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS git_sha TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS git_branch TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS paper_set_hash TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS question TEXT")
    cur.execute("ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS report_question_set TEXT")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow.workstream_runs (
            workstream_id TEXT NOT NULL REFERENCES workflow.workstreams(workstream_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            arm TEXT,
            source_bucket TEXT,
            is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
            parent_run_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (workstream_id, run_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow.workflow_steps (
            run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            step TEXT NOT NULL,
            status TEXT,
            step_attempt_id TEXT,
            attempt_no INTEGER,
            queued_at TIMESTAMPTZ,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            duration_ms INTEGER,
            status_reason TEXT,
            error_code TEXT,
            error_message TEXT,
            worker_id TEXT,
            retry_of_attempt_id TEXT,
            output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (run_id, step)
        )
        """
    )
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS step_attempt_id TEXT")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS attempt_no INTEGER")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS duration_ms INTEGER")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS status_reason TEXT")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS error_code TEXT")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS error_message TEXT")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS worker_id TEXT")
    cur.execute("ALTER TABLE workflow.workflow_steps ADD COLUMN IF NOT EXISTS retry_of_attempt_id TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS workflow_runs_workstream_idx ON workflow.workflow_runs(workstream_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS workflow_runs_created_at_idx ON workflow.workflow_runs(created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS workflow_workstream_runs_run_idx ON workflow.workstream_runs(run_id)")
    conn.commit()
    return conn


def create_workflow_run(
    db_path: Path,
    *,
    run_id: str,
    papers_dir: str,
    config_hash: Optional[str],
    status: str = "running",
    metadata: Optional[Dict[str, Any]] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    trigger_source: Optional[str] = None,
    git_sha: Optional[str] = None,
    git_branch: Optional[str] = None,
    config_effective: Optional[Dict[str, Any]] = None,
    paper_set_hash: Optional[str] = None,
    question: Optional[str] = None,
    report_question_set: Optional[str] = None,
) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if workstream_id:
            cur.execute(
                """
                INSERT INTO workflow.workstreams (workstream_id, name, objective, created_at, metadata_json)
                VALUES (%s, %s, %s, NOW(), %s::jsonb)
                ON CONFLICT (workstream_id) DO UPDATE SET
                    metadata_json = workflow.workstreams.metadata_json || EXCLUDED.metadata_json
                """,
                (
                    workstream_id,
                    workstream_id,
                    None,
                    json.dumps(
                        {
                            "trigger_source": trigger_source,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        cur.execute(
            """
            INSERT INTO workflow.workflow_runs
            (
                run_id, created_at, started_at, finished_at, status, papers_dir, config_hash,
                workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
                config_effective_json, paper_set_hash, question, report_question_set, metadata_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (run_id) DO UPDATE SET
                started_at = COALESCE(workflow.workflow_runs.started_at, EXCLUDED.started_at),
                finished_at = COALESCE(EXCLUDED.finished_at, workflow.workflow_runs.finished_at),
                status = EXCLUDED.status,
                papers_dir = EXCLUDED.papers_dir,
                config_hash = EXCLUDED.config_hash,
                workstream_id = COALESCE(workflow.workflow_runs.workstream_id, EXCLUDED.workstream_id),
                arm = COALESCE(workflow.workflow_runs.arm, EXCLUDED.arm),
                parent_run_id = COALESCE(workflow.workflow_runs.parent_run_id, EXCLUDED.parent_run_id),
                trigger_source = COALESCE(workflow.workflow_runs.trigger_source, EXCLUDED.trigger_source),
                git_sha = COALESCE(workflow.workflow_runs.git_sha, EXCLUDED.git_sha),
                git_branch = COALESCE(workflow.workflow_runs.git_branch, EXCLUDED.git_branch),
                config_effective_json = CASE
                    WHEN workflow.workflow_runs.config_effective_json IS NULL OR workflow.workflow_runs.config_effective_json = '{}'::jsonb
                        THEN EXCLUDED.config_effective_json
                    ELSE workflow.workflow_runs.config_effective_json
                END,
                paper_set_hash = COALESCE(workflow.workflow_runs.paper_set_hash, EXCLUDED.paper_set_hash),
                question = COALESCE(workflow.workflow_runs.question, EXCLUDED.question),
                report_question_set = COALESCE(workflow.workflow_runs.report_question_set, EXCLUDED.report_question_set),
                metadata_json = workflow.workflow_runs.metadata_json || EXCLUDED.metadata_json
            """,
            (
                run_id,
                _utc_now(),
                started_at,
                finished_at,
                status,
                papers_dir,
                config_hash,
                workstream_id,
                arm,
                parent_run_id,
                trigger_source,
                git_sha,
                git_branch,
                json.dumps(config_effective or {}, ensure_ascii=False),
                paper_set_hash,
                question,
                report_question_set,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        if workstream_id:
            cur.execute(
                """
                INSERT INTO workflow.workstream_runs
                (workstream_id, run_id, arm, source_bucket, is_baseline, parent_run_id, created_at, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
                ON CONFLICT (workstream_id, run_id) DO UPDATE SET
                    arm = COALESCE(workflow.workstream_runs.arm, EXCLUDED.arm),
                    source_bucket = COALESCE(workflow.workstream_runs.source_bucket, EXCLUDED.source_bucket),
                    parent_run_id = COALESCE(workflow.workstream_runs.parent_run_id, EXCLUDED.parent_run_id),
                    metadata_json = workflow.workstream_runs.metadata_json || EXCLUDED.metadata_json
                """,
                (
                    workstream_id,
                    run_id,
                    arm,
                    "current",
                    bool(str(arm or "").strip().lower() in {"baseline", "control", "gpt-5"}),
                    parent_run_id,
                    json.dumps({"created_by": "workflow"}, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def set_workflow_status(db_path: Path, run_id: str, status: str, *, finished_at: Optional[str] = None) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        final_ts = finished_at
        if final_ts is None and status in {"completed", "failed"}:
            final_ts = _utc_now()
        cur.execute(
            """
            UPDATE workflow.workflow_runs
            SET status = %s,
                finished_at = COALESCE(%s, finished_at)
            WHERE run_id = %s
            """,
            (status, final_ts, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_step(
    db_path: Path,
    *,
    run_id: str,
    step: str,
    status: str,
    step_attempt_id: Optional[str] = None,
    attempt_no: Optional[int] = None,
    queued_at: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
    status_reason: Optional[str] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    worker_id: Optional[str] = None,
    retry_of_attempt_id: Optional[str] = None,
    output: Optional[Dict[str, Any]] = None,
) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO workflow.workflow_steps
            (
                run_id, step, status, step_attempt_id, attempt_no, queued_at,
                started_at, finished_at, duration_ms, status_reason,
                error_code, error_message, worker_id, retry_of_attempt_id, output_json
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (run_id, step) DO UPDATE SET
                status = EXCLUDED.status,
                step_attempt_id = COALESCE(EXCLUDED.step_attempt_id, workflow.workflow_steps.step_attempt_id),
                attempt_no = COALESCE(EXCLUDED.attempt_no, workflow.workflow_steps.attempt_no),
                queued_at = COALESCE(EXCLUDED.queued_at, workflow.workflow_steps.queued_at),
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                duration_ms = COALESCE(EXCLUDED.duration_ms, workflow.workflow_steps.duration_ms),
                status_reason = COALESCE(EXCLUDED.status_reason, workflow.workflow_steps.status_reason),
                error_code = COALESCE(EXCLUDED.error_code, workflow.workflow_steps.error_code),
                error_message = COALESCE(EXCLUDED.error_message, workflow.workflow_steps.error_message),
                worker_id = COALESCE(EXCLUDED.worker_id, workflow.workflow_steps.worker_id),
                retry_of_attempt_id = COALESCE(EXCLUDED.retry_of_attempt_id, workflow.workflow_steps.retry_of_attempt_id),
                output_json = EXCLUDED.output_json
            """,
            (
                run_id,
                step,
                status,
                step_attempt_id,
                attempt_no,
                queued_at,
                started_at,
                finished_at,
                duration_ms,
                status_reason,
                error_code,
                error_message,
                worker_id,
                retry_of_attempt_id,
                json.dumps(output or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_workflow_run(db_path: Path, run_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                run_id, created_at, started_at, finished_at, status, papers_dir, config_hash,
                workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
                paper_set_hash, question, report_question_set, metadata_json, config_effective_json
            FROM workflow.workflow_runs
            WHERE run_id = %s
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        meta_value = row[16] if isinstance(row[16], dict) else {}
        if not isinstance(meta_value, dict):
            try:
                meta_value = json.loads(str(row[16] or "{}"))
            except Exception:
                meta_value = {}
        config_effective = row[17] if isinstance(row[17], dict) else {}
        if not isinstance(config_effective, dict):
            try:
                config_effective = json.loads(str(row[17] or "{}"))
            except Exception:
                config_effective = {}
        return {
            "run_id": row[0],
            "created_at": _to_iso(row[1]),
            "started_at": _to_iso(row[2]),
            "finished_at": _to_iso(row[3]),
            "status": row[4],
            "papers_dir": row[5],
            "config_hash": row[6],
            "workstream_id": row[7],
            "arm": row[8],
            "parent_run_id": row[9],
            "trigger_source": row[10],
            "git_sha": row[11],
            "git_branch": row[12],
            "paper_set_hash": row[13],
            "question": row[14],
            "report_question_set": row[15],
            "metadata": meta_value,
            "config_effective": config_effective,
        }
    finally:
        conn.close()


def list_workflow_steps(db_path: Path, run_id: str) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                step, status, step_attempt_id, attempt_no, queued_at,
                started_at, finished_at, duration_ms, status_reason,
                error_code, error_message, worker_id, retry_of_attempt_id, output_json
            FROM workflow.workflow_steps
            WHERE run_id = %s
            ORDER BY started_at NULLS LAST, step
            """,
            (run_id,),
        )
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            output = row[13] if isinstance(row[13], dict) else {}
            if not isinstance(output, dict):
                try:
                    output = json.loads(str(row[13] or "{}"))
                except Exception:
                    output = {}
            out.append(
                {
                    "step": row[0],
                    "status": row[1],
                    "step_attempt_id": row[2],
                    "attempt_no": row[3],
                    "queued_at": _to_iso(row[4]),
                    "started_at": _to_iso(row[5]),
                    "finished_at": _to_iso(row[6]),
                    "duration_ms": row[7],
                    "status_reason": row[8],
                    "error_code": row[9],
                    "error_message": row[10],
                    "worker_id": row[11],
                    "retry_of_attempt_id": row[12],
                    "output": output,
                }
            )
        return out
    finally:
        conn.close()
