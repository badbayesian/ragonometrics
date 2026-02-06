"""Workflow state persistence for multi-step agentic runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


from ragonometrics.core.config import SQLITE_DIR


DEFAULT_STATE_DB = SQLITE_DIR / "ragonometrics_workflow_state.sqlite"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT,
            status TEXT,
            papers_dir TEXT,
            config_hash TEXT,
            metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_steps (
            run_id TEXT,
            step TEXT,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            output_json TEXT,
            PRIMARY KEY (run_id, step)
        )
        """
    )
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
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO workflow_runs
            (run_id, created_at, status, papers_dir, config_hash, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _utc_now(),
                status,
                papers_dir,
                config_hash,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def set_workflow_status(db_path: Path, run_id: str, status: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE workflow_runs SET status = ? WHERE run_id = ?",
            (status, run_id),
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
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    output: Optional[Dict[str, Any]] = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO workflow_steps
            (run_id, step, status, started_at, finished_at, output_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                step,
                status,
                started_at,
                finished_at,
                json.dumps(output or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_workflow_run(db_path: Path, run_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT run_id, created_at, status, papers_dir, config_hash, metadata_json FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        meta = {}
        try:
            meta = json.loads(row[5] or "{}")
        except Exception:
            meta = {}
        return {
            "run_id": row[0],
            "created_at": row[1],
            "status": row[2],
            "papers_dir": row[3],
            "config_hash": row[4],
            "metadata": meta,
        }
    finally:
        conn.close()


def list_workflow_steps(db_path: Path, run_id: str) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT step, status, started_at, finished_at, output_json FROM workflow_steps WHERE run_id = ?",
            (run_id,),
        )
        out = []
        for row in cur.fetchall():
            output = {}
            try:
                output = json.loads(row[4] or "{}")
            except Exception:
                output = {}
            out.append(
                {
                    "step": row[0],
                    "status": row[1],
                    "started_at": row[2],
                    "finished_at": row[3],
                    "output": output,
                }
            )
        return out
    finally:
        conn.close()
