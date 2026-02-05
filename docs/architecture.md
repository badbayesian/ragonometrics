# Ragonomics Architecture: Decisions and Rationale

This document summarizes the current Ragonomics architecture, design tradeoffs, and operational guidance.

Overview
--------
Ragonomics ingests PDFs, extracts per-page text for provenance, chunks with overlap, embeds chunks, indexes embeddings in FAISS, and serves retrieval + LLM summaries via CLI and a Streamlit UI. DOI metadata can be retrieved from Crossref and cached. The system is designed to be reproducible, auditable, and scalable from local runs to a Postgres-backed deployment.

Key Components
--------------
- Config and prompts
  - `config.toml` (optional) is the primary configuration surface with env-var overrides.
  - Centralized prompts live in `src/prompts.py`.
- PDF extraction and preprocessing
  - `pdftotext` + `pdfinfo` (Poppler) with OCR fallback.
  - Per-page extraction supports provenance (page + word offsets).
  - Optional section-aware chunking (title/abstract/introduction/methods/results) via `SECTION_AWARE_CHUNKING`.
- Embeddings and retrieval
  - OpenAI embeddings via `embed_texts`.
  - Hybrid BM25 + FAISS retrieval when `DATABASE_URL` is configured.
  - Optional query expansion (`QUERY_EXPANSION`) and LLM reranking (`RERANKER_MODEL`, `RERANK_TOP_N`).
- Indexing
  - FAISS `IndexFlatIP` with normalized vectors.
  - Index versions are tracked with `index_id` and a sidecar JSON next to FAISS artifacts.
  - Postgres metadata stores vectors, index shards, and index version rows.
  - Idempotent indexing based on a deterministic key (same corpus + params).
- UI and CLI
  - Streamlit UI (`src/streamlit_app.py`) provides a Chat tab and DOI Network tab.
  - Console entrypoints: `ragonomics index | query | ui | benchmark`.
- Caching
  - Crossref responses cached in Postgres.
  - Query/answer cache stored in local SQLite (`ragonomics_query_cache.sqlite`).

Data and Metadata Stores
------------------------
- Postgres (`DATABASE_URL`):
  - `vectors`, `index_shards`, `index_versions`, `pipeline_runs`, and failure logs.
- Local artifacts:
  - FAISS indexes in `vectors.index` and versioned shards in `indexes/`.
  - Index version sidecar JSON next to each shard.
  - Query cache in `ragonomics_query_cache.sqlite`.

Reproducibility
---------------
- A config hash is computed from effective settings (config + env overrides).
- Each indexing run writes a manifest JSON next to the index shard containing:
  - git SHA, config hash, model names, chunk params, paper list, timestamps, and artifact paths.

Retrieval Quality Controls
--------------------------
- Optional query expansion and LLM reranking to improve relevance.
- Section-aware chunking enriches chunk metadata and retrieval provenance.
- Guardrails prevent retrieval when the FAISS shard and DB `index_id` disagree.

Operational Hardening
---------------------
- Idempotent indexing: same corpus + params does not double-insert.
- Structured JSON logging for key operations.
- OpenAI and Crossref calls include retries and failure recording in Postgres.

Evaluation
----------
- `src/eval.py` provides retrieval metrics (recall@k, MRR) and answer proxies
  (citation coverage, hallucination proxy, self-consistency).
- Golden-set format supports curated Q/A and expected citations.

Queueing
--------
- Redis + RQ (`src/rq_queue.py`) for async indexing jobs.

Benchmarks
----------
- `src/benchmark.py` and `tools/benchmark.py` measure indexing, chunking, and retrieval timing.

Entrypoints
-----------
- `ragonomics index` builds FAISS indexes.
- `ragonomics query` runs a question against a paper.
- `ragonomics ui` launches the Streamlit UI.
- `ragonomics benchmark` runs the benchmark suite.
