"""Shared Postgres connection and pooling utilities."""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Iterator, Optional, Tuple

from psycopg import Connection
from psycopg import connect as pg_connect
from psycopg_pool import ConnectionPool


EXPECTED_ALEMBIC_REVISION = "0015"
_LEGACY_ALEMBIC_ALIASES = {
    "0001_unified_schema": "0001",
    "0002_migrate_workflow_legacy": "0002",
    "0003_async_jobs": "0003",
    "0003_add_async_jobs_queue": "0003",
    "0004_run_record_idempotency_usage": "0004",
    "0005_drop_legacy_openalex_cache": "0005",
    "0006_streamlit_auth_tables": "0006",
    "0007_web_rate_limits": "0007",
    "0008_web_chat_history": "0008",
    "0009_web_ux_auth_upgrades": "0009",
    "0010_paper_comparisons": "0010",
    "0011_openalex_citation_graph_cache": "0011",
    "0012_projects_core": "0012",
    "0013_project_scope_existing_tables": "0013",
    "0014_hybrid_query_cache": "0014",
    "0015_multi_paper_chat": "0015",
}
_POOL_LOCK = threading.Lock()
_POOLS: dict[str, ConnectionPool] = {}
_SCHEMA_READY_BY_DSN: set[str] = set()


def get_database_url(explicit_db_url: str | None = None, *, required: bool = True) -> str | None:
    """Resolve Postgres DSN from explicit value or environment."""
    value = (explicit_db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not value and required:
        raise RuntimeError("DATABASE_URL is required.")
    return value or None


def ensure_schema_ready(conn: Connection, *, expected_revision: str = EXPECTED_ALEMBIC_REVISION) -> None:
    """Fail fast if Alembic migrations were not applied."""
    dsn = str(conn.info.dsn or "")
    if dsn in _SCHEMA_READY_BY_DSN:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.alembic_version')")
        row = cur.fetchone()
        if not row or row[0] is None:
            raise RuntimeError(
                "Database schema is not initialized. Run: "
                "`ragonometrics db migrate --db-url <DATABASE_URL>`"
            )
        cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
        revision_row = cur.fetchone()
        if not revision_row or not revision_row[0]:
            raise RuntimeError(
                "Alembic revision is missing. Run: "
                "`ragonometrics db migrate --db-url <DATABASE_URL>`"
            )
        revision = normalize_alembic_revision(str(revision_row[0]).strip())
        expected = normalize_alembic_revision(expected_revision)
        if expected and revision != expected:
            raise RuntimeError(
                f"Database schema is outdated (found {revision}, expected {expected}). "
                "Run: `ragonometrics db migrate --db-url <DATABASE_URL>`"
            )
    _SCHEMA_READY_BY_DSN.add(dsn)


def normalize_alembic_revision(revision: str | None) -> str:
    """Normalize historical Alembic revision ids to canonical short ids.

    Args:
        revision (str | None): Raw revision text from ``alembic_version``.

    Returns:
        str: Canonical revision id when resolvable.
    """
    text = str(revision or "").strip()
    if not text:
        return ""
    if text in _LEGACY_ALEMBIC_ALIASES:
        return _LEGACY_ALEMBIC_ALIASES[text]
    match = re.match(r"^(000\d+)_", text)
    if match:
        return match.group(1)
    return text


def normalize_alembic_version_marker(conn: Connection) -> Tuple[Optional[str], Optional[str], bool]:
    """Normalize the stored ``alembic_version`` marker in-place when needed.

    Args:
        conn (Connection): Open DB connection.

    Returns:
        Tuple[Optional[str], Optional[str], bool]: ``(raw, normalized, changed)``.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.alembic_version')")
        row = cur.fetchone()
        if not row or row[0] is None:
            return None, None, False
        cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
        revision_row = cur.fetchone()
        if not revision_row or not revision_row[0]:
            return None, None, False
        raw = str(revision_row[0]).strip()
        normalized = normalize_alembic_revision(raw)
        if normalized and normalized != raw:
            cur.execute("UPDATE alembic_version SET version_num = %s WHERE version_num = %s", (normalized, raw))
            return raw, normalized, True
        return raw, normalized, False


def connect(
    db_url: str | None = None,
    *,
    autocommit: bool = False,
    require_migrated: bool = True,
) -> Connection:
    """Open a psycopg3 connection."""
    resolved = get_database_url(db_url, required=True)
    conn = pg_connect(resolved, autocommit=autocommit)
    if require_migrated:
        ensure_schema_ready(conn)
    return conn


def get_pool(
    db_url: str | None = None,
    *,
    min_size: int | None = None,
    max_size: int | None = None,
) -> ConnectionPool:
    """Get or create a process-global connection pool for a DSN."""
    resolved = get_database_url(db_url, required=True)
    min_value = int(min_size if min_size is not None else os.environ.get("DB_POOL_MIN_SIZE", "1"))
    max_value = int(max_size if max_size is not None else os.environ.get("DB_POOL_MAX_SIZE", "8"))
    with _POOL_LOCK:
        pool = _POOLS.get(resolved)
        if pool is None:
            pool = ConnectionPool(
                conninfo=resolved,
                min_size=max(1, min_value),
                max_size=max(1, max_value),
                kwargs={"autocommit": False},
                open=True,
            )
            _POOLS[resolved] = pool
    return pool


@contextmanager
def pooled_connection(
    db_url: str | None = None,
    *,
    require_migrated: bool = True,
) -> Iterator[Connection]:
    """Yield one pooled connection."""
    pool = get_pool(db_url)
    with pool.connection() as conn:
        if require_migrated:
            ensure_schema_ready(conn)
        yield conn


def close_all_pools() -> None:
    """Close all process-global pools."""
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        try:
            pool.close()
        except Exception:
            pass
