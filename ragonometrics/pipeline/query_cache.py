"""Postgres query cache for question answers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

import psycopg2

# Kept for call-site compatibility; runtime persistence now uses Postgres.
DEFAULT_CACHE_PATH = Path("postgres_query_cache")


def _database_url() -> str:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for query cache persistence.")
    return db_url


def _connect(_db_path: Path) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS retrieval")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS retrieval.query_cache (
            cache_key TEXT PRIMARY KEY,
            query TEXT,
            paper_path TEXT,
            model TEXT,
            context_hash TEXT,
            answer TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS retrieval_query_cache_created_at_idx
        ON retrieval.query_cache(created_at DESC)
        """
    )
    conn.commit()
    return conn


def make_cache_key(query: str, paper_path: str, model: str, context: str) -> str:
    payload = f"{paper_path}||{model}||{query}||{hashlib.sha256(context.encode('utf-8')).hexdigest()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached_answer(db_path: Path, cache_key: str) -> Optional[str]:
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

