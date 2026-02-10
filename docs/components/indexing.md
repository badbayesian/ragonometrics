# Indexing and Retrieval

Indexing and Retrieval
----------------------
- Primary ANN backend is Postgres vector search (`pgvector` + `pgvectorscale` `diskann`) over `vectors.embedding`.
- FAISS index artifacts are still written to [`vectors.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors.index) and versioned in [`indexes/`](https://github.com/badbayesian/ragonometrics/tree/main/indexes) as a compatibility fallback.
- Metadata, vector text, and embeddings are stored in Postgres (requires `DATABASE_URL`).
- Each index build writes a manifest next to the shard with config hash, corpus fingerprint, stable doc/chunk ids, and embedding/index hashes.

DOI Network
-----------
- [`build_doi_network_from_paper()`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/core/main.py) extracts DOIs from text and queries Crossref.
- Optional persistence in Postgres with [`build_and_store_doi_network()`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/core/main.py).

Queueing
--------
- [`ragonometrics/integrations/rq_queue.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/rq_queue.py) enqueues indexing jobs.
- Use Redis + RQ worker for async indexing.
