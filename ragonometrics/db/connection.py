"""Shared Postgres connection and pooling utilities."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg import connect as pg_connect
from psycopg_pool import ConnectionPool


EXPECTED_ALEMBIC_REVISION = "0004"
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
        revision = str(revision_row[0]).strip()
        if expected_revision and revision != expected_revision:
            raise RuntimeError(
                f"Database schema is outdated (found {revision}, expected {expected_revision}). "
                "Run: `ragonometrics db migrate --db-url <DATABASE_URL>`"
            )
    _SCHEMA_READY_BY_DSN.add(dsn)


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
