"""Postgres query cache for question answers."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ragonometrics.db.connection import connect

# Kept for call-site compatibility; runtime persistence now uses Postgres.
DEFAULT_CACHE_PATH = Path("postgres_query_cache")


def _database_url() -> str:
    """Database url.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for query cache persistence.")
    return db_url


def _connect(_db_path: Path):
    """Connect.

    Args:
        _db_path (Path): Path to the local SQLite state database.

    Returns:
        Any: Return value produced by the operation.
    """
    return connect(_database_url(), require_migrated=True)


def make_cache_key(query: str, paper_path: str, model: str, context: str) -> str:
    """Make cache key.

    Args:
        query (str): Input query text.
        paper_path (str): Path to a single paper file.
        model (str): Model name used for this operation.
        context (str): Input value for context.

    Returns:
        str: Computed string result.
    """
    payload = f"{paper_path}||{model}||{query}||{hashlib.sha256(context.encode('utf-8')).hexdigest()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_query_for_cache(query: str) -> str:
    """Build a normalized query key for broader fallback cache lookups."""
    text = str(query or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def paper_fingerprint(paper_path: str) -> str:
    """Build one deterministic paper fingerprint for cache guardrails."""
    normalized = str(paper_path or "").replace("\\", "/").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def profile_hash(value: str) -> str:
    """Build one deterministic profile hash for prompt/retrieval/persona identity."""
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _is_nonempty_answer(value: Any) -> bool:
    """Internal helper for is nonempty answer."""
    return bool(str(value or "").strip())


def _legacy_upsert_sql_params(
    *,
    cache_key: str,
    query: str,
    paper_path: str,
    model: str,
    context: str,
    answer: str,
) -> tuple[str, tuple[Any, ...]]:
    """Internal helper for legacy upsert sql params."""
    sql = """
        INSERT INTO retrieval.query_cache
        (cache_key, query, query_normalized, paper_path, model, context_hash, answer, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (cache_key) DO UPDATE SET
            query = EXCLUDED.query,
            query_normalized = EXCLUDED.query_normalized,
            paper_path = EXCLUDED.paper_path,
            model = EXCLUDED.model,
            context_hash = EXCLUDED.context_hash,
            answer = EXCLUDED.answer,
            created_at = EXCLUDED.created_at
    """
    params = (
        cache_key,
        query,
        normalize_query_for_cache(query),
        paper_path,
        model,
        hashlib.sha256(context.encode("utf-8")).hexdigest(),
        answer,
    )
    return sql, params


def _guardrail_where_sql() -> str:
    """Internal helper for guardrail where sql."""
    return (
        "COALESCE(paper_fingerprint, '') = %s "
        "AND COALESCE(prompt_profile_hash, '') = %s "
        "AND COALESCE(retrieval_profile_hash, '') = %s "
        "AND COALESCE(persona_profile_hash, '') = %s"
    )


def get_cached_answer_hybrid(
    db_path: Path,
    *,
    cache_key: str,
    query: str,
    paper_path: str,
    model: str,
    project_id: Optional[str],
    prompt_profile_hash: str,
    retrieval_profile_hash: str,
    persona_profile_hash: str,
    allow_cross_project_answer_reuse: bool,
    variation_mode: bool,
    has_history: bool,
    validate_answer: Optional[Callable[[Any], bool]] = None,
) -> Dict[str, Any]:
    """Read cache with project-local first, then guarded shared fallback."""
    validator = validate_answer or _is_nonempty_answer
    normalized_query = normalize_query_for_cache(query)
    normalized_path = str(paper_path or "").replace("\\", "/").strip()
    scoped_project = str(project_id or "").strip()
    fp = paper_fingerprint(normalized_path)

    payload: Dict[str, Any] = {
        "answer": None,
        "cache_hit": False,
        "cache_scope": "fresh",
        "cache_hit_layer": "none",
        "cache_miss_reason": "strict_and_normalized_miss",
    }
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if scoped_project:
            try:
                cur.execute(
                    """
                    SELECT answer
                    FROM retrieval.project_query_cache
                    WHERE project_id = %s
                      AND cache_key = %s
                    LIMIT 1
                    """,
                    (scoped_project, cache_key),
                )
                row = cur.fetchone()
                if row and validator(row[0]):
                    payload.update(
                        {
                            "answer": str(row[0]).strip(),
                            "cache_hit": True,
                            "cache_scope": "project",
                            "cache_hit_layer": "strict",
                            "cache_miss_reason": "",
                        }
                    )
                    return payload
                cur.execute(
                    """
                    SELECT answer
                    FROM retrieval.project_query_cache
                    WHERE project_id = %s
                      AND query_normalized = %s
                      AND paper_path = %s
                      AND model = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (scoped_project, normalized_query, normalized_path, model),
                )
                row = cur.fetchone()
                if row and validator(row[0]):
                    payload.update(
                        {
                            "answer": str(row[0]).strip(),
                            "cache_hit": True,
                            "cache_scope": "project",
                            "cache_hit_layer": "fallback",
                            "cache_miss_reason": "",
                        }
                    )
                    return payload
            except Exception:
                # Project cache is additive; proceed to shared lookup path.
                pass
        if variation_mode:
            payload["cache_miss_reason"] = "variation_mode_bypass"
            return payload
        if has_history:
            payload["cache_miss_reason"] = "conversation_history_bypass_shared"
            return payload
        if not allow_cross_project_answer_reuse:
            payload["cache_miss_reason"] = "policy_opt_out"
            return payload

        guard_params = (fp, prompt_profile_hash, retrieval_profile_hash, persona_profile_hash)
        cur.execute(
            f"""
            SELECT answer
            FROM retrieval.query_cache
            WHERE cache_key = %s
              AND COALESCE(share_eligible, TRUE) = TRUE
              AND {_guardrail_where_sql()}
            LIMIT 1
            """,
            (cache_key, *guard_params),
        )
        row = cur.fetchone()
        if row and validator(row[0]):
            payload.update(
                {
                    "answer": str(row[0]).strip(),
                    "cache_hit": True,
                    "cache_scope": "shared",
                    "cache_hit_layer": "strict",
                    "cache_miss_reason": "",
                }
            )
            return payload
        cur.execute(
            f"""
            SELECT answer
            FROM retrieval.query_cache
            WHERE query_normalized = %s
              AND paper_path = %s
              AND model = %s
              AND COALESCE(share_eligible, TRUE) = TRUE
              AND {_guardrail_where_sql()}
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (normalized_query, normalized_path, model, *guard_params),
        )
        row = cur.fetchone()
        if row and validator(row[0]):
            payload.update(
                {
                    "answer": str(row[0]).strip(),
                    "cache_hit": True,
                    "cache_scope": "shared",
                    "cache_hit_layer": "fallback",
                    "cache_miss_reason": "",
                }
            )
            return payload
        payload["cache_miss_reason"] = "guardrail_miss"
        return payload
    finally:
        conn.close()


def _is_canonical_share_question(query: str) -> bool:
    """Internal helper for is canonical share question."""
    normalized = normalize_query_for_cache(query)
    if not normalized:
        return False
    starter = {
        normalize_query_for_cache("What is the main research question of this paper?"),
        normalize_query_for_cache("What identification strategy does the paper use?"),
        normalize_query_for_cache("What dataset and sample period are used?"),
        normalize_query_for_cache("What are the key quantitative findings?"),
        normalize_query_for_cache("What are the main limitations and caveats?"),
        normalize_query_for_cache("What policy implications follow from the results?"),
    }
    if normalized in starter:
        return True
    try:
        from ragonometrics.services import structured as structured_service  # Local import to avoid cycles.

        structured_norm = {
            normalize_query_for_cache(str(item.get("question") or ""))
            for item in structured_service.structured_report_questions()
        }
        return normalized in structured_norm
    except Exception:
        return False


def set_cached_answer_hybrid(
    db_path: Path,
    *,
    cache_key: str,
    query: str,
    paper_path: str,
    model: str,
    context: str,
    answer: str,
    project_id: Optional[str],
    user_id: Optional[int],
    source_project_id: Optional[str],
    prompt_profile_hash: str,
    retrieval_profile_hash: str,
    persona_profile_hash: str,
    allow_custom_question_sharing: bool,
) -> None:
    """Write project cache and optionally publish to guarded shared cache."""
    normalized_query = normalize_query_for_cache(query)
    normalized_path = str(paper_path or "").replace("\\", "/").strip()
    context_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    fp = paper_fingerprint(normalized_path)
    scoped_project = str(project_id or "").strip()
    share_eligible = _is_canonical_share_question(query) or bool(allow_custom_question_sharing)

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if scoped_project:
            try:
                project_cache_key = hashlib.sha256(f"{scoped_project}||{cache_key}".encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO retrieval.project_query_cache
                    (
                        project_cache_key, project_id, cache_key, query, query_normalized, paper_path, model,
                        context_hash, answer, paper_fingerprint, prompt_profile_hash,
                        retrieval_profile_hash, persona_profile_hash, persona_hash, safety_flags_json, created_at
                    )
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, NOW())
                    ON CONFLICT (project_cache_key) DO UPDATE SET
                        query = EXCLUDED.query,
                        query_normalized = EXCLUDED.query_normalized,
                        paper_path = EXCLUDED.paper_path,
                        model = EXCLUDED.model,
                        context_hash = EXCLUDED.context_hash,
                        answer = EXCLUDED.answer,
                        paper_fingerprint = EXCLUDED.paper_fingerprint,
                        prompt_profile_hash = EXCLUDED.prompt_profile_hash,
                        retrieval_profile_hash = EXCLUDED.retrieval_profile_hash,
                        persona_profile_hash = EXCLUDED.persona_profile_hash,
                        persona_hash = EXCLUDED.persona_hash,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        project_cache_key,
                        scoped_project,
                        cache_key,
                        query,
                        normalized_query,
                        normalized_path,
                        model,
                        context_hash,
                        answer,
                        fp,
                        prompt_profile_hash,
                        retrieval_profile_hash,
                        persona_profile_hash,
                        persona_profile_hash,
                    ),
                )
            except Exception:
                # Project cache table may be absent in legacy test snapshots.
                pass

        try:
            cur.execute(
                """
                INSERT INTO retrieval.query_cache
                (
                    cache_key, query, query_normalized, paper_path, model, context_hash, answer,
                    paper_fingerprint, prompt_profile_hash, retrieval_profile_hash, persona_profile_hash,
                    share_eligible, source_project_id, source_user_id, safety_flags_json, created_at
                )
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, NOW())
                ON CONFLICT (cache_key) DO UPDATE SET
                    query = EXCLUDED.query,
                    query_normalized = EXCLUDED.query_normalized,
                    paper_path = EXCLUDED.paper_path,
                    model = EXCLUDED.model,
                    context_hash = EXCLUDED.context_hash,
                    answer = EXCLUDED.answer,
                    paper_fingerprint = EXCLUDED.paper_fingerprint,
                    prompt_profile_hash = EXCLUDED.prompt_profile_hash,
                    retrieval_profile_hash = EXCLUDED.retrieval_profile_hash,
                    persona_profile_hash = EXCLUDED.persona_profile_hash,
                    share_eligible = EXCLUDED.share_eligible,
                    source_project_id = EXCLUDED.source_project_id,
                    source_user_id = EXCLUDED.source_user_id,
                    created_at = EXCLUDED.created_at
                """,
                (
                    cache_key,
                    query,
                    normalized_query,
                    normalized_path,
                    model,
                    context_hash,
                    answer,
                    fp,
                    prompt_profile_hash,
                    retrieval_profile_hash,
                    persona_profile_hash,
                    bool(share_eligible),
                    str(source_project_id or "").strip() or None,
                    int(user_id) if user_id is not None else None,
                ),
            )
        except Exception:
            # Backward compatibility for environments without hybrid columns.
            sql, params = _legacy_upsert_sql_params(
                cache_key=cache_key,
                query=query,
                paper_path=normalized_path,
                model=model,
                context=context,
                answer=answer,
            )
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def get_cached_answer(db_path: Path, cache_key: str) -> Optional[str]:
    """Get cached answer.

    Args:
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.

    Returns:
        Optional[str]: Computed result, or `None` when unavailable.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT answer FROM retrieval.query_cache WHERE cache_key = %s", (cache_key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_cached_answer_by_normalized_query(
    db_path: Path,
    *,
    query: str,
    paper_path: str,
    model: str,
) -> Optional[str]:
    """Fetch most recent cached answer by normalized query + paper + model."""
    normalized_query = normalize_query_for_cache(query)
    if not normalized_query:
        return None
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT answer
            FROM retrieval.query_cache
            WHERE query_normalized = %s
              AND paper_path = %s
              AND model = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (normalized_query, paper_path, model),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_cached_answer(
    db_path: Path,
    *,
    cache_key: str,
    query: str,
    paper_path: str,
    model: str,
    context: str,
    answer: str,
) -> None:
    """Set cached answer.

    Args:
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.
        query (str): Input query text.
        paper_path (str): Path to a single paper file.
        model (str): Model name used for this operation.
        context (str): Input value for context.
        answer (str): Input value for answer.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO retrieval.query_cache
            (cache_key, query, query_normalized, paper_path, model, context_hash, answer, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (cache_key) DO UPDATE SET
                query = EXCLUDED.query,
                query_normalized = EXCLUDED.query_normalized,
                paper_path = EXCLUDED.paper_path,
                model = EXCLUDED.model,
                context_hash = EXCLUDED.context_hash,
                answer = EXCLUDED.answer,
                created_at = EXCLUDED.created_at
            """,
            (
                cache_key,
                query,
                normalize_query_for_cache(query),
                paper_path,
                model,
                hashlib.sha256(context.encode("utf-8")).hexdigest(),
                answer,
            ),
        )
        conn.commit()
    finally:
        conn.close()

