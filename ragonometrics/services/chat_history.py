"""Server-side chat history persistence for Flask web UI."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import pooled_connection


MAX_HISTORY_LIMIT = 200


def _clean_username(username: Optional[str]) -> str:
    """Internal helper for clean username."""
    return str(username or "").strip()


def _history_scope_sql(*, user_id: Optional[int], username: str) -> tuple[str, List[Any]]:
    """Internal helper for history scope sql."""
    if user_id is not None:
        return (
            "(user_id = %s OR (user_id IS NULL AND lower(username) = lower(%s)))",
            [int(user_id), username],
        )
    return ("lower(username) = lower(%s)", [username])


def _parse_json_list(value: Any) -> List[Any]:
    """Parse a JSON list payload; fallback to empty list."""
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_json_dict(value: Any) -> Dict[str, Any]:
    """Parse a JSON object payload; fallback to empty dict."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_turns(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    paper_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return recent chat turns for one user+paper scope."""
    clean_username = _clean_username(username)
    clean_project_id = str(project_id or "").strip()
    clean_paper_id = str(paper_id or "").strip()
    if not db_url or not clean_username or not clean_paper_id:
        return []

    row_limit = max(1, min(MAX_HISTORY_LIMIT, int(limit or 50)))
    scope_sql, scope_params = _history_scope_sql(user_id=user_id, username=clean_username)

    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    query,
                    answer,
                    paper_path,
                    model,
                    citations_json,
                    retrieval_stats_json,
                    cache_hit,
                    variation_mode,
                    request_id,
                    created_at
                FROM retrieval.chat_history_turns
                WHERE paper_id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                [clean_paper_id, clean_project_id, clean_project_id, *scope_params, row_limit],
            )
            rows = cur.fetchall()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        citations = _parse_json_list(row[4])
        stats = _parse_json_dict(row[5])
        out.append(
            {
                "query": str(row[0] or ""),
                "answer": str(row[1] or ""),
                "paper_path": str(row[2] or ""),
                "model": str(row[3] or ""),
                "citations": citations,
                "retrieval_stats": stats,
                "cache_hit": bool(row[6]) if row[6] is not None else None,
                "variation_mode": bool(row[7]),
                "request_id": str(row[8] or ""),
                "created_at": row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9] or ""),
            }
        )

    out.reverse()
    return out


def append_turn(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    session_id: Optional[str],
    paper_id: str,
    paper_path: str,
    model: Optional[str],
    variation_mode: bool,
    query: str,
    answer: str,
    citations: Optional[List[Dict[str, Any]]],
    retrieval_stats: Optional[Dict[str, Any]],
    cache_hit: Optional[bool],
    request_id: Optional[str],
) -> None:
    """Persist one chat turn for the authenticated user and selected paper."""
    clean_username = _clean_username(username)
    clean_project_id = str(project_id or "").strip()
    clean_persona_id = str(persona_id or "").strip()
    clean_paper_id = str(paper_id or "").strip()
    clean_query = str(query or "").strip()
    clean_answer = str(answer or "").strip()
    if not db_url or not clean_username or not clean_paper_id or not clean_query or not clean_answer:
        return

    citations_payload = citations if isinstance(citations, list) else []
    stats_payload = retrieval_stats if isinstance(retrieval_stats, dict) else {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO retrieval.chat_history_turns (
                    user_id,
                    username,
                    project_id,
                    persona_id,
                    session_id,
                    paper_id,
                    paper_path,
                    model,
                    variation_mode,
                    query,
                    answer,
                    citations_json,
                    retrieval_stats_json,
                    cache_hit,
                    request_id,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s, NOW()
                )
                """,
                (
                    int(user_id) if user_id is not None else None,
                    clean_username,
                    clean_project_id or None,
                    clean_persona_id or None,
                    str(session_id or "") or None,
                    clean_paper_id,
                    str(paper_path or ""),
                    str(model or "") or None,
                    bool(variation_mode),
                    clean_query,
                    clean_answer,
                    json.dumps(citations_payload, ensure_ascii=False),
                    json.dumps(stats_payload, ensure_ascii=False),
                    bool(cache_hit) if cache_hit is not None else None,
                    str(request_id or "") or None,
                ),
            )
            conn.commit()
    except Exception:
        return


def clear_turns(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    paper_id: str,
) -> int:
    """Delete chat history rows for one user+paper scope."""
    clean_username = _clean_username(username)
    clean_project_id = str(project_id or "").strip()
    clean_paper_id = str(paper_id or "").strip()
    if not db_url or not clean_username or not clean_paper_id:
        return 0

    scope_sql, scope_params = _history_scope_sql(user_id=user_id, username=clean_username)
    where_sql = (
        f"""
        FROM retrieval.chat_history_turns
        WHERE paper_id = %s
          AND (%s = '' OR COALESCE(project_id, '') = %s)
          AND {scope_sql}
        """
    )
    where_params = [clean_paper_id, clean_project_id, clean_project_id, *scope_params]
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) {where_sql}",
                where_params,
            )
            before = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                f"DELETE {where_sql}",
                where_params,
            )
            cur.execute(
                f"SELECT COUNT(*) {where_sql}",
                where_params,
            )
            after = int((cur.fetchone() or [0])[0] or 0)
            conn.commit()
            return max(0, before - after)
    except Exception:
        return 0
