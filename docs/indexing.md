# Indexing and Retrieval

Indexing and Retrieval
----------------------
- FAISS index uses `IndexFlatIP` with normalized vectors for cosine similarity.
- Index files are written to `vectors.index` and versioned in `indexes/`.
- Metadata and vector text are stored in Postgres (requires `DATABASE_URL`).
- Each index build writes a manifest next to the shard with config hash, corpus fingerprint, stable doc/chunk ids, and embedding/index hashes.

DOI Network
-----------
- `build_doi_network_from_paper()` extracts DOIs from text and queries Crossref.
- Optional persistence in Postgres with `build_and_store_doi_network()`.

Queueing
--------
- `rq_queue.py` enqueues indexing jobs.
- Use Redis + RQ worker for async indexing.
