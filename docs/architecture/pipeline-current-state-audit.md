# Pipeline Simplification Baseline: Current-State Flow Audit

This document captures the current runtime architecture as a simplification baseline.
Scope includes all runtime flows: workflow, query/UI, indexing, metadata-only, and async queue.

## Runtime Entry Points and Process Flows
| Flow | Entry | Core Path | Primary Outputs |
| --- | --- | --- | --- |
| Workflow (sync) | `ragonometrics workflow` via `ragonometrics/cli/entrypoints.py` | `run_workflow` in `ragonometrics/pipeline/workflow.py` | `reports/workflow-report-<run_id>.json`, `reports/prep-manifest-<run_id>.json`, `workflow.run_records` rows |
| Workflow (async) | `ragonometrics workflow --async` via `ragonometrics/cli/entrypoints.py` | `enqueue_workflow` -> Postgres queue worker -> `workflow_entrypoint` in `ragonometrics/integrations/rq_queue.py`, `ragonometrics/pipeline/workflow.py` | Same as sync, executed by Postgres queue worker |
| Query CLI | `ragonometrics query` via `ragonometrics/cli/entrypoints.py` | `load_papers` -> chunk/embed -> `top_k_context` -> LLM answer | stdout answer + `retrieval.query_cache` rows |
| Streamlit chat | `ragonometrics ui` via `ragonometrics/cli/entrypoints.py` | `ragonometrics/ui/streamlit_app.py` | Interactive answers, snapshots, usage metrics, cache rows |
| Index-only | `ragonometrics index` via `ragonometrics/cli/entrypoints.py` | `build_index` in `ragonometrics/indexing/indexer.py` | `vectors.index`, `indexes/vectors-*.index`, index sidecar/manifests, Postgres vectors/metadata |
| Metadata-only | `ragonometrics store-metadata` via `ragonometrics/cli/entrypoints.py` | `store_paper_metadata` in `ragonometrics/indexing/paper_store.py` | Postgres `paper_metadata` upserts |
| Workflow report backfill | `ragonometrics store-workflow-reports` via `ragonometrics/cli/entrypoints.py` | `store_workflow_reports_from_dir` in `ragonometrics/pipeline/report_store.py` | Postgres `workflow.run_records` (`record_kind=report/question/artifact`) backfilled from disk |

## Main Data Flows (as implemented)
1. Workflow orchestration
   - `prep` -> `ingest` -> `enrich` -> optional `econ_data` -> optional `agentic` -> optional `index` -> `evaluate` -> `report` in `ragonometrics/pipeline/workflow.py`.
2. Ingestion + enrichment coupling
   - `load_papers` in `ragonometrics/core/main.py` performs extraction plus OpenAlex/CitEc fetching inline.
3. Retrieval path selection
   - `top_k_context` in `ragonometrics/core/main.py` tries Postgres hybrid retrieval first (`ragonometrics/indexing/retriever.py`), otherwise local embedding cosine fallback.
4. Indexing persistence
   - `build_index` in `ragonometrics/indexing/indexer.py` writes vectors/documents/paper metadata, index versions/shards/manifests.
5. Workflow report persistence
   - JSON report file + optional DB store in `ragonometrics/pipeline/workflow.py` with storage helper in `ragonometrics/pipeline/report_store.py`.
6. State and observability
   - Step-level state and lineage in `workflow.run_records` via `ragonometrics/pipeline/state.py`; token usage in `observability.token_usage` via `ragonometrics/pipeline/token_usage.py`.
7. UI flow
   - Streamed Q&A, cache, math-LaTeX review pass, and snapshots in `ragonometrics/ui/streamlit_app.py`.

## Store and Artifact Map
| Layer | Store/Artifact | Produced By | Consumed By |
| --- | --- | --- | --- |
| Filesystem | `reports/prep-manifest-<run_id>.json` (default path) | `prep_corpus` (`ragonometrics/pipeline/prep.py`) | Workflow audit/debug |
| Filesystem | `reports/workflow-report-<run_id>.json` (default path) | Workflow finalization (`ragonometrics/pipeline/workflow.py`) | Humans + backfill command |
| Filesystem | `vectors.index` and `indexes/vectors-*.index` | `build_index` (`ragonometrics/indexing/indexer.py`) | Retriever fallback path |
| Filesystem | index sidecar/manifests (`*.index.version.json`, run manifest) | `build_index` + manifest helpers | Index-version verification |
| Postgres | `workflow.run_records` | `create_workflow_run`/`record_step` (`ragonometrics/pipeline/state.py`) | Workflow traceability |
| Postgres | `retrieval.query_cache` | `set_cached_answer` (`ragonometrics/pipeline/query_cache.py`) | Query CLI + Streamlit |
| Postgres | `observability.token_usage` | `record_usage` (`ragonometrics/pipeline/token_usage.py`) | Usage tab and reporting |
| Postgres | `enrichment.openalex_http_cache`, `enrichment.paper_openalex_metadata`, `enrichment.citec_cache` | integration caches + canonical per-paper OpenAlex metadata | Ingestion enrichment |
| Postgres | `indexing.vectors`, `ingestion.documents`, `ingestion.paper_metadata`, run/index tables | `build_index`, metadata helpers | Hybrid retrieval + metadata |
| Postgres | `workflow.run_records` (`record_kind=report/question/artifact`) | `store_workflow_report` (`ragonometrics/pipeline/report_store.py`) | Backfill/report querying |

## Key Current-State Findings (for simplification)
- Ingest and external enrichment are coupled in `load_papers`, reducing composability.
- Retrieval can switch between local and global DB context by env, introducing behavioral variance.
- Workflow agentic step embeds chunks, and index step embeds again later; duplicated heavy work.
- Multiple persistence layers are active simultaneously (JSON report/manifests on filesystem + Postgres runtime stores).

## Essentialness Table (Pipeline Pieces)
| Component | Location | Function | Essentialness | Keep/Simplify Decision |
| --- | --- | --- | --- | --- |
| Workflow orchestrator | `ragonometrics/pipeline/workflow.py` | End-to-end run control | Critical | Keep as primary orchestrator |
| Prep profiler | `ragonometrics/pipeline/prep.py` | Corpus validation/manifest | High | Keep; maintain as preflight gate |
| Paper ingestion | `ragonometrics/core/main.py` | PDF text + metadata extraction | Critical | Keep; split enrichment concerns |
| Chunking | `ragonometrics/core/main.py` + `ragonometrics/core/io_loaders.py` | Provenance chunk generation | Critical | Keep |
| Embedding utility | `ragonometrics/core/main.py` | Embedding generation | Critical | Keep; centralize all embedding calls |
| Retrieval selector | `ragonometrics/core/main.py` | Hybrid/local context retrieval | Critical | Keep; make deterministic mode explicit |
| Agentic QA/report questions | `ragonometrics/pipeline/workflow.py` (`_answer_report_questions`) | Deep analysis/report generation | High | Keep as optional module |
| Index builder | `ragonometrics/indexing/indexer.py` | Vector + metadata index creation | Critical | Keep; isolate side effects |
| Metadata schema helpers | `ragonometrics/indexing/metadata.py` | Postgres schema/run/index records | Critical | Keep |
| Hybrid retriever | `ragonometrics/indexing/retriever.py` | BM25 + vector search | High | Keep; tighten doc/paper scoping |
| Workflow state store | `ragonometrics/pipeline/state.py` | Step/state audit trail | High | Keep |
| Workflow report store | `ragonometrics/pipeline/report_store.py` | JSONB report persistence | High | Keep |
| Query cache | `ragonometrics/pipeline/query_cache.py` | Reuse Q/A responses | Medium | Keep optional |
| Token usage logging | `ragonometrics/pipeline/token_usage.py` | Usage observability | Medium | Keep optional |
| Streamlit app | `ragonometrics/ui/streamlit_app.py` | Primary interactive UX | High | Keep |
| Async queue wrapper | `ragonometrics/integrations/rq_queue.py` | Background workflow/index jobs | Medium | Keep optional |
| Econ data connector | `ragonometrics/integrations/econ_data.py` | FRED/WB fetch | Optional | Keep plugin-style optional |
| OpenAlex/CitEc integrations | `ragonometrics/integrations/openalex.py`, `ragonometrics/integrations/citec.py` | Metadata enrichment | High | Keep; decouple from base ingestion |

## Baseline Main Components (for simplification phases)
- Ingest and Normalize
  - PDF extraction, text normalization, chunking, and core paper objects.
- Retrieve and Reason
  - Embeddings, retrieval selection (hybrid/local), agentic decomposition/synthesis.
- Persist and Audit
  - Reports, state tracking, usage tracking, Postgres index/report stores.
- Interfaces (CLI/UI/Queue)
  - Synchronous CLI, Streamlit UX, and async queue entrypoints.

## Public API and Interface Impact (this phase)
- No runtime API/type changes in this phase (documentation-only baseline).
- Future simplification phases may change:
  - Internal boundaries around ingestion/enrichment and retrieval mode control.

## Validation Checklist for This Baseline
1. `ragonometrics --help` still lists current command surface.
2. `ragonometrics workflow --help` aligns with documented workflow flags.
3. `ragonometrics query --help`, `index --help`, `store-metadata --help`, `store-workflow-reports --help` align with report inventory.
4. This document is reachable from docs navigation links.
5. This report includes all runtime entry flows, persistence stores, and essentialness classifications.
