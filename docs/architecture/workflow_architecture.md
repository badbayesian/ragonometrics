# Ragonometrics Workflow Architecture

This document describes the workflow subsystem: how it orchestrates multi-step runs, what it reads and writes, how it handles errors, and what artifacts it produces.

Overview
--------
The workflow runner coordinates ingestion, enrichment, optional econ data pulls, optional agentic QA, optional indexing, evaluation, and report emission. It is implemented in `ragonometrics/pipeline/workflow.py` and persists state transitions to SQLite (`sqlite/ragonometrics_workflow_state.sqlite`).

Workflow Diagram
----------------
```mermaid
flowchart LR
  Start[CLI: ragonometrics workflow] --> Ingest[Ingest PDFs]
  Ingest --> Enrich[External metadata]
  Enrich --> Econ[Econ data (optional)]
  Econ --> Agentic[Agentic QA (optional)]
  Agentic --> Index[Index build (optional)]
  Index --> Eval[Evaluate]
  Eval --> Report[Write report JSON]

  Ingest -->|state| StateDB[(workflow state DB)]
  Enrich -->|state| StateDB
  Econ -->|state| StateDB
  Agentic -->|state| StateDB
  Index -->|state| StateDB
  Eval -->|state| StateDB
  Report -->|state| StateDB

  Agentic --> UsageDB[(token usage DB)]
  Index --> Postgres[(metadata DB)]
```

Entry Points
------------
- CLI: `ragonometrics workflow --papers <path> [--agentic ...]`
- Queue: `ragonometrics/integrations/rq_queue.py` enqueues `workflow_entrypoint`

The `--papers` flag accepts either a **directory** or a **single PDF file**. The runner normalizes this into a list of PDF paths.

Step-by-Step Behavior
---------------------
1) Ingest  
   - Discovers PDFs (`.pdf`) and extracts page-level text.  
   - Uses `pdftotext` + `pdfinfo`, with OCR fallback if enabled.  
   - Emits counts and creates `Paper` objects.

2) Enrich  
   - Fetches Semantic Scholar and CitEc metadata when available.  
   - Only used as context for downstream analysis.

3) Econ Data (optional)  
   - Pulls FRED series if `FRED_API_KEY` or `ECON_SERIES_IDS` are set.

4) Agentic (optional)  
   - Generates sub-questions (agentic plan).  
   - Answers each sub-question with retrieval context.  
   - Builds a final synthesized answer.  
   - Optionally extracts citations for context enrichment.  
   - Generates structured report questions (A–K) and optional “previous questions” set.

5) Index (optional)  
   - Builds FAISS index + Postgres metadata if `DATABASE_URL` is reachable.  
   - Skips gracefully if DB is unreachable.

6) Evaluate  
   - Computes light-weight chunk statistics (avg/max/min).

7) Report  
   - Writes a JSON report to `reports/workflow-report-<run_id>.json`.

Artifacts and State
-------------------
- Workflow state DB: `sqlite/ragonometrics_workflow_state.sqlite`
  - `workflow_runs` tracks run metadata and status.
  - `workflow_steps` tracks step outputs, timestamps, status.
- Report JSON: `reports/workflow-report-<run_id>.json`
- Usage tracking: `sqlite/ragonometrics_token_usage.sqlite`
- Optional FAISS + metadata: `vectors.index`, `indexes/`, Postgres tables.

Report Schema Highlights
------------------------
Each report includes:
- `run_id`, `started_at`, `finished_at`
- `config` snapshot (effective config + hash)
- Step outputs under `ingest`, `enrich`, `econ_data`, `agentic`, `index`, `evaluate`

Agentic outputs include:
- `subquestions`, `sub_answers`, `final_answer`
- `report_questions` list with rich fields:
  - `answer`, `evidence_type`, `confidence`
  - `citation_anchors` (page + word offsets)
  - `quote_snippet`, `table_figure`, `data_source`
  - `assumption_flag`, `assumption_notes`, `related_questions`

Concurrency
-----------
- Report questions are executed concurrently (default 8 workers).
- Control with `WORKFLOW_REPORT_QUESTION_WORKERS`.

Failure Handling
----------------
- Step failures are recorded with `status: failed` and error text.
- If Postgres is unreachable, indexing is skipped with `reason: db_unreachable`.
- If hybrid retrieval fails, the agentic step falls back to local embedding retrieval.

Key Configuration Flags
-----------------------
- `WORKFLOW_AGENTIC=1` to enable agentic step
- `WORKFLOW_QUESTION` sets the main question
- `WORKFLOW_AGENTIC_MODEL` model override
- `WORKFLOW_AGENTIC_CITATIONS=1` to include citations context
- `WORKFLOW_REPORT_QUESTIONS=0` to skip structured report questions
- `WORKFLOW_REPORT_QUESTIONS_SET=structured|agentic|both|none`
- `WORKFLOW_REPORT_QUESTION_WORKERS=8` controls concurrency
- `DATABASE_URL` enables indexing + hybrid retrieval
- `FRED_API_KEY`, `ECON_SERIES_IDS` enable econ step

Performance Tips
----------------
- Use `report-question-set agentic` for faster runs.
- Lower `TOP_K` or increase `CHUNK_WORDS` to reduce retrieval cost.
- Disable citations if not needed.

Code Locations
--------------
- Orchestration: `ragonometrics/pipeline/workflow.py`
- State persistence: `ragonometrics/pipeline/state.py`
- Report schema: `ragonometrics/pipeline/workflow.py` (agentic report section)
