"""Usage telemetry service wrappers."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import pooled_connection

DEFAULT_PROJECT_ID = "default-shared"


def _database_url() -> str:
    """Internal helper for database url."""
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for usage queries.")
    return db_url


def _where_sql(
    *,
    session_id: Optional[str],
    request_id: Optional[str],
    since: Optional[str],
    account_user_id: Optional[int],
    account_username: Optional[str],
    project_id: Optional[str],
) -> tuple[str, List[Any]]:
    """Internal helper for where sql."""
    clauses: List[str] = []
    params: List[Any] = []
    if session_id:
        clauses.append("u.session_id = %s")
        params.append(session_id)
    if request_id:
        clauses.append("u.request_id = %s")
        params.append(request_id)
    if since:
        clauses.append("u.created_at >= %s")
        params.append(since)
    normalized_project = str(project_id or "").strip()
    if normalized_project:
        if normalized_project == DEFAULT_PROJECT_ID:
            clauses.append("(COALESCE(u.project_id, '') = %s OR COALESCE(u.project_id, '') = '')")
            params.append(normalized_project)
        else:
            clauses.append("COALESCE(u.project_id, '') = %s")
            params.append(normalized_project)

    account_parts: List[str] = []
    if account_user_id is not None:
        account_parts.append("s.user_id = %s")
        params.append(int(account_user_id))
    normalized_username = str(account_username or "").strip()
    if normalized_username:
        account_parts.append("lower(COALESCE(s.username, '')) = lower(%s)")
        params.append(normalized_username)
    if account_parts:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM auth.streamlit_sessions s "
            "WHERE s.session_id = u.session_id "
            "AND s.revoked_at IS NULL "
            f"AND ({' OR '.join(account_parts)})"
            ")"
        )

    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def usage_summary(
    *,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    since: Optional[str] = None,
    account_user_id: Optional[int] = None,
    account_username: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return one usage summary payload."""
    where_sql, params = _where_sql(
        session_id=session_id,
        request_id=request_id,
        since=since,
        account_user_id=account_user_id,
        account_username=account_username,
        project_id=project_id,
    )
    with pooled_connection(_database_url(), require_migrated=True) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(u.input_tokens), 0),
                COALESCE(SUM(u.output_tokens), 0),
                COALESCE(SUM(u.total_tokens), 0)
            FROM observability.token_usage u
            {where_sql}
            """,
            params,
        )
        row = cur.fetchone() or [0, 0, 0, 0]
    return {
        "calls": int(row[0] or 0),
        "input_tokens": int(row[1] or 0),
        "output_tokens": int(row[2] or 0),
        "total_tokens": int(row[3] or 0),
    }


def usage_by_model(
    *,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    since: Optional[str] = None,
    account_user_id: Optional[int] = None,
    account_username: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return usage grouped by model."""
    where_sql, params = _where_sql(
        session_id=session_id,
        request_id=request_id,
        since=since,
        account_user_id=account_user_id,
        account_username=account_username,
        project_id=project_id,
    )
    with pooled_connection(_database_url(), require_migrated=True) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                u.model,
                COUNT(*) AS calls,
                COALESCE(SUM(u.total_tokens), 0) AS total_tokens
            FROM observability.token_usage u
            {where_sql}
            GROUP BY u.model
            ORDER BY total_tokens DESC
            """,
            params,
        )
        rows = cur.fetchall()
    return [{"model": row[0], "calls": int(row[1] or 0), "total_tokens": int(row[2] or 0)} for row in rows]


def recent_usage(
    *,
    limit: int = 200,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    account_user_id: Optional[int] = None,
    account_username: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return recent usage rows."""
    where_sql, params = _where_sql(
        session_id=session_id,
        request_id=request_id,
        since=None,
        account_user_id=account_user_id,
        account_username=account_username,
        project_id=project_id,
    )
    params_with_limit = [*params, int(limit)]
    with pooled_connection(_database_url(), require_migrated=True) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT
                u.created_at,
                u.model,
                u.operation,
                u.step,
                u.question_id,
                u.input_tokens,
                u.output_tokens,
                u.total_tokens,
                u.session_id,
                u.request_id,
                u.run_id
            FROM observability.token_usage u
            {where_sql}
            ORDER BY u.created_at DESC
            LIMIT %s
            """,
            params_with_limit,
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
