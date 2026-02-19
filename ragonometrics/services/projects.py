"""Project workspace tenancy helpers for web/CLI service layers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import pooled_connection

DEFAULT_PROJECT_ID = "default-shared"
DEFAULT_PROJECT_NAME = "Default Shared Project"
DEFAULT_PROJECT_SLUG = "default-shared"
DEFAULT_PERSONA_ID = "default-shared-persona"
DEFAULT_PERSONA_NAME = "Default Persona"


@dataclass(frozen=True)
class ProjectContext:
    """Resolved request tenancy context."""

    user_id: Optional[int]
    username: str
    project_id: str
    project_name: str
    role: str
    persona_id: str
    persona_name: str
    allow_cross_project_answer_reuse: bool
    allow_custom_question_sharing: bool


def _safe_username(value: Any) -> str:
    """Internal helper for safe username."""
    return str(value or "").strip()


def _slugify(value: str) -> str:
    """Internal helper for slugify."""
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    text = text.strip("-")
    return text or "project"


def _ensure_defaults(conn, *, user_id: Optional[int]) -> None:
    """Internal helper for ensure defaults."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO auth.projects (project_id, name, slug, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, TRUE, NOW(), NOW())
        ON CONFLICT (project_id) DO UPDATE SET
            name = EXCLUDED.name,
            slug = EXCLUDED.slug,
            is_active = TRUE,
            updated_at = NOW()
        """,
        (DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME, DEFAULT_PROJECT_SLUG),
    )
    cur.execute(
        """
        INSERT INTO auth.project_personas
        (
            persona_id, project_id, slug, name, system_prompt_suffix, default_model,
            retrieval_profile_json, is_default, is_active, created_at, updated_at
        )
        VALUES
        (
            %s, %s, 'default', %s, '', '', '{}'::jsonb, TRUE, TRUE, NOW(), NOW()
        )
        ON CONFLICT (persona_id) DO UPDATE SET
            project_id = EXCLUDED.project_id,
            name = EXCLUDED.name,
            is_default = TRUE,
            is_active = TRUE,
            updated_at = NOW()
        """,
        (DEFAULT_PERSONA_ID, DEFAULT_PROJECT_ID, DEFAULT_PERSONA_NAME),
    )
    cur.execute(
        """
        INSERT INTO auth.project_settings
        (
            project_id,
            allow_cross_project_answer_reuse,
            allow_custom_question_sharing,
            default_persona_id,
            updated_at
        )
        VALUES (%s, TRUE, FALSE, %s, NOW())
        ON CONFLICT (project_id) DO UPDATE SET
            default_persona_id = COALESCE(auth.project_settings.default_persona_id, EXCLUDED.default_persona_id),
            updated_at = NOW()
        """,
        (DEFAULT_PROJECT_ID, DEFAULT_PERSONA_ID),
    )
    if user_id is not None:
        cur.execute(
            """
            INSERT INTO auth.project_memberships
            (project_id, user_id, role, is_active, created_at, updated_at)
            VALUES (%s, %s, 'owner', TRUE, NOW(), NOW())
            ON CONFLICT (project_id, user_id) DO UPDATE SET
                is_active = TRUE,
                role = auth.project_memberships.role,
                updated_at = NOW()
            """,
            (DEFAULT_PROJECT_ID, int(user_id)),
        )


def _list_user_projects_conn(conn, *, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Internal helper for list user projects conn."""
    if user_id is None:
        return [
            {
                "project_id": DEFAULT_PROJECT_ID,
                "name": DEFAULT_PROJECT_NAME,
                "slug": DEFAULT_PROJECT_SLUG,
                "role": "owner",
                "is_active": True,
                "allow_cross_project_answer_reuse": True,
                "allow_custom_question_sharing": False,
                "default_persona_id": DEFAULT_PERSONA_ID,
            }
        ]
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            p.project_id,
            p.name,
            p.slug,
            p.is_active,
            pm.role,
            COALESCE(ps.allow_cross_project_answer_reuse, TRUE) AS allow_cross_project_answer_reuse,
            COALESCE(ps.allow_custom_question_sharing, FALSE) AS allow_custom_question_sharing,
            COALESCE(ps.default_persona_id, '') AS default_persona_id
        FROM auth.project_memberships pm
        JOIN auth.projects p
          ON p.project_id = pm.project_id
        LEFT JOIN auth.project_settings ps
          ON ps.project_id = p.project_id
        WHERE pm.user_id = %s
          AND pm.is_active = TRUE
          AND p.is_active = TRUE
        ORDER BY p.updated_at DESC, p.name ASC
        """,
        (int(user_id),),
    )
    rows = cur.fetchall()
    return [
        {
            "project_id": str(row[0] or ""),
            "name": str(row[1] or ""),
            "slug": str(row[2] or ""),
            "is_active": bool(row[3]),
            "role": str(row[4] or "viewer"),
            "allow_cross_project_answer_reuse": bool(row[5]),
            "allow_custom_question_sharing": bool(row[6]),
            "default_persona_id": str(row[7] or ""),
        }
        for row in rows
        if str(row[0] or "").strip()
    ]


def _list_project_personas_conn(conn, *, project_id: str) -> List[Dict[str, Any]]:
    """Internal helper for list project personas conn."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT persona_id, slug, name, is_default, is_active, default_model, system_prompt_suffix
        FROM auth.project_personas
        WHERE project_id = %s
          AND is_active = TRUE
        ORDER BY is_default DESC, updated_at DESC, name ASC
        """,
        (project_id,),
    )
    return [
        {
            "persona_id": str(row[0] or ""),
            "slug": str(row[1] or ""),
            "name": str(row[2] or ""),
            "is_default": bool(row[3]),
            "is_active": bool(row[4]),
            "default_model": str(row[5] or ""),
            "system_prompt_suffix": str(row[6] or ""),
        }
        for row in cur.fetchall()
        if str(row[0] or "").strip()
    ]


def list_user_projects(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
) -> List[Dict[str, Any]]:
    """List active projects available to one user."""
    if not db_url:
        return [
            {
                "project_id": DEFAULT_PROJECT_ID,
                "name": DEFAULT_PROJECT_NAME,
                "slug": DEFAULT_PROJECT_SLUG,
                "role": "owner",
                "is_active": True,
                "allow_cross_project_answer_reuse": True,
                "allow_custom_question_sharing": False,
                "default_persona_id": DEFAULT_PERSONA_ID,
            }
        ]
    clean_user = _safe_username(username)
    try:
        with pooled_connection(db_url) as conn:
            _ensure_defaults(conn, user_id=user_id)
            rows = _list_user_projects_conn(conn, user_id=user_id)
            conn.commit()
            if rows:
                return rows
    except Exception:
        pass
    return [
        {
            "project_id": DEFAULT_PROJECT_ID,
            "name": DEFAULT_PROJECT_NAME,
            "slug": DEFAULT_PROJECT_SLUG,
            "role": "owner" if clean_user else "viewer",
            "is_active": True,
            "allow_cross_project_answer_reuse": True,
            "allow_custom_question_sharing": False,
            "default_persona_id": DEFAULT_PERSONA_ID,
        }
    ]


def get_project_context(
    db_url: str,
    *,
    session_id: str,
    user_id: Optional[int],
    username: str,
    requested_project_id: Optional[str] = None,
) -> ProjectContext:
    """Resolve project/persona context for one session request."""
    clean_username = _safe_username(username)
    requested = str(requested_project_id or "").strip()
    fallback = ProjectContext(
        user_id=user_id,
        username=clean_username,
        project_id=DEFAULT_PROJECT_ID,
        project_name=DEFAULT_PROJECT_NAME,
        role="owner" if user_id is not None else "viewer",
        persona_id=DEFAULT_PERSONA_ID,
        persona_name=DEFAULT_PERSONA_NAME,
        allow_cross_project_answer_reuse=True,
        allow_custom_question_sharing=False,
    )
    if not db_url:
        return fallback
    sid = str(session_id or "").strip()
    try:
        with pooled_connection(db_url) as conn:
            _ensure_defaults(conn, user_id=user_id)
            projects = _list_user_projects_conn(conn, user_id=user_id)
            if not projects:
                conn.commit()
                return fallback
            project_ids = {str(row.get("project_id") or ""): row for row in projects}
            selected_project_id = ""
            selected_persona_id = ""
            if sid:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COALESCE(current_project_id, ''), COALESCE(current_persona_id, '')
                    FROM auth.streamlit_sessions
                    WHERE session_id = %s
                    LIMIT 1
                    """,
                    (sid,),
                )
                row = cur.fetchone()
                if row:
                    selected_project_id = str(row[0] or "")
                    selected_persona_id = str(row[1] or "")
            if requested and requested in project_ids:
                selected_project_id = requested
            if selected_project_id not in project_ids:
                selected_project_id = str(projects[0].get("project_id") or DEFAULT_PROJECT_ID)
            selected_project = project_ids.get(selected_project_id) or projects[0]
            personas = _list_project_personas_conn(conn, project_id=selected_project_id)
            persona_ids = {str(row.get("persona_id") or ""): row for row in personas}
            if not selected_persona_id or selected_persona_id not in persona_ids:
                default_persona = str(selected_project.get("default_persona_id") or "")
                if default_persona in persona_ids:
                    selected_persona_id = default_persona
                elif personas:
                    selected_persona_id = str(personas[0].get("persona_id") or "")
                else:
                    selected_persona_id = DEFAULT_PERSONA_ID
            selected_persona = persona_ids.get(selected_persona_id) or {
                "persona_id": DEFAULT_PERSONA_ID,
                "name": DEFAULT_PERSONA_NAME,
            }
            if sid:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE auth.streamlit_sessions
                    SET current_project_id = %s,
                        current_persona_id = %s,
                        updated_at = NOW()
                    WHERE session_id = %s
                    """,
                    (selected_project_id, selected_persona_id, sid),
                )
            conn.commit()
            return ProjectContext(
                user_id=user_id,
                username=clean_username,
                project_id=selected_project_id,
                project_name=str(selected_project.get("name") or selected_project_id),
                role=str(selected_project.get("role") or "viewer"),
                persona_id=selected_persona_id,
                persona_name=str(selected_persona.get("name") or selected_persona_id),
                allow_cross_project_answer_reuse=bool(
                    selected_project.get("allow_cross_project_answer_reuse")
                ),
                allow_custom_question_sharing=bool(selected_project.get("allow_custom_question_sharing")),
            )
    except Exception:
        return fallback


def create_project(
    db_url: str,
    *,
    name: str,
    created_by_user_id: Optional[int],
    created_by_username: str,
) -> Dict[str, Any]:
    """Create one project and owner membership."""
    clean_name = str(name or "").strip()
    if not db_url:
        raise ValueError("database_unavailable")
    if len(clean_name) < 2:
        raise ValueError("Project name must be at least 2 characters.")
    owner_id = int(created_by_user_id) if created_by_user_id is not None else None
    slug_base = _slugify(clean_name)[:48]
    if not slug_base:
        slug_base = "project"
    created_by = _safe_username(created_by_username) or "user"
    try:
        with pooled_connection(db_url) as conn:
            _ensure_defaults(conn, user_id=owner_id)
            cur = conn.cursor()
            slug = slug_base
            project_id = slug
            suffix = 1
            while True:
                cur.execute("SELECT 1 FROM auth.projects WHERE slug = %s LIMIT 1", (slug,))
                if not cur.fetchone():
                    break
                suffix += 1
                slug = f"{slug_base}-{suffix}"
                project_id = slug
            cur.execute(
                """
                INSERT INTO auth.projects
                (project_id, name, slug, is_active, created_by_user_id, created_at, updated_at)
                VALUES (%s, %s, %s, TRUE, %s, NOW(), NOW())
                """,
                (project_id, clean_name, slug, owner_id),
            )
            if owner_id is not None:
                cur.execute(
                    """
                    INSERT INTO auth.project_memberships
                    (project_id, user_id, role, is_active, created_at, updated_at)
                    VALUES (%s, %s, 'owner', TRUE, NOW(), NOW())
                    ON CONFLICT (project_id, user_id) DO UPDATE SET
                        role = 'owner',
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (project_id, owner_id),
                )
            persona_id = f"{project_id}-persona-default"
            cur.execute(
                """
                INSERT INTO auth.project_personas
                (
                    persona_id, project_id, slug, name, system_prompt_suffix, default_model,
                    retrieval_profile_json, is_default, is_active, created_at, updated_at
                )
                VALUES
                (%s, %s, 'default', 'Default Persona', '', '', '{}'::jsonb, TRUE, TRUE, NOW(), NOW())
                ON CONFLICT (persona_id) DO NOTHING
                """,
                (persona_id, project_id),
            )
            cur.execute(
                """
                INSERT INTO auth.project_settings
                (
                    project_id,
                    allow_cross_project_answer_reuse,
                    allow_custom_question_sharing,
                    default_persona_id,
                    updated_at
                )
                VALUES (%s, TRUE, FALSE, %s, NOW())
                ON CONFLICT (project_id) DO UPDATE SET
                    updated_at = NOW()
                """,
                (project_id, persona_id),
            )
            conn.commit()
    except Exception as exc:
        raise ValueError(f"project_create_failed: {exc}") from exc
    return {
        "project_id": project_id,
        "name": clean_name,
        "slug": slug,
        "created_by_user_id": owner_id,
        "created_by_username": created_by,
    }


def add_project_member(
    db_url: str,
    *,
    project_id: str,
    identifier: str,
    role: str,
) -> Dict[str, Any]:
    """Add or update one project membership by username/email identifier."""
    wanted_project = str(project_id or "").strip()
    wanted_identifier = str(identifier or "").strip()
    wanted_role = str(role or "viewer").strip().lower()
    if wanted_role not in {"owner", "editor", "viewer"}:
        raise ValueError("role must be one of owner|editor|viewer.")
    if not db_url:
        raise ValueError("database_unavailable")
    if not wanted_project or not wanted_identifier:
        raise ValueError("project_id and identifier are required.")
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM auth.projects WHERE project_id = %s LIMIT 1", (wanted_project,))
            if not cur.fetchone():
                raise ValueError("project_not_found")
            cur.execute(
                """
                SELECT id, username, email
                FROM auth.streamlit_users
                WHERE lower(username) = lower(%s)
                   OR lower(COALESCE(email, '')) = lower(%s)
                LIMIT 1
                """,
                (wanted_identifier, wanted_identifier),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("user_not_found")
            user_id = int(row[0])
            username = str(row[1] or "")
            email = str(row[2] or "")
            cur.execute(
                """
                INSERT INTO auth.project_memberships
                (project_id, user_id, role, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, TRUE, NOW(), NOW())
                ON CONFLICT (project_id, user_id) DO UPDATE SET
                    role = EXCLUDED.role,
                    is_active = TRUE,
                    updated_at = NOW()
                """,
                (wanted_project, user_id, wanted_role),
            )
            conn.commit()
            return {
                "project_id": wanted_project,
                "user_id": user_id,
                "username": username,
                "email": email,
                "role": wanted_role,
            }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"project_member_add_failed: {exc}") from exc


def list_project_papers(
    db_url: str,
    *,
    project_id: str,
) -> List[Dict[str, Any]]:
    """List allowlisted paper mappings for a project."""
    wanted_project = str(project_id or "").strip()
    if not db_url or not wanted_project:
        return []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT paper_id, paper_path, added_by_user_id, created_at
                FROM auth.project_papers
                WHERE project_id = %s
                ORDER BY created_at DESC, paper_id ASC
                """,
                (wanted_project,),
            )
            return [
                {
                    "paper_id": str(row[0] or ""),
                    "paper_path": str(row[1] or ""),
                    "added_by_user_id": int(row[2]) if row[2] is not None else None,
                    "created_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3] or ""),
                }
                for row in cur.fetchall()
                if str(row[0] or "").strip()
            ]
    except Exception:
        return []


def add_project_paper(
    db_url: str,
    *,
    project_id: str,
    paper_id: str,
    paper_path: str,
    added_by_user_id: Optional[int],
) -> Dict[str, Any]:
    """Allowlist one paper for one project."""
    wanted_project = str(project_id or "").strip()
    wanted_paper_id = str(paper_id or "").strip()
    wanted_path = str(paper_path or "").strip()
    if not db_url:
        raise ValueError("database_unavailable")
    if not wanted_project or not wanted_paper_id or not wanted_path:
        raise ValueError("project_id, paper_id, and paper_path are required.")
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO auth.project_papers
                (project_id, paper_id, paper_path, added_by_user_id, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (project_id, paper_id) DO UPDATE SET
                    paper_path = EXCLUDED.paper_path,
                    added_by_user_id = EXCLUDED.added_by_user_id
                """,
                (
                    wanted_project,
                    wanted_paper_id,
                    wanted_path,
                    int(added_by_user_id) if added_by_user_id is not None else None,
                ),
            )
            conn.commit()
            return {"project_id": wanted_project, "paper_id": wanted_paper_id, "paper_path": wanted_path}
    except Exception as exc:
        raise ValueError(f"project_paper_add_failed: {exc}") from exc


def project_paper_ids(
    db_url: str,
    *,
    project_id: str,
) -> List[str]:
    """Return paper ids mapped to one project."""
    return [str(row.get("paper_id") or "") for row in list_project_papers(db_url, project_id=project_id)]


def update_project_settings(
    db_url: str,
    *,
    project_id: str,
    allow_cross_project_answer_reuse: Optional[bool] = None,
    allow_custom_question_sharing: Optional[bool] = None,
) -> Dict[str, Any]:
    """Patch project settings toggles."""
    wanted_project = str(project_id or "").strip()
    if not db_url:
        raise ValueError("database_unavailable")
    if not wanted_project:
        raise ValueError("project_id is required.")
    updates: List[str] = []
    params: List[Any] = []
    if allow_cross_project_answer_reuse is not None:
        updates.append("allow_cross_project_answer_reuse = %s")
        params.append(bool(allow_cross_project_answer_reuse))
    if allow_custom_question_sharing is not None:
        updates.append("allow_custom_question_sharing = %s")
        params.append(bool(allow_custom_question_sharing))
    if not updates:
        raise ValueError("No settings provided.")
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO auth.project_settings
                (project_id, allow_cross_project_answer_reuse, allow_custom_question_sharing, updated_at)
                VALUES (%s, TRUE, FALSE, NOW())
                ON CONFLICT (project_id) DO NOTHING
                """,
                (wanted_project,),
            )
            cur.execute(
                f"""
                UPDATE auth.project_settings
                SET {", ".join(updates)}, updated_at = NOW()
                WHERE project_id = %s
                """,
                (*params, wanted_project),
            )
            cur.execute(
                """
                SELECT
                    project_id,
                    allow_cross_project_answer_reuse,
                    allow_custom_question_sharing,
                    default_persona_id,
                    updated_at
                FROM auth.project_settings
                WHERE project_id = %s
                LIMIT 1
                """,
                (wanted_project,),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                raise ValueError("project_not_found")
            return {
                "project_id": str(row[0] or ""),
                "allow_cross_project_answer_reuse": bool(row[1]),
                "allow_custom_question_sharing": bool(row[2]),
                "default_persona_id": str(row[3] or ""),
                "updated_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4] or ""),
            }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"project_settings_update_failed: {exc}") from exc


def list_project_personas(
    db_url: str,
    *,
    project_id: str,
) -> List[Dict[str, Any]]:
    """List active personas for one project."""
    wanted_project = str(project_id or "").strip()
    if not db_url or not wanted_project:
        return []
    try:
        with pooled_connection(db_url) as conn:
            rows = _list_project_personas_conn(conn, project_id=wanted_project)
            conn.commit()
            return rows
    except Exception:
        return []


def select_project(
    db_url: str,
    *,
    session_id: str,
    user_id: Optional[int],
    username: str,
    project_id: str,
) -> ProjectContext:
    """Select one project for the current session context."""
    desired = str(project_id or "").strip()
    projects = list_user_projects(db_url, user_id=user_id, username=username)
    if desired and desired not in {str(row.get("project_id") or "") for row in projects}:
        raise ValueError("project_not_found")
    return get_project_context(
        db_url,
        session_id=session_id,
        user_id=user_id,
        username=username,
        requested_project_id=desired or None,
    )


def select_persona(
    db_url: str,
    *,
    session_id: str,
    user_id: Optional[int],
    username: str,
    project_id: str,
    persona_id: str,
) -> ProjectContext:
    """Select one persona for current session context."""
    wanted_project = str(project_id or "").strip()
    wanted_persona = str(persona_id or "").strip()
    if not db_url:
        raise ValueError("database_unavailable")
    if not wanted_project or not wanted_persona:
        raise ValueError("project_id and persona_id are required.")
    context = select_project(
        db_url,
        session_id=session_id,
        user_id=user_id,
        username=username,
        project_id=wanted_project,
    )
    personas = list_project_personas(db_url, project_id=context.project_id)
    persona_ids = {str(item.get("persona_id") or ""): item for item in personas}
    if wanted_persona not in persona_ids:
        raise ValueError("persona_not_found")
    sid = str(session_id or "").strip()
    if sid:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE auth.streamlit_sessions
                SET current_project_id = %s,
                    current_persona_id = %s,
                    updated_at = NOW()
                WHERE session_id = %s
                """,
                (context.project_id, wanted_persona, sid),
            )
            conn.commit()
    selected = persona_ids[wanted_persona]
    return ProjectContext(
        user_id=context.user_id,
        username=context.username,
        project_id=context.project_id,
        project_name=context.project_name,
        role=context.role,
        persona_id=wanted_persona,
        persona_name=str(selected.get("name") or wanted_persona),
        allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
        allow_custom_question_sharing=context.allow_custom_question_sharing,
    )

