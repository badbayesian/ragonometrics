"""Workflow state persistence for multi-step agentic runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2

from ragonometrics.pipeline.run_records import ensure_run_records_table

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
    ensure_run_records_table(conn)
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
        now = _utc_now()
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key,
                status, papers_dir, config_hash,
                workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
                config_effective_json, paper_set_hash, question, report_question_set,
                started_at, finished_at, created_at, updated_at,
                payload_json, metadata_json
            )
            VALUES (
                %s, 'run', '', 'main',
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, %s, %s, %s,
                %s, %s, %s, %s,
                %s::jsonb, %s::jsonb
            )
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
                    WHEN workflow.run_records.config_effective_json IS NULL OR workflow.run_records.config_effective_json = '{}'::jsonb
                        THEN EXCLUDED.config_effective_json
                    ELSE workflow.run_records.config_effective_json
                END,
                paper_set_hash = COALESCE(EXCLUDED.paper_set_hash, workflow.run_records.paper_set_hash),
                question = COALESCE(EXCLUDED.question, workflow.run_records.question),
                report_question_set = COALESCE(EXCLUDED.report_question_set, workflow.run_records.report_question_set),
                started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
                finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
                payload_json = CASE
                    WHEN workflow.run_records.payload_json IS NULL OR workflow.run_records.payload_json = '{}'::jsonb
                        THEN EXCLUDED.payload_json
                    ELSE workflow.run_records.payload_json
                END,
                metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                updated_at = EXCLUDED.updated_at
            """,
            (
                run_id,
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
                started_at,
                finished_at,
                now,
                now,
                json.dumps({"source": "state"}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        if workstream_id:
            cur.execute(
                """
                INSERT INTO workflow.run_records
                (
                    run_id, record_kind, step, record_key,
                    status, workstream_id, arm, parent_run_id,
                    created_at, updated_at, payload_json, metadata_json
                )
                VALUES (
                    %s, 'workstream_link', '', %s,
                    %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb
                )
                ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                    status = COALESCE(EXCLUDED.status, workflow.run_records.status),
                    workstream_id = COALESCE(EXCLUDED.workstream_id, workflow.run_records.workstream_id),
                    arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
                    parent_run_id = COALESCE(EXCLUDED.parent_run_id, workflow.run_records.parent_run_id),
                    payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
                    metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    run_id,
                    workstream_id,
                    status,
                    workstream_id,
                    arm,
                    parent_run_id,
                    now,
                    now,
                    json.dumps(
                        {
                            "source_bucket": "current",
                            "is_baseline": bool(str(arm or "").strip().lower() in {"baseline", "control", "gpt-5"}),
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"source": "state"}, ensure_ascii=False),
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
            UPDATE workflow.run_records
            SET status = %s,
                finished_at = COALESCE(%s, finished_at),
                updated_at = NOW()
            WHERE run_id = %s
              AND record_kind = 'run'
              AND step = ''
              AND record_key = 'main'
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
        meta = {
            "step_attempt_id": step_attempt_id,
            "attempt_no": attempt_no,
            "queued_at": queued_at,
            "duration_ms": duration_ms,
            "status_reason": status_reason,
            "error_code": error_code,
            "error_message": error_message,
            "worker_id": worker_id,
            "retry_of_attempt_id": retry_of_attempt_id,
        }
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status,
                started_at, finished_at, created_at, updated_at,
                output_json, metadata_json
            )
            VALUES (
                %s, 'step', %s, 'main', %s,
                %s, %s, NOW(), NOW(),
                %s::jsonb, %s::jsonb
            )
            ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                status = EXCLUDED.status,
                started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
                finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
                output_json = CASE
                    WHEN EXCLUDED.output_json = '{}'::jsonb
                        THEN workflow.run_records.output_json
                    ELSE EXCLUDED.output_json
                END,
                metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            """,
            (
                run_id,
                step,
                status,
                started_at,
                finished_at,
                json.dumps(output or {}, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def find_similar_completed_step(
    db_path: Path,
    *,
    step: str,
    exclude_run_id: str,
    config_hash: Optional[str] = None,
    papers_dir: Optional[str] = None,
    paper_set_hash: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    question: Optional[str] = None,
    report_question_set: Optional[str] = None,
    match_question: bool = False,
    match_report_question_set: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return latest completed step output from a similar prior run, if any."""

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        query = """
            SELECT
                s.run_id,
                s.started_at,
                s.finished_at,
                s.output_json
            FROM workflow.run_records s
            JOIN workflow.run_records r
              ON r.run_id = s.run_id
             AND r.record_kind = 'run'
             AND r.step = ''
             AND r.record_key = 'main'
            WHERE s.record_kind = 'step'
              AND s.step = %s
              AND s.record_key = 'main'
              AND s.status = 'completed'
              AND s.run_id <> %s
        """
        params: List[Any] = [step, exclude_run_id]
        if config_hash:
            query += " AND r.config_hash = %s"
            params.append(config_hash)
        if papers_dir:
            query += " AND r.papers_dir = %s"
            params.append(papers_dir)
        if paper_set_hash:
            query += " AND r.paper_set_hash = %s"
            params.append(paper_set_hash)
        if workstream_id:
            query += " AND r.workstream_id = %s"
            params.append(workstream_id)
        if arm:
            query += " AND r.arm = %s"
            params.append(arm)
        if match_question:
            query += " AND COALESCE(r.question, '') = %s"
            params.append(question or "")
        if match_report_question_set:
            query += " AND COALESCE(r.report_question_set, '') = %s"
            params.append(report_question_set or "")
        query += """
            ORDER BY COALESCE(s.finished_at, s.updated_at, s.created_at) DESC
            LIMIT 1
        """
        cur.execute(query, params)
        row = cur.fetchone()
        if not row:
            return None
        output = row[3] if isinstance(row[3], dict) else {}
        if not isinstance(output, dict):
            try:
                output = json.loads(str(row[3] or "{}"))
            except Exception:
                output = {}
        return {
            "run_id": row[0],
            "started_at": _to_iso(row[1]),
            "finished_at": _to_iso(row[2]),
            "output": output,
        }
    finally:
        conn.close()


def find_similar_report_question_items(
    db_path: Path,
    *,
    exclude_run_id: str,
    config_hash: Optional[str] = None,
    papers_dir: Optional[str] = None,
    paper_set_hash: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    question: Optional[str] = None,
    report_question_set: Optional[str] = None,
    match_question: bool = False,
    match_report_question_set: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Return latest reusable structured question payloads keyed by question_id."""

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        query = """
            SELECT DISTINCT ON (q.question_id)
                q.question_id,
                q.run_id,
                r.finished_at,
                q.payload_json
            FROM workflow.run_records q
            JOIN workflow.run_records r
              ON r.run_id = q.run_id
             AND r.record_kind = 'run'
             AND r.step = ''
             AND r.record_key = 'main'
            WHERE q.record_kind = 'question'
              AND q.step = 'agentic'
              AND q.question_id IS NOT NULL
              AND q.run_id <> %s
              AND r.status = 'completed'
        """
        params: List[Any] = [exclude_run_id]
        if config_hash:
            query += " AND r.config_hash = %s"
            params.append(config_hash)
        if papers_dir:
            query += " AND r.papers_dir = %s"
            params.append(papers_dir)
        if paper_set_hash:
            query += " AND r.paper_set_hash = %s"
            params.append(paper_set_hash)
        if workstream_id:
            query += " AND r.workstream_id = %s"
            params.append(workstream_id)
        if arm:
            query += " AND r.arm = %s"
            params.append(arm)
        if match_question:
            query += " AND COALESCE(r.question, '') = %s"
            params.append(question or "")
        if match_report_question_set:
            query += " AND COALESCE(r.report_question_set, '') = %s"
            params.append(report_question_set or "")
        query += """
            ORDER BY q.question_id, COALESCE(r.finished_at, q.updated_at, q.created_at) DESC
        """
        cur.execute(query, params)
        out: Dict[str, Dict[str, Any]] = {}
        for qid, source_run_id, source_finished_at, payload in cur.fetchall():
            question_id = str(qid or "").strip()
            if not question_id:
                continue
            item = payload if isinstance(payload, dict) else {}
            if not isinstance(item, dict):
                try:
                    item = json.loads(str(payload or "{}"))
                except Exception:
                    item = {}
            if not item:
                continue
            out[question_id] = {
                "source_run_id": str(source_run_id or ""),
                "source_finished_at": _to_iso(source_finished_at),
                "item": item,
            }
        return out
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
            FROM workflow.run_records
            WHERE run_id = %s
              AND record_kind = 'run'
              AND step = ''
              AND record_key = 'main'
            LIMIT 1
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
                step, status, started_at, finished_at, output_json, metadata_json
            FROM workflow.run_records
            WHERE run_id = %s
              AND record_kind = 'step'
            ORDER BY started_at NULLS LAST, step
            """,
            (run_id,),
        )
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            output = row[4] if isinstance(row[4], dict) else {}
            if not isinstance(output, dict):
                try:
                    output = json.loads(str(row[4] or "{}"))
                except Exception:
                    output = {}
            meta = row[5] if isinstance(row[5], dict) else {}
            if not isinstance(meta, dict):
                try:
                    meta = json.loads(str(row[5] or "{}"))
                except Exception:
                    meta = {}
            out.append(
                {
                    "step": row[0],
                    "status": row[1],
                    "step_attempt_id": meta.get("step_attempt_id"),
                    "attempt_no": meta.get("attempt_no"),
                    "queued_at": _to_iso(meta.get("queued_at")),
                    "started_at": _to_iso(row[2]),
                    "finished_at": _to_iso(row[3]),
                    "duration_ms": meta.get("duration_ms"),
                    "status_reason": meta.get("status_reason"),
                    "error_code": meta.get("error_code"),
                    "error_message": meta.get("error_message"),
                    "worker_id": meta.get("worker_id"),
                    "retry_of_attempt_id": meta.get("retry_of_attempt_id"),
                    "output": output,
                }
            )
        return out
    finally:
        conn.close()
