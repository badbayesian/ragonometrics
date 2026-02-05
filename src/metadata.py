from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import psycopg2


def init_metadata_db(db_url: str):
    """Initialize metadata tables for pipeline runs and vectors.

    Args:
        db_url: Postgres database URL.

    Returns:
        connection: Open psycopg2 connection.
    """
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # pipeline runs (audit manifest for a build)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id SERIAL PRIMARY KEY,
            git_sha TEXT,
            extractor_version TEXT,
            embedding_model TEXT,
            chunk_words INTEGER,
            chunk_overlap INTEGER,
            normalized BOOLEAN,
            created_at TIMESTAMP
        )
        """
    )

    # index shards manifest
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS index_shards (
            id SERIAL PRIMARY KEY,
            shard_name TEXT UNIQUE,
            path TEXT,
            pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
            created_at TIMESTAMP,
            is_active BOOLEAN DEFAULT FALSE
        )
        """
    )

    # vectors metadata (per-chunk)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vectors (
            id BIGINT PRIMARY KEY,
            doc_id TEXT,
            paper_path TEXT,
            page INTEGER,
            start_word INTEGER,
            end_word INTEGER,
            text TEXT,
            pipeline_run_id INTEGER REFERENCES pipeline_runs(id),
            created_at TIMESTAMP
        )
        """
    )

    # simple documents table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            path TEXT,
            title TEXT,
            author TEXT,
            extracted_at TIMESTAMP
        )
        """
    )

    conn.commit()
    return conn


def create_pipeline_run(conn, *, git_sha: Optional[str], extractor_version: Optional[str], embedding_model: str, chunk_words: int, chunk_overlap: int, normalized: bool) -> int:
    """Create a pipeline run record and return its id.

    Args:
        conn: Open database connection.
        git_sha: Optional git SHA for the run.
        extractor_version: Optional extractor version string.
        embedding_model: Embedding model name.
        chunk_words: Chunk size in words.
        chunk_overlap: Overlap size in words.
        normalized: Whether embeddings were normalized.

    Returns:
        int: Pipeline run id if available.
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pipeline_runs (git_sha, extractor_version, embedding_model, chunk_words, chunk_overlap, normalized, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (git_sha, extractor_version, embedding_model, chunk_words, chunk_overlap, normalized, datetime.utcnow()),
    )
    # attempt to find the inserted id
    try:
        # prefer explicit id, fall back to rowid for sqlite
        cur.execute("SELECT COALESCE(id, rowid) FROM pipeline_runs ORDER BY rowid DESC LIMIT 1")
        row = cur.fetchone()
        run_id = row[0] if row else None
    except Exception:
        run_id = None
    conn.commit()
    return run_id


def publish_shard(conn, shard_name: str, path: str, pipeline_run_id: int) -> int:
    """Upsert an index shard and mark it active.

    Args:
        conn: Open database connection.
        shard_name: Unique shard name.
        path: Filesystem path to the shard.
        pipeline_run_id: Associated pipeline run id.

    Returns:
        int: Shard id if available.
    """
    cur = conn.cursor()
    # deactivate any previous active shard(s)
    cur.execute("UPDATE index_shards SET is_active = FALSE WHERE is_active = TRUE")
    # upsert shard (Postgres-style ON CONFLICT used in production). For sqlite testing, attempt insert then select.
    try:
        cur.execute(
            "INSERT INTO index_shards (shard_name, path, pipeline_run_id, created_at, is_active) VALUES (%s, %s, %s, %s, TRUE) ON CONFLICT (shard_name) DO UPDATE SET path = EXCLUDED.path, pipeline_run_id = EXCLUDED.pipeline_run_id, created_at = EXCLUDED.created_at, is_active = TRUE",
            (shard_name, path, pipeline_run_id, datetime.utcnow()),
        )
    except Exception:
        # fallback for sqlite: try simple insert or replace
        try:
            cur.execute(
                "REPLACE INTO index_shards (shard_name, path, pipeline_run_id, created_at, is_active) VALUES (%s, %s, %s, %s, 1)",
                (shard_name, path, pipeline_run_id, datetime.utcnow()),
            )
        except Exception:
            pass
    # find the shard id
    try:
        cur.execute("SELECT COALESCE(id, rowid) FROM index_shards WHERE shard_name = %s LIMIT 1", (shard_name,))
        r = cur.fetchone()
        shard_id = r[0] if r else None
    except Exception:
        shard_id = None
    conn.commit()
    return shard_id


def get_active_shards(conn):
    """Fetch active index shards ordered by creation time.

    Args:
        conn: Open database connection.

    Returns:
        list[tuple[str, str]]: (shard_name, path) rows.
    """
    cur = conn.cursor()
    cur.execute("SELECT shard_name, path FROM index_shards WHERE is_active = TRUE ORDER BY created_at DESC")
    return cur.fetchall()
