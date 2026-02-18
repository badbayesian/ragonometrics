"""Postgres query cache for question answers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

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
            (cache_key, query, paper_path, model, context_hash, answer, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (cache_key) DO UPDATE SET
                query = EXCLUDED.query,
                paper_path = EXCLUDED.paper_path,
                model = EXCLUDED.model,
                context_hash = EXCLUDED.context_hash,
                answer = EXCLUDED.answer,
                created_at = EXCLUDED.created_at
            """,
            (
                cache_key,
                query,
                paper_path,
                model,
                hashlib.sha256(context.encode("utf-8")).hexdigest(),
                answer,
            ),
        )
        conn.commit()
    finally:
        conn.close()

