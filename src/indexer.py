from __future__ import annotations

import json
from pathlib import Path
from typing import List

import faiss
import numpy as np
from openai import OpenAI

from ragonomics.main import (
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
from . import metadata


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


def build_index(
    settings: Settings,
    paper_paths: List[Path],
    index_path: Path = Path("vectors.index"),
    meta_db_url: str | None = None,
):
    """Build a FAISS index from paper paths and persist metadata to Postgres.

    Args:
        settings: Runtime settings for chunking and embeddings.
        paper_paths: PDF paths to index.
        index_path: Output index file path.
        meta_db_url: Optional Postgres URL for metadata storage.

    Raises:
        RuntimeError: If no metadata DB URL is available or index dims mismatch.
    """
    client = OpenAI()

    all_vectors: List[np.ndarray] = []
    metadata_rows = []
    next_id = 0

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
        text_hash = hashlib.sha256(paper.text.encode("utf-8", errors="ignore")).hexdigest()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        doc_id = hashlib.sha256((file_hash + text_hash).encode("utf-8")).hexdigest()
        chunks = prepare_chunks_for_paper(paper, settings)
        if not chunks:
            continue

        texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
        embeddings = embed_texts(client, texts, settings.embedding_model, settings.batch_size)

        vecs = np.array(embeddings, dtype=np.float32)
        vecs = normalize(vecs)

        for i, c in enumerate(chunks):
            metadata_rows.append(
                (
                    next_id,
                    doc_id,
                    str(path),
                    c["page"] if isinstance(c, dict) else None,
                    c["start_word"] if isinstance(c, dict) else None,
                    c["end_word"] if isinstance(c, dict) else None,
                    c["text"] if isinstance(c, dict) else str(c),
                    None,  # pipeline_run_id placeholder; set later
                    datetime.utcnow().isoformat(),
                )
            )
            next_id += 1

        all_vectors.append(vecs)

    if not all_vectors:
        print("No vectors to index.")
        return

    X = np.vstack(all_vectors).astype(np.float32)
    dim = X.shape[1]
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
    # create a pipeline run
    run_conn = psycopg2.connect(db_url)
    run_id = metadata.create_pipeline_run(run_conn, git_sha=None, extractor_version=None, embedding_model=settings.embedding_model, chunk_words=settings.chunk_words, chunk_overlap=settings.chunk_overlap, normalized=True)
    run_conn.close()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vectors (
            id BIGINT PRIMARY KEY,
            paper_path TEXT,
            page INTEGER,
            start_word INTEGER,
            end_word INTEGER,
            text TEXT,
            created_at TEXT
        )
        """
    )
    # Upsert rows
    # upsert rows and attach pipeline_run_id
    for row in metadata_rows:
        id_, doc_id, paper_path, page, start_word, end_word, text, _, created_at = row
        cur.execute(
            """
            INSERT INTO vectors (id, doc_id, paper_path, page, start_word, end_word, text, pipeline_run_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                paper_path = EXCLUDED.paper_path,
                page = EXCLUDED.page,
                start_word = EXCLUDED.start_word,
                end_word = EXCLUDED.end_word,
                text = EXCLUDED.text,
                pipeline_run_id = EXCLUDED.pipeline_run_id,
                created_at = EXCLUDED.created_at
            """,
            (id_, doc_id, paper_path, page, start_word, end_word, text, run_id, created_at),
        )
    conn.commit()
    conn.close()

    # write a versioned/indexed shard file (immutable artifact)
    import shutil

    with open(str(index_path), "rb") as f:
        raw = f.read()
    h = hashlib.sha256(raw).hexdigest()[:12]
    shards_dir = Path("indexes")
    shards_dir.mkdir(exist_ok=True)
    shard_name = f"vectors-{h}.index"
    shard_path = shards_dir / shard_name
    # copy index to shard path (immutable)
    shutil.copy2(str(index_path), str(shard_path))

    # register shard in metadata and atomically mark active
    reg_conn = psycopg2.connect(db_url)
    metadata.publish_shard(reg_conn, shard_name, str(shard_path), run_id)
    reg_conn.close()

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
