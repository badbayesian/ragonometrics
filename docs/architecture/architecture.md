# Ragonometrics Architecture: Decisions and Rationale

This document summarizes the current Ragonometrics architecture, design tradeoffs, and operational guidance.

Overview
--------
Ragonometrics ingests PDFs, extracts per-page text for provenance, chunks with overlap, embeds chunks, indexes embeddings in FAISS, and serves retrieval + LLM summaries via CLI and a Streamlit UI. The system enriches papers with external metadata (OpenAlex and CitEc) when available. The system is designed to be reproducible, auditable, and scalable from local runs to a Postgres-backed deployment.

Architecture Diagram
--------------------
```mermaid
flowchart LR
  subgraph Ingestion
    PDFs[PDFs] -->|pdftotext/pdfinfo| Extract[Text + Metadata]
    Extract -->|chunk + overlap| Chunks[Provenance Chunks]
  end

  subgraph Enrichment
    Chunks -->|DOI/RePEc extract| Ids[Identifiers]
    Ids -->|OpenAlex| OA[OpenAlex Cache]
    Ids -->|CitEc| CitEc[CitEc Cache]
  end

  subgraph Indexing
    Chunks -->|embeddings| Embeds[Embeddings]
    Embeds -->|FAISS| Faiss[FAISS Index]
    Chunks -->|metadata| PG[(Postgres)]
  end

  subgraph Retrieval
    Query[User Query] -->|expand/rerank| Retrieve[Hybrid Retrieval]
    Retrieve --> Context[Context Chunks]
    Context --> LLM[LLM Answer]
    OA --> LLM
    CitEc --> LLM
  end

  subgraph EconData
    FRED[FRED API] --> EconSeries[Time Series]
    WB[World Bank API] --> EconSeries
  end

  subgraph Workflow
    Runner[Workflow Runner] --> StateDB[(Workflow State DB)]
    Runner --> Report[Workflow Report]
    Runner --> Agentic[Agentic LLM Steps]
  end

  subgraph Interfaces
    CLI[CLI] --> Query
    UI[Streamlit UI] --> Query
  end

  subgraph Containers
    CUI[streamlit container] --> UI
    CWF[workflow container] --> Runner
    CRQ[rq-worker container] --> Runner
    CRedis[redis container]
    CPG[postgres container]
  end

  PG --> Retrieve
  Faiss --> Retrieve
  Runner --> PDFs
  Runner --> EconSeries
```

Key Components
--------------
- Config and prompts
  - [`config.toml`](https://github.com/badbayesian/ragonometrics/blob/main/config.toml) (optional) is the primary configuration surface with env-var overrides.
  - Centralized prompts live in [`ragonometrics/core/prompts.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/core/prompts.py).
- Package layout (logical groupings)
  - [`ragonometrics/core/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/core): settings, ingestion, extraction, core prompts, logging.
  - [`ragonometrics/pipeline/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/pipeline): LLM call wrapper, query cache, token usage accounting.
  - [`ragonometrics/indexing/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/indexing): FAISS indexing, Postgres metadata, hybrid retrieval, migrations.
  - [`ragonometrics/integrations/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/integrations): OpenAlex, CitEc, econ data, Redis/RQ jobs.
  - [`ragonometrics/ui/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/ui): Streamlit app.
  - [`ragonometrics/eval/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/eval): eval + benchmark tooling.
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
  - Streamlit UI ([`ragonometrics/ui/streamlit_app.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/ui/streamlit_app.py)) provides Chat and Usage tabs.
  - External metadata (OpenAlex with CitEc fallback) is shown in a UI expander and injected into prompts.
  - Console entrypoints: `ragonometrics index | query | ui | benchmark`.
- Agentic workflow
  - [`ragonometrics/pipeline/workflow.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/workflow.py) orchestrates prep -> ingest -> enrich -> index -> evaluate -> report.
  - State persisted in Postgres (`workflow.workflow_runs`, `workflow.workflow_steps`) via [`ragonometrics/pipeline/state.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/state.py).
  - Optional async execution with Redis + RQ ([`ragonometrics/integrations/rq_queue.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/rq_queue.py)).
  - Optional agentic step plans sub-questions, retrieves context, and synthesizes an answer.
- Caching
  - OpenAlex metadata cache in Postgres (`enrichment.openalex_cache`).
  - CitEc metadata cache in Postgres (`enrichment.citec_cache`).
  - Query/answer cache in Postgres (`retrieval.query_cache`).
  - Token usage in Postgres (`observability.token_usage`).

Data and Metadata Stores
------------------------
- Postgres (`DATABASE_URL`):
  - Ingestion: `ingestion.documents`, `ingestion.paper_metadata`, `ingestion.prep_manifests`.
  - Enrichment: `enrichment.openalex_cache`, `enrichment.citec_cache`.
  - Indexing: `indexing.vectors`, `indexing.index_shards`, `indexing.index_versions`, `indexing.pipeline_runs`.
  - Workflow: `workflow.workflow_runs`, `workflow.workflow_steps`, `workflow.workflow_reports`.
  - Retrieval: `retrieval.query_cache`, `retrieval.retrieval_events`.
  - Observability: `observability.token_usage`, `observability.request_failures`.
- Local artifacts:
  - FAISS indexes in [`vectors.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors.index) and versioned shards in [`indexes/`](https://github.com/badbayesian/ragonometrics/tree/main/indexes).
  - Index version sidecar JSON next to each shard.
  - Workflow JSON reports in [`reports/`](https://github.com/badbayesian/ragonometrics/tree/main/reports).

Reproducibility
---------------
- A config hash is computed from effective settings (config + env overrides).
- [`config.toml`](https://github.com/badbayesian/ragonometrics/blob/main/config.toml) is the primary config surface; env vars override for deploys.
- A prep manifest records corpus fingerprints and file-level metadata before ingestion.
- Each indexing run writes a manifest JSON next to the index shard containing:
  - git SHA, dependency fingerprints, config hash + effective config snapshot.
  - corpus fingerprint, embedding dim + hashes, chunking scheme, timestamps, and artifact paths.
  - deterministic paper list with stable `doc_id`s and per-chunk `chunk_id` + `chunk_hash` entries for diffable runs.

Tradeoffs
---------
- **Local-only (FAISS artifacts) vs Postgres-backed metadata**: Local artifacts are fast and low-friction but limited for shared access. Postgres adds infra overhead but enables multi-user metadata, hybrid retrieval, and durable state/report caching.
- **Determinism vs throughput**: Stable ordering, chunk hashes, and manifest recording improve auditability but add compute and I/O overhead during ingestion and indexing.
- **Agentic depth vs cost**: Agentic workflows improve coverage and synthesis quality but increase latency and token usage; structured questions are cheaper and more predictable.
- **Citations and provenance vs speed**: Citation extraction and page-level provenance improve trustworthiness but add extra parsing and retrieval work.
- **Hybrid retrieval vs simplicity**: BM25 + reranking can improve relevance but introduces more tuning surface and failure modes compared to pure vector search.

Retrieval Quality Controls
--------------------------
- Optional query expansion and LLM reranking to improve relevance.
- Section-aware chunking enriches chunk metadata and retrieval provenance.
- Guardrails prevent retrieval when the FAISS shard and DB `index_id` disagree.

Operational Hardening
---------------------
- Idempotent indexing: same corpus + params does not double-insert.
- Structured JSON logging for key operations.
- OpenAI calls include retries and failure recording in Postgres.
- OpenAlex and CitEc calls include retries and local caching.

Economics Data
--------------
- FRED and World Bank connectors live in [`ragonometrics/integrations/econ_data.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/econ_data.py).
- Example workflow in [`tools/econ_workflow.py`](https://github.com/badbayesian/ragonometrics/blob/main/tools/econ_workflow.py) (see [`docs/data/econ_schema.md`](https://github.com/badbayesian/ragonometrics/blob/main/docs/data/econ_schema.md)).

Evaluation
----------
- [`ragonometrics/eval/eval.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/eval/eval.py) provides retrieval metrics (recall@k, MRR) and answer proxies
  (citation coverage, hallucination proxy, self-consistency).
- Golden-set format supports curated Q/A and expected citations.

Queueing
--------
- Redis + RQ ([`ragonometrics/integrations/rq_queue.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/rq_queue.py)) for async indexing jobs.

Benchmarks
----------
- [`ragonometrics/eval/benchmark.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/eval/benchmark.py) and [`tools/benchmark.py`](https://github.com/badbayesian/ragonometrics/blob/main/tools/benchmark.py) measure indexing, chunking, and retrieval timing.

Entrypoints
-----------
- `ragonometrics index` builds FAISS indexes.
- `ragonometrics query` runs a question against a paper.
- `ragonometrics ui` launches the Streamlit UI.
- `ragonometrics benchmark` runs the benchmark suite.
- `ragonometrics workflow` runs the multi-step (optionally agentic) workflow.

Containerization
----------------
- [`Dockerfile`](https://github.com/badbayesian/ragonometrics/blob/main/Dockerfile) installs package dependencies and Poppler.
- [`compose.yml`](https://github.com/badbayesian/ragonometrics/blob/main/compose.yml) defines services for UI, workflow, Redis, RQ worker, and Postgres.
- Services run code from the image by default; add a bind mount for live code editing if desired.

