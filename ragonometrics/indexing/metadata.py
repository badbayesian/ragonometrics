"""Postgres metadata schema and helpers for pipeline runs and index shards. Used by indexing and retrieval to store and resolve artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Optional

import psycopg2


def _safe_execute(cur, sql: str, params: tuple | None = None) -> bool:
    """Execute SQL and suppress backend-specific incompatibilities."""
    savepoint_name = "ragonometrics_safe_execute"
    has_savepoint = False
    try:
        cur.execute(f"SAVEPOINT {savepoint_name}")
        has_savepoint = True
    except Exception:
        has_savepoint = False
    try:
        cur.execute(sql, params or ())
        if has_savepoint:
            try:
                cur.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return True
    except Exception:
        if has_savepoint:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            except Exception:
                pass
            try:
                cur.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return False


def ensure_vector_extensions(cur) -> None:
    """Enable vector extensions when supported by the backend."""
    # vectorscale cascades to pgvector on supported images.
    if not _safe_execute(cur, "CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE"):
        # fallback to pgvector-only setups
        _safe_execute(cur, "CREATE EXTENSION IF NOT EXISTS vector")


def ensure_vector_indexes(cur) -> None:
    """Ensure ANN vector indexes exist when backend supports vector indexes."""
    if _safe_execute(
        cur,
        """
        CREATE INDEX IF NOT EXISTS vectors_embedding_diskann_idx
        ON indexing.vectors USING diskann (embedding vector_cosine_ops)
        """,
    ):
        return
    # Fallback for environments that have pgvector but not vectorscale.
    _safe_execute(
        cur,
        """
        CREATE INDEX IF NOT EXISTS vectors_embedding_ivfflat_idx
        ON indexing.vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
        """,
    )


def init_metadata_db(db_url: str):
    """Initialize metadata tables for pipeline runs and vectors.

    Args:
        db_url: Postgres database URL.

    Returns:
        connection: Open psycopg2 connection.
    """
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    ensure_vector_extensions(cur)

    _safe_execute(cur, "CREATE SCHEMA IF NOT EXISTS ingestion")
    _safe_execute(cur, "CREATE SCHEMA IF NOT EXISTS indexing")
    _safe_execute(cur, "CREATE SCHEMA IF NOT EXISTS observability")

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS ingestion.documents (
            doc_id TEXT PRIMARY KEY,
            path TEXT,
            title TEXT,
            author TEXT,
            extracted_at TIMESTAMPTZ,
            file_hash TEXT,
            text_hash TEXT
        )
        """,
    )
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS ingestion.paper_metadata (
            doc_id TEXT PRIMARY KEY REFERENCES ingestion.documents(doc_id) ON DELETE CASCADE,
            path TEXT,
            title TEXT,
            author TEXT,
            authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            primary_doi TEXT,
            dois_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            openalex_id TEXT,
            openalex_doi TEXT,
            publication_year INTEGER,
            venue TEXT,
            repec_handle TEXT,
            source_url TEXT,
            openalex_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            citec_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            extracted_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS paper_metadata_primary_doi_idx ON ingestion.paper_metadata (primary_doi)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS paper_metadata_path_idx ON ingestion.paper_metadata (path)")

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS indexing.pipeline_runs (
            id BIGSERIAL PRIMARY KEY,
            workflow_run_id TEXT,
            workstream_id TEXT,
            arm TEXT,
            paper_set_hash TEXT,
            index_build_reason TEXT,
            git_sha TEXT,
            extractor_version TEXT,
            embedding_model TEXT,
            chunk_words INTEGER,
            chunk_overlap INTEGER,
            normalized BOOLEAN,
            idempotency_key TEXT UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    _safe_execute(cur, "ALTER TABLE indexing.pipeline_runs ADD COLUMN IF NOT EXISTS workflow_run_id TEXT")
    _safe_execute(cur, "ALTER TABLE indexing.pipeline_runs ADD COLUMN IF NOT EXISTS workstream_id TEXT")
    _safe_execute(cur, "ALTER TABLE indexing.pipeline_runs ADD COLUMN IF NOT EXISTS arm TEXT")
    _safe_execute(cur, "ALTER TABLE indexing.pipeline_runs ADD COLUMN IF NOT EXISTS paper_set_hash TEXT")
    _safe_execute(cur, "ALTER TABLE indexing.pipeline_runs ADD COLUMN IF NOT EXISTS index_build_reason TEXT")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS indexing_pipeline_runs_workflow_run_idx ON indexing.pipeline_runs(workflow_run_id)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS indexing_pipeline_runs_workstream_idx ON indexing.pipeline_runs(workstream_id)")

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS indexing.index_versions (
            index_id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ,
            embedding_model TEXT,
            chunk_words INTEGER,
            chunk_overlap INTEGER,
            corpus_fingerprint TEXT,
            index_path TEXT,
            shard_path TEXT
        )
        """,
    )

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS indexing.index_shards (
            id BIGSERIAL PRIMARY KEY,
            shard_name TEXT UNIQUE,
            path TEXT,
            pipeline_run_id BIGINT REFERENCES indexing.pipeline_runs(id),
            index_id TEXT REFERENCES indexing.index_versions(index_id),
            created_at TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT FALSE
        )
        """,
    )

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS indexing.vectors (
            id BIGINT PRIMARY KEY,
            doc_id TEXT REFERENCES ingestion.documents(doc_id) ON DELETE CASCADE,
            chunk_id TEXT UNIQUE,
            chunk_hash TEXT,
            paper_path TEXT,
            page INTEGER,
            start_word INTEGER,
            end_word INTEGER,
            text TEXT,
            embedding VECTOR,
            pipeline_run_id BIGINT REFERENCES indexing.pipeline_runs(id),
            created_at TIMESTAMPTZ
        )
        """,
    )
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS indexing_vectors_doc_id_idx ON indexing.vectors(doc_id)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS indexing_vectors_pipeline_run_id_idx ON indexing.vectors(pipeline_run_id)")

    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS observability.request_failures (
            id BIGSERIAL PRIMARY KEY,
            component TEXT,
            error TEXT,
            context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    ensure_vector_indexes(cur)
    conn.commit()
    return conn


def upsert_paper_metadata(
    conn,
    *,
    doc_id: str,
    path: str,
    title: str,
    author: str,
    authors: list[str] | None = None,
    primary_doi: str | None = None,
    dois: list[str] | None = None,
    openalex_id: str | None = None,
    openalex_doi: str | None = None,
    publication_year: int | None = None,
    venue: str | None = None,
    repec_handle: str | None = None,
    source_url: str | None = None,
    openalex_json: dict[str, Any] | None = None,
    citec_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
    extracted_at: str | None = None,
) -> None:
    """Upsert one paper metadata row into `ingestion.paper_metadata`."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    extracted = extracted_at or now
    authors_payload = json.dumps(authors or [], ensure_ascii=False)
    dois_payload = json.dumps(dois or [], ensure_ascii=False)
    openalex_payload = json.dumps(openalex_json or {}, ensure_ascii=False)
    citec_payload = json.dumps(citec_json or {}, ensure_ascii=False)
    meta_payload = json.dumps(metadata_json or {}, ensure_ascii=False)

    cur.execute(
        """
        INSERT INTO ingestion.paper_metadata (
            doc_id, path, title, author, authors_json, primary_doi, dois_json,
            openalex_id, openalex_doi, publication_year, venue, repec_handle, source_url,
            openalex_json, citec_json, metadata_json, extracted_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
            %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s
        )
        ON CONFLICT (doc_id) DO UPDATE SET
            path = EXCLUDED.path,
            title = EXCLUDED.title,
            author = EXCLUDED.author,
            authors_json = EXCLUDED.authors_json,
            primary_doi = EXCLUDED.primary_doi,
            dois_json = EXCLUDED.dois_json,
            openalex_id = EXCLUDED.openalex_id,
            openalex_doi = EXCLUDED.openalex_doi,
            publication_year = EXCLUDED.publication_year,
            venue = EXCLUDED.venue,
            repec_handle = EXCLUDED.repec_handle,
            source_url = EXCLUDED.source_url,
            openalex_json = EXCLUDED.openalex_json,
            citec_json = EXCLUDED.citec_json,
            metadata_json = EXCLUDED.metadata_json,
            extracted_at = EXCLUDED.extracted_at,
            updated_at = EXCLUDED.updated_at
        """,
        (
            doc_id,
            path,
            title,
            author,
            authors_payload,
            primary_doi,
            dois_payload,
            openalex_id,
            openalex_doi,
            publication_year,
            venue,
            repec_handle,
            source_url,
            openalex_payload,
            citec_payload,
            meta_payload,
            extracted,
            now,
        ),
    )
    conn.commit()


def create_pipeline_run(
    conn,
    *,
    git_sha: Optional[str],
    extractor_version: Optional[str],
    embedding_model: str,
    chunk_words: int,
    chunk_overlap: int,
    normalized: bool,
    idempotency_key: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    paper_set_hash: Optional[str] = None,
    index_build_reason: Optional[str] = None,
) -> int:
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
    now = datetime.now(timezone.utc).isoformat()
    if idempotency_key:
        cur.execute(
            """
            INSERT INTO indexing.pipeline_runs
            (
                workflow_run_id, workstream_id, arm, paper_set_hash, index_build_reason,
                git_sha, extractor_version, embedding_model, chunk_words, chunk_overlap,
                normalized, created_at, idempotency_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO UPDATE SET
                workflow_run_id = COALESCE(indexing.pipeline_runs.workflow_run_id, EXCLUDED.workflow_run_id),
                workstream_id = COALESCE(indexing.pipeline_runs.workstream_id, EXCLUDED.workstream_id),
                arm = COALESCE(indexing.pipeline_runs.arm, EXCLUDED.arm),
                paper_set_hash = COALESCE(indexing.pipeline_runs.paper_set_hash, EXCLUDED.paper_set_hash),
                index_build_reason = COALESCE(indexing.pipeline_runs.index_build_reason, EXCLUDED.index_build_reason),
                idempotency_key = EXCLUDED.idempotency_key
            RETURNING id
            """,
            (
                workflow_run_id,
                workstream_id,
                arm,
                paper_set_hash,
                index_build_reason,
                git_sha,
                extractor_version,
                embedding_model,
                chunk_words,
                chunk_overlap,
                normalized,
                now,
                idempotency_key,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO indexing.pipeline_runs
            (
                workflow_run_id, workstream_id, arm, paper_set_hash, index_build_reason,
                git_sha, extractor_version, embedding_model, chunk_words, chunk_overlap,
                normalized, created_at, idempotency_key
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                workflow_run_id,
                workstream_id,
                arm,
                paper_set_hash,
                index_build_reason,
                git_sha,
                extractor_version,
                embedding_model,
                chunk_words,
                chunk_overlap,
                normalized,
                now,
                None,
            ),
        )
    row = cur.fetchone()
    run_id = int(row[0]) if row and row[0] is not None else None
    if run_id is None and idempotency_key:
        cur.execute(
            "SELECT id FROM indexing.pipeline_runs WHERE idempotency_key = %s LIMIT 1",
            (idempotency_key,),
        )
        row = cur.fetchone()
        run_id = int(row[0]) if row and row[0] is not None else None
    if run_id is None:
        cur.execute("SELECT id FROM indexing.pipeline_runs ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        run_id = int(row[0]) if row and row[0] is not None else 0
    conn.commit()
    return run_id


def publish_shard(conn, shard_name: str, path: str, pipeline_run_id: int, index_id: str | None = None) -> int:
    """Upsert an index shard and mark it active.

    Args:
        conn: Open database connection.
        shard_name: Unique shard name.
        path: Filesystem path to the shard.
        pipeline_run_id: Associated pipeline run id.
        index_id: Optional index version id.

    Returns:
        int: Shard id if available.
    """
    cur = conn.cursor()
    cur.execute("UPDATE indexing.index_shards SET is_active = FALSE WHERE is_active = TRUE")
    cur.execute(
        """
        INSERT INTO indexing.index_shards
        (shard_name, path, pipeline_run_id, created_at, is_active, index_id)
        VALUES (%s, %s, %s, %s, TRUE, %s)
        ON CONFLICT (shard_name) DO UPDATE SET
            path = EXCLUDED.path,
            pipeline_run_id = EXCLUDED.pipeline_run_id,
            created_at = EXCLUDED.created_at,
            is_active = TRUE,
            index_id = EXCLUDED.index_id
        RETURNING id
        """,
        (shard_name, path, pipeline_run_id, datetime.now(timezone.utc).isoformat(), index_id),
    )
    r = cur.fetchone()
    shard_id = int(r[0]) if r and r[0] is not None else None
    if shard_id is None:
        cur.execute(
            "SELECT id FROM indexing.index_shards WHERE shard_name = %s LIMIT 1",
            (shard_name,),
        )
        row = cur.fetchone()
        shard_id = int(row[0]) if row and row[0] is not None else 0
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
    cur.execute(
        "SELECT shard_name, path FROM indexing.index_shards WHERE is_active = TRUE ORDER BY created_at DESC"
    )
    return cur.fetchall()


def record_failure(conn, component: str, error: str, context: dict | None = None) -> None:
    """Record a failure for later replay/debug."""
    cur = conn.cursor()
    payload = json.dumps(context or {}, ensure_ascii=False)
    cur.execute(
        "INSERT INTO observability.request_failures (component, error, context_json, created_at) VALUES (%s, %s, %s::jsonb, %s)",
        (component, error, payload, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def create_index_version(
    conn,
    *,
    index_id: str,
    embedding_model: str,
    chunk_words: int,
    chunk_overlap: int,
    corpus_fingerprint: str,
    index_path: str,
    shard_path: str,
) -> str:
    """Insert an index version row and return the index id."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO indexing.index_versions (
            index_id, created_at, embedding_model, chunk_words, chunk_overlap,
            corpus_fingerprint, index_path, shard_path
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (index_id) DO NOTHING
        """,
        (
            index_id,
            datetime.now(timezone.utc).isoformat(),
            embedding_model,
            chunk_words,
            chunk_overlap,
            corpus_fingerprint,
            index_path,
            shard_path,
        ),
    )
    conn.commit()
    return index_id
