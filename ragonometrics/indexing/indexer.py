"""Index builder for embeddings and Postgres metadata. Writes DB vector rows, ANN indexes, and FAISS compatibility artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import faiss
import numpy as np
from openai import OpenAI

from ragonometrics.core.main import (
    Paper,
    Settings,
    embed_texts,
    load_papers,
    load_settings,
    prepare_chunks_for_paper,
)
import os
import psycopg2
from datetime import datetime
import hashlib
import uuid
from . import metadata
from ragonometrics.indexing.manifest import build_index_version, build_run_manifest, write_index_version_sidecar, write_run_manifest
from ragonometrics.core.logging_utils import log_event


def normalize(v: np.ndarray) -> np.ndarray:
    """L2-normalize a 2D array of vectors.

    Args:
        v: Array of vectors with shape (n, d).

    Returns:
        np.ndarray: Normalized vectors with the same shape.
    """
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return v / norms


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8", errors="ignore"))


def _stable_chunk_id(doc_id: str, page: int | None, start_word: int | None, end_word: int | None, chunk_hash: str) -> str:
    payload = f"{doc_id}|{page}|{start_word}|{end_word}|{chunk_hash}"
    return _sha256_text(payload)


def _to_pgvector_literal(values: np.ndarray) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values.tolist()) + "]"


def build_index(
    settings: Settings,
    paper_paths: List[Path],
    index_path: Path = Path("vectors.index"),
    meta_db_url: str | None = None,
):
    """Build vector indexes from paper paths and persist metadata to Postgres.

    Args:
        settings: Runtime settings for chunking and embeddings.
        paper_paths: PDF paths to index.
        index_path: Output index file path.
        meta_db_url: Optional Postgres URL for metadata storage.

    Raises:
        RuntimeError: If no metadata DB URL is available or index dims mismatch.
    """
    client = OpenAI()
    log_event("index_start", {"papers": len(paper_paths), "index_path": str(index_path)})

    paper_paths = sorted(paper_paths, key=lambda p: str(p).lower())
    all_vectors: List[np.ndarray] = []
    metadata_rows = []
    next_id = 0
    doc_ids: List[str] = []
    paper_manifests: List[dict] = []

    # If index exists, load it and determine starting id
    if index_path.exists():
        try:
            index = faiss.read_index(str(index_path))
            existing_n = index.ntotal
            next_id = int(existing_n)
            print(f"Existing index found with {existing_n} vectors; new vectors will start at id {next_id}.")
        except Exception:
            index = None
            next_id = 0
    else:
        index = None

    for path in paper_paths:
        papers = load_papers([path])
        if not papers:
            continue
        paper = papers[0]
        # compute doc_id (sha256 of file bytes + text) to support dedup/versioning
        try:
            with open(path, "rb") as fh:
                file_bytes = fh.read()
        except Exception:
            file_bytes = b""
        text_hash = _sha256_text(paper.text)
        file_hash = _sha256_bytes(file_bytes)
        doc_id = _sha256_text(f"{file_hash}:{text_hash}")
        doc_ids.append(doc_id)
        chunks = prepare_chunks_for_paper(paper, settings)
        if not chunks:
            continue

        texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
        embeddings = embed_texts(client, texts, settings.embedding_model, settings.batch_size)

        vecs = np.array(embeddings, dtype=np.float32)
        vecs = normalize(vecs)

        paper_chunk_manifest = []
        for i, c in enumerate(chunks):
            chunk_text = c["text"] if isinstance(c, dict) else str(c)
            page = c["page"] if isinstance(c, dict) else None
            start_word = c["start_word"] if isinstance(c, dict) else None
            end_word = c["end_word"] if isinstance(c, dict) else None
            chunk_hash = _sha256_text(chunk_text)
            chunk_id = _stable_chunk_id(doc_id, page, start_word, end_word, chunk_hash)
            embedding_literal = _to_pgvector_literal(vecs[i])
            metadata_rows.append(
                (
                    next_id,
                    doc_id,
                    chunk_id,
                    chunk_hash,
                    str(path),
                    page,
                    start_word,
                    end_word,
                    chunk_text,
                    embedding_literal,
                    None,  # pipeline_run_id placeholder; set later
                    datetime.utcnow().isoformat(),
                )
            )
            paper_chunk_manifest.append(
                {
                    "chunk_id": chunk_id,
                    "chunk_hash": chunk_hash,
                    "page": page,
                    "start_word": start_word,
                    "end_word": end_word,
                }
            )
            next_id += 1

        paper_manifests.append(
            {
                "path": str(path),
                "title": paper.title,
                "author": paper.author,
                "doc_id": doc_id,
                "file_sha256": file_hash,
                "text_sha256": text_hash,
                "chunk_count": len(paper_chunk_manifest),
                "chunks": paper_chunk_manifest,
            }
        )

        all_vectors.append(vecs)

    if not all_vectors:
        print("No vectors to index.")
        return

    X = np.vstack(all_vectors).astype(np.float32)
    dim = X.shape[1]
    embeddings_sha256 = _sha256_bytes(X.tobytes())
    if index is None:
        index = faiss.IndexFlatIP(dim)
        index.add(X)
    else:
        # ensure dims match
        if index.d != dim:
            raise RuntimeError(f"Index dim {index.d} != embeddings dim {dim}")
        index.add(X)
    faiss.write_index(index, str(index_path))

    # store metadata in Postgres
    db_url = meta_db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("No meta DB URL provided and DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    # ensure metadata tables exist
    metadata.init_metadata_db(db_url)
    # idempotency key for run
    corpus_fingerprint = hashlib.sha256("|".join(sorted(doc_ids)).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{settings.embedding_model}|{settings.chunk_words}|{settings.chunk_overlap}|{corpus_fingerprint}".encode("utf-8")
    ).hexdigest()
    # create a pipeline run (or reuse existing)
    run_conn = psycopg2.connect(db_url)
    run_id = metadata.create_pipeline_run(
        run_conn,
        git_sha=None,
        extractor_version=None,
        embedding_model=settings.embedding_model,
        chunk_words=settings.chunk_words,
        chunk_overlap=settings.chunk_overlap,
        normalized=True,
        idempotency_key=idempotency_key,
    )
    run_conn.close()
    if os.environ.get("INDEX_IDEMPOTENT_SKIP", "1") == "1":
        # if a run with same idempotency key already exists and vectors are present, skip
        try:
            cur.execute("SELECT 1 FROM vectors WHERE pipeline_run_id = %s LIMIT 1", (run_id,))
            if cur.fetchone():
                log_event("index_skip", {"reason": "idempotent", "run_id": run_id})
                conn.close()
                return
        except Exception:
            pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vectors (
            id BIGINT PRIMARY KEY,
            doc_id TEXT,
            chunk_id TEXT,
            chunk_hash TEXT,
            paper_path TEXT,
            page INTEGER,
            start_word INTEGER,
            end_word INTEGER,
            text TEXT,
            embedding VECTOR,
            pipeline_run_id INTEGER,
            created_at TEXT
        )
        """
    )
    for paper_meta in paper_manifests:
        cur.execute(
            """
            INSERT INTO documents (doc_id, path, title, author, extracted_at, file_hash, text_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (doc_id) DO UPDATE SET
                path = EXCLUDED.path,
                title = EXCLUDED.title,
                author = EXCLUDED.author,
                extracted_at = EXCLUDED.extracted_at,
                file_hash = EXCLUDED.file_hash,
                text_hash = EXCLUDED.text_hash
            """,
            (
                paper_meta["doc_id"],
                paper_meta["path"],
                paper_meta["title"],
                paper_meta["author"],
                datetime.utcnow().isoformat(),
                paper_meta["file_sha256"],
                paper_meta["text_sha256"],
            ),
        )
    # Upsert rows
    # upsert rows and attach pipeline_run_id
    for row in metadata_rows:
        id_, doc_id, chunk_id, chunk_hash, paper_path, page, start_word, end_word, text, embedding, _, created_at = row
        cur.execute(
            """
            INSERT INTO vectors (
                id, doc_id, chunk_id, chunk_hash, paper_path, page, start_word, end_word, text, embedding, pipeline_run_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                chunk_id = EXCLUDED.chunk_id,
                chunk_hash = EXCLUDED.chunk_hash,
                paper_path = EXCLUDED.paper_path,
                page = EXCLUDED.page,
                start_word = EXCLUDED.start_word,
                end_word = EXCLUDED.end_word,
                text = EXCLUDED.text,
                embedding = EXCLUDED.embedding,
                pipeline_run_id = EXCLUDED.pipeline_run_id,
                created_at = EXCLUDED.created_at
            """,
            (id_, doc_id, chunk_id, chunk_hash, paper_path, page, start_word, end_word, text, embedding, run_id, created_at),
        )
    conn.commit()
    conn.close()

    # write a versioned/indexed shard file (immutable artifact)
    import shutil

    with open(str(index_path), "rb") as f:
        raw = f.read()
    index_sha256 = _sha256_bytes(raw)
    h = index_sha256[:12]
    shards_dir = Path("indexes")
    shards_dir.mkdir(exist_ok=True)
    shard_name = f"vectors-{h}.index"
    shard_path = shards_dir / shard_name
    # copy index to shard path (immutable)
    shutil.copy2(str(index_path), str(shard_path))

    # build index version metadata
    index_id = str(uuid.uuid4())
    # register index version + shard in metadata and atomically mark active
    reg_conn = psycopg2.connect(db_url)
    metadata.create_index_version(
        reg_conn,
        index_id=index_id,
        embedding_model=settings.embedding_model,
        chunk_words=settings.chunk_words,
        chunk_overlap=settings.chunk_overlap,
        corpus_fingerprint=corpus_fingerprint,
        index_path=str(index_path),
        shard_path=str(shard_path),
    )
    metadata.publish_shard(reg_conn, shard_name, str(shard_path), run_id, index_id=index_id)
    reg_conn.close()

    # write index version sidecar next to shard
    index_version_payload = build_index_version(
        index_id=index_id,
        embedding_model=settings.embedding_model,
        chunk_words=settings.chunk_words,
        chunk_overlap=settings.chunk_overlap,
        corpus_fingerprint=corpus_fingerprint,
        embedding_dim=dim,
    )
    write_index_version_sidecar(shard_path, index_version_payload)

    manifest = build_run_manifest(
        settings=settings,
        paper_paths=paper_paths,
        index_path=index_path,
        shard_path=shard_path,
        pipeline_run_id=run_id,
        corpus_fingerprint=corpus_fingerprint,
        embedding_dim=dim,
        index_sha256=index_sha256,
        embeddings_sha256=embeddings_sha256,
        paper_manifest=paper_manifests,
    )
    write_run_manifest(shard_path, manifest)

    print(f"Wrote index ({X.shape[0]} vectors, dim={dim}) to {index_path}")
    print(f"Wrote metadata rows to {db_url}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--papers-dir", type=str, default=None)
    parser.add_argument("--index-path", type=str, default="vectors.index")
    parser.add_argument("--meta-db-url", type=str, default=None)
    parser.add_argument("--limit", type=int, default=0, help="Limit number of papers to index (0 = all)")
    args = parser.parse_args()

    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    pdfs = sorted(papers_dir.glob("*.pdf"))
    if args.limit > 0:
        pdfs = pdfs[: args.limit]

    build_index(settings, pdfs, index_path=Path(args.index_path), meta_db_url=args.meta_db_url)

