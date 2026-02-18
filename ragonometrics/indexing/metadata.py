"""Postgres metadata CRUD helpers for indexing and ingestion artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Optional

from ragonometrics.db.connection import connect, ensure_schema_ready


def init_metadata_db(db_url: str):
    """Open validated metadata DB connection.

    Args:
        db_url (str): Postgres connection URL.

    Returns:
        Any: Return value produced by the operation.
    """
    conn = connect(db_url, require_migrated=True)
    ensure_schema_ready(conn)
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
    """Upsert one paper metadata row into `ingestion.paper_metadata`.

    Args:
        conn (Any): Open database connection.
        doc_id (str): Input value for doc id.
        path (str): Filesystem path value.
        title (str): Paper title text.
        author (str): Author name text.
        authors (list[str] | None): List of author names.
        primary_doi (str | None): Input value for primary doi.
        dois (list[str] | None): Collection of dois.
        openalex_id (str | None): OpenAlex work identifier.
        openalex_doi (str | None): DOI reported by OpenAlex.
        publication_year (int | None): Publication year value.
        venue (str | None): Publication venue name.
        repec_handle (str | None): RePEc handle value.
        source_url (str | None): Source URL for external metadata.
        openalex_json (dict[str, Any] | None): Raw OpenAlex metadata payload.
        citec_json (dict[str, Any] | None): Raw CitEc metadata payload.
        metadata_json (dict[str, Any] | None): Raw merged metadata payload.
        extracted_at (str | None): ISO timestamp when metadata was extracted.
    """
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
        conn (Any): Open database connection.
        git_sha (Optional[str]): Input value for git sha.
        extractor_version (Optional[str]): Input value for extractor version.
        embedding_model (str): Embedding model name.
        chunk_words (int): Input value for chunk words.
        chunk_overlap (int): Input value for chunk overlap.
        normalized (bool): Whether to enable normalized.
        idempotency_key (Optional[str]): Deterministic key used for idempotent writes.
        workflow_run_id (Optional[str]): Workflow run identifier associated with this operation.
        workstream_id (Optional[str]): Logical workstream identifier for grouping related runs.
        arm (Optional[str]): Experiment arm label for this run.
        paper_set_hash (Optional[str]): Stable hash representing the selected paper set.
        index_build_reason (Optional[str]): Reason recorded for this index build.

    Returns:
        int: Computed integer result.
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
        conn (Any): Open database connection.
        shard_name (str): Input value for shard name.
        path (str): Filesystem path value.
        pipeline_run_id (int): Pipeline run identifier associated with this record.
        index_id (str | None): Identifier of the index record.

    Returns:
        int: Computed integer result.
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
        conn (Any): Open database connection.

    Returns:
        Any: Return value produced by the operation.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT shard_name, path FROM indexing.index_shards WHERE is_active = TRUE ORDER BY created_at DESC"
    )
    return cur.fetchall()


def record_failure(conn, component: str, error: str, context: dict | None = None) -> None:
    """Record a failure for later replay/debug.

    Args:
        conn (Any): Open database connection.
        component (str): Input value for component.
        error (str): Input value for error.
        context (dict | None): Mapping containing context.
    """
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
    """Insert an index version row and return the index id.

    Args:
        conn (Any): Open database connection.
        index_id (str): Identifier of the index record.
        embedding_model (str): Embedding model name.
        chunk_words (int): Input value for chunk words.
        chunk_overlap (int): Input value for chunk overlap.
        corpus_fingerprint (str): Input value for corpus fingerprint.
        index_path (str): Path to the vector index file.
        shard_path (str): Input value for shard path.

    Returns:
        str: Computed string result.
    """
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
