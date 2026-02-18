"""User-scoped paper notes service for web PDF viewer."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import pooled_connection


def _clean_username(username: str) -> str:
    return str(username or "").strip()


def _scope_sql(*, user_id: Optional[int], username: str) -> tuple[str, List[Any]]:
    if user_id is not None:
        return (
            "(user_id = %s OR (user_id IS NULL AND lower(username) = lower(%s)))",
            [int(user_id), username],
        )
    return ("lower(username) = lower(%s)", [username])


def list_notes(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    paper_id: str,
    page_number: Optional[int] = None,
) -> List[Dict[str, Any]]:
    clean_user = _clean_username(username)
    clean_project = str(project_id or "").strip()
    clean_paper = str(paper_id or "").strip()
    if not db_url or not clean_user or not clean_paper:
        return []
    scope_sql, scope_params = _scope_sql(user_id=user_id, username=clean_user)
    params: List[Any] = [clean_paper, *scope_params]
    page_clause = ""
    if page_number is not None:
        page_clause = "AND COALESCE(page_number, -1) = %s"
        params.append(int(page_number))
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    id, paper_id, page_number, highlight_text, highlight_terms_json,
                    note_text, color, created_at, updated_at
                FROM retrieval.paper_notes
                WHERE paper_id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                  {page_clause}
                ORDER BY created_at DESC
                """,
                tuple([clean_paper, clean_project, clean_project, *scope_params, *([] if page_number is None else [int(page_number)])]),
            )
            rows = cur.fetchall()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        terms = row[4]
        if isinstance(terms, str):
            try:
                terms = json.loads(terms)
            except Exception:
                terms = []
        out.append(
            {
                "id": int(row[0]),
                "paper_id": str(row[1] or ""),
                "page_number": int(row[2]) if row[2] is not None else None,
                "highlight_text": str(row[3] or ""),
                "highlight_terms": terms if isinstance(terms, list) else [],
                "note_text": str(row[5] or ""),
                "color": str(row[6] or ""),
                "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7] or ""),
                "updated_at": row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8] or ""),
            }
        )
    return out


def create_note(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    paper_id: str,
    page_number: Optional[int],
    highlight_text: str,
    highlight_terms: Optional[List[str]],
    note_text: str,
    color: Optional[str] = None,
) -> Dict[str, Any]:
    clean_user = _clean_username(username)
    clean_project = str(project_id or "").strip()
    clean_paper = str(paper_id or "").strip()
    clean_note = str(note_text or "").strip()
    if not db_url or not clean_user or not clean_paper or not clean_note:
        return {}
    terms = [str(item or "").strip() for item in (highlight_terms or []) if str(item or "").strip()]
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO retrieval.paper_notes
                (
                    user_id, username, paper_id, page_number, highlight_text,
                    project_id,
                    highlight_terms_json, note_text, color, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (
                    int(user_id) if user_id is not None else None,
                    clean_user,
                    clean_paper,
                    int(page_number) if page_number is not None else None,
                    str(highlight_text or ""),
                    clean_project or None,
                    json.dumps(terms, ensure_ascii=False),
                    clean_note,
                    str(color or ""),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            note_id = int((row or [0])[0] or 0)
    except Exception:
        return {}
    notes = list_notes(db_url, user_id=user_id, username=clean_user, project_id=clean_project, paper_id=clean_paper)
    for item in notes:
        if int(item.get("id") or 0) == note_id:
            return item
    return {}


def update_note(
    db_url: str,
    *,
    note_id: int,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    note_text: Optional[str] = None,
    color: Optional[str] = None,
    highlight_text: Optional[str] = None,
    highlight_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    clean_user = _clean_username(username)
    clean_project = str(project_id or "").strip()
    if not db_url or not clean_user or int(note_id) <= 0:
        return {}
    scope_sql, scope_params = _scope_sql(user_id=user_id, username=clean_user)
    updates: List[str] = []
    params: List[Any] = []
    if note_text is not None:
        updates.append("note_text = %s")
        params.append(str(note_text or "").strip())
    if color is not None:
        updates.append("color = %s")
        params.append(str(color or ""))
    if highlight_text is not None:
        updates.append("highlight_text = %s")
        params.append(str(highlight_text or ""))
    if highlight_terms is not None:
        terms = [str(item or "").strip() for item in highlight_terms if str(item or "").strip()]
        updates.append("highlight_terms_json = %s::jsonb")
        params.append(json.dumps(terms, ensure_ascii=False))
    if not updates:
        return {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE retrieval.paper_notes
                SET {", ".join(updates)}, updated_at = NOW()
                WHERE id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                RETURNING paper_id
                """,
                tuple(params + [int(note_id), clean_project, clean_project, *scope_params]),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return {}
            paper_id = str(row[0] or "")
    except Exception:
        return {}
    notes = list_notes(db_url, user_id=user_id, username=clean_user, project_id=clean_project, paper_id=paper_id)
    for item in notes:
        if int(item.get("id") or 0) == int(note_id):
            return item
    return {}


def delete_note(
    db_url: str,
    *,
    note_id: int,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
) -> bool:
    clean_user = _clean_username(username)
    clean_project = str(project_id or "").strip()
    if not db_url or not clean_user or int(note_id) <= 0:
        return False
    scope_sql, scope_params = _scope_sql(user_id=user_id, username=clean_user)
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT 1
                FROM retrieval.paper_notes
                WHERE id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                LIMIT 1
                """,
                (int(note_id), clean_project, clean_project, *scope_params),
            )
            if not cur.fetchone():
                return False
            cur.execute(
                f"""
                DELETE FROM retrieval.paper_notes
                WHERE id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                """,
                (int(note_id), clean_project, clean_project, *scope_params),
            )
            check = conn.cursor()
            check.execute(
                f"""
                SELECT 1
                FROM retrieval.paper_notes
                WHERE id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                LIMIT 1
                """,
                (int(note_id), clean_project, clean_project, *scope_params),
            )
            deleted = 0 if check.fetchone() else 1
            conn.commit()
            return deleted > 0
    except Exception:
        return False
