# Ragonomics Architecture: Decisions and Rationale

This document explains the architecture choices made for the Ragonomics RAG pipeline, design tradeoffs, and guidance for scaling.

Overview
--------
Ragonomics ingests PDFs, extracts text (per-page when possible), chunks text with provenance, embeds chunks, indexes embeddings in FAISS, and serves retrieval + LLM summarization via a Streamlit UI and background workers. DOI metadata is retrieved from Crossref (cached). The system is designed to be reproducible, auditable, and scalable to large document collections.

Components
----------
- PDF extraction: `pdftotext` (Poppler). We extract per-page text when `pdfinfo` is available — this supports provenance at page/word ranges.
- Preprocessing: normalization, trimming, and word-based chunking with overlap (`src/main.py`). Chunks include `page`, `start_word`, `end_word` metadata.
- Embeddings: OpenAI embeddings via `openai` SDK (`embed_texts`). Embeddings are normalized before indexing.
- Indexing: FAISS (`faiss-cpu`) index created via `src/indexer.py`. We use `IndexFlatIP` for cosine-similarity (inner-product) on normalized vectors for simplicity; the indexer supports incremental append by loading existing index and adding new vectors.
- Metadata: chunk metadata persisted in SQLite (`vectors_meta.sqlite`) for simplicity and portability. For large deployments, use Postgres or a dedicated metadata store.
- Queueing: Redis + RQ for background tasks; `src/rq_queue.py` enqueues indexing jobs and `rq-worker` runs them. This makes indexing scalable and distributable.
- Crossref: cached Crossref fetches with exponential backoff using `backoff` and cached in SQLite (`src/crossref_cache.py`) to avoid rate-limits.
- UI: `src/streamlit_app.py` provides interactive chat and DOI network visualization. Provenance information is surfaced with each retrieved chunk.
- Docker compose: compose file contains services for Streamlit, indexer, DOI worker, Redis, and RQ worker. Poppler is installed in the Docker image.
- Benchmarks: `tools/benchmark.py` measures indexing and chunking performance for small sample runs.

Design Rationale
----------------
- FAISS + SQLite metadata: Lightweight and widely used. FAISS handles vector retrieval efficiently; SQLite provides easy metadata storage and portability. For concurrency and scale, swap metadata to Postgres and use a durable FAISS store/backing.
- Per-page provenance: Essential for legal and scholarly use — allows traceability and citation. We extract and store `page` and word offsets with each chunk.
- Incremental indexing: Loading and appending to a FAISS index enables continuous indexing without re-creating the full index for every update. We conservatively use `IndexFlatIP`; for very large corpora, use `IndexIVFFlat` + training and sharding.
- Redis + RQ: Simple job queue that integrates with Python and Docker easily. Replaces ad-hoc one-off index runs with queued tasks and workers.
- Crossref caching + backoff: Minimizes rate-limit failures and redundant calls. Cache TTL is conservative (30 days) but configurable.
- Streamlit UI: Fast prototyping UI for interactive review, suitable for researchers. For production, build a more controlled frontend with RBAC.

Scaling & Operations
--------------------
- Sharding: shard FAISS by time or topic; keep metadata centralized in Postgres for queries.
- Concurrency: use RQ workers behind autoscaling group; ensure FAISS writes are serialized or use local worker shards.
- Persistence: move from SQLite to Postgres for metadata and to a cloud storage for FAISS indexes (S3 + EBS).
- Security: integrate secret manager for API keys, restrict network access to Redis and DB.
- Observability: add metrics for embeddings calls, index sizes, queue latencies, Crossref cache hits.

Benchmarks & Targets
--------------------
- Aim: indexing throughput (papers/hour), embedding latency (s/paper), retrieval latency (ms/query). Use `tools/benchmark.py` as a starting point and add more rigorous microbenchmarks with larger sample sets.

Operational Checklist
---------------------
- Ensure Poppler installed (Docker image includes it).
- Provide `OPENAI_API_KEY` via .env or secrets manager.
- For production: Postgres + persistent Redis + backup strategy for indexes.

Next Steps
----------
- Add Postgres metadata persistence and migration scripts.
- Add index sharding and a retriever service that loads appropriate shards.
- Harden Crossref caching to include ETag/conditional requests and cache invalidation policies.

Contact
-------
If you want, I can convert this into a `docs/` site, add diagrams (Mermaid + PNG), and create step-by-step operational runbooks.
