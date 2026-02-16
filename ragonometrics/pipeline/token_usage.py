"""Postgres-backed token usage logging and reporting."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2

# Kept for call-site compatibility; runtime persistence now uses Postgres.
DEFAULT_USAGE_DB = Path("postgres_token_usage")


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated token usage statistics."""

    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


def _database_url() -> str:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for token usage persistence.")
    return db_url


def _connect(_db_path: Path) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS observability")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS observability.token_usage (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model TEXT,
            operation TEXT,
            step TEXT,
            question_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            session_id TEXT,
            request_id TEXT,
            provider_request_id TEXT,
            latency_ms INTEGER,
            cache_hit BOOLEAN,
            cost_usd_input DOUBLE PRECISION,
            cost_usd_output DOUBLE PRECISION,
            cost_usd_total DOUBLE PRECISION,
            run_id TEXT,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS step TEXT")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS question_id TEXT")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS provider_request_id TEXT")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS latency_ms INTEGER")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS cost_usd_input DOUBLE PRECISION")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS cost_usd_output DOUBLE PRECISION")
    cur.execute("ALTER TABLE observability.token_usage ADD COLUMN IF NOT EXISTS cost_usd_total DOUBLE PRECISION")
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_created_at_idx
        ON observability.token_usage(created_at DESC)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_session_idx
        ON observability.token_usage(session_id)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_request_idx
        ON observability.token_usage(request_id)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_run_idx
        ON observability.token_usage(run_id)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_step_idx
        ON observability.token_usage(step)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS observability_token_usage_question_idx
        ON observability.token_usage(question_id)
        """
    )
    conn.commit()
    return conn


def record_usage(
    *,
    db_path: Path = DEFAULT_USAGE_DB,
    model: str,
    operation: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step: Optional[str] = None,
    question_id: Optional[str] = None,
    provider_request_id: Optional[str] = None,
    latency_ms: Optional[int] = None,
    cache_hit: Optional[bool] = None,
    cost_usd_input: Optional[float] = None,
    cost_usd_output: Optional[float] = None,
    cost_usd_total: Optional[float] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist a token usage record to Postgres."""
    conn = _connect(db_path)
    try:
        resolved_run_id = run_id
        resolved_step = step
        resolved_question_id = question_id
        if isinstance(meta, dict):
            if resolved_run_id is None:
                resolved_run_id = meta.get("run_id")
            if resolved_step is None:
                resolved_step = meta.get("step")
            if resolved_question_id is None:
                resolved_question_id = meta.get("question_id")
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO observability.token_usage
            (
                created_at, model, operation, step, question_id,
                input_tokens, output_tokens, total_tokens,
                session_id, request_id, provider_request_id,
                latency_ms, cache_hit,
                cost_usd_input, cost_usd_output, cost_usd_total,
                run_id, meta
            )
            VALUES (
                NOW(), %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s::jsonb
            )
            """,
            (
                model,
                operation,
                resolved_step,
                resolved_question_id,
                int(input_tokens),
                int(output_tokens),
                int(total_tokens),
                session_id,
                request_id,
                provider_request_id,
                int(latency_ms) if latency_ms is not None else None,
                cache_hit,
                float(cost_usd_input) if cost_usd_input is not None else None,
                float(cost_usd_output) if cost_usd_output is not None else None,
                float(cost_usd_total) if cost_usd_total is not None else None,
                str(resolved_run_id) if resolved_run_id is not None else None,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _where_clauses(
    *,
    session_id: Optional[str],
    request_id: Optional[str],
    since: Optional[str],
) -> tuple[str, List[Any]]:
    clauses = []
    params: List[Any] = []
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
    if request_id:
        clauses.append("request_id = %s")
        params.append(request_id)
    if since:
        clauses.append("created_at >= %s")
        params.append(since)
    if clauses:
        return " WHERE " + " AND ".join(clauses), params
    return "", params


def get_usage_summary(
    *,
    db_path: Path = DEFAULT_USAGE_DB,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    since: Optional[str] = None,
) -> UsageSummary:
    """Return aggregate usage stats."""
    conn = _connect(db_path)
    try:
        where_sql, params = _where_clauses(session_id=session_id, request_id=request_id, since=since)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*),
                   COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0),
                   COALESCE(SUM(total_tokens), 0)
            FROM observability.token_usage
            {where_sql}
            """,
            params,
        )
        row = cur.fetchone()
        return UsageSummary(
            calls=int(row[0] or 0),
            input_tokens=int(row[1] or 0),
            output_tokens=int(row[2] or 0),
            total_tokens=int(row[3] or 0),
        )
    finally:
        conn.close()


def get_usage_by_model(
    *,
    db_path: Path = DEFAULT_USAGE_DB,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return aggregated usage totals grouped by model."""
    conn = _connect(db_path)
    try:
        where_sql, params = _where_clauses(session_id=session_id, request_id=request_id, since=since)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM observability.token_usage
            {where_sql}
            GROUP BY model
            ORDER BY total_tokens DESC
            """,
            params,
        )
        rows = cur.fetchall()
        return [
            {"model": row[0], "calls": int(row[1] or 0), "total_tokens": int(row[2] or 0)}
            for row in rows
        ]
    finally:
        conn.close()


def get_recent_usage(
    *,
    db_path: Path = DEFAULT_USAGE_DB,
    limit: int = 200,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the most recent usage rows."""
    conn = _connect(db_path)
    try:
        where_sql, params = _where_clauses(session_id=session_id, request_id=request_id, since=None)
        params.append(int(limit))
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT created_at, model, operation, step, question_id, input_tokens, output_tokens, total_tokens, session_id, request_id, run_id
            FROM observability.token_usage
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
        return [
            {
                "created_at": row[0].isoformat() if hasattr(row[0], "isoformat") else row[0],
                "model": row[1],
                "operation": row[2],
                "step": row[3],
                "question_id": row[4],
                "input_tokens": int(row[5] or 0),
                "output_tokens": int(row[6] or 0),
                "total_tokens": int(row[7] or 0),
                "session_id": row[8],
                "request_id": row[9],
                "run_id": row[10],
            }
            for row in rows
        ]
    finally:
        conn.close()
