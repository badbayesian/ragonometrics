# Indexing and Retrieval

Indexing and Retrieval
----------------------
- Primary ANN backend is Postgres vector search (`pgvector` + `pgvectorscale` `diskann`) over `vectors.embedding`.
- FAISS index artifacts are written to [`vectors-3072.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors-3072.index) by default and versioned in [`indexes/`](https://github.com/badbayesian/ragonometrics/tree/main/indexes). Legacy [`vectors.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors.index) can be retained for older 1536-dim runs.
- Metadata, vector text, and embeddings are stored in Postgres (requires `DATABASE_URL`).
- Each index build writes a manifest next to the shard with config hash, corpus fingerprint, stable doc/chunk ids, and embedding/index hashes.

Queueing
--------
- [`ragonometrics/integrations/rq_queue.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/rq_queue.py) enqueues indexing jobs.
- Use Redis + RQ worker for async indexing.
