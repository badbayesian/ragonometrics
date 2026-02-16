Ragonometrics - RAG pipeline for economics papers
============================================

Overview
--------
Ragonometrics ingests PDFs, extracts per-page text for provenance, chunks with overlap, embeds chunks, indexes vectors in Postgres (`pgvector` + `pgvectorscale`) with FAISS fallback artifacts, and serves retrieval + LLM summaries via CLI and a Streamlit UI. External metadata is enriched via OpenAlex and CitEc when available. Author display uses layered lookup (OpenAlex, PDF metadata, then first-page text parsing) to reduce `Unknown` results. Async workflow execution is Postgres-backed (`workflow.async_jobs`) and processed by the queue worker service. The system is designed to be reproducible, auditable, and scalable from local runs to a Postgres-backed deployment.

This repo is a combination of coding + vibe coding.

Quick Start
-----------
1. Install dependencies in your enviroment.

```bash
python -m pip install -e .
```

2. Install Poppler (provides `pdftotext` and `pdfinfo`). On Windows, add Poppler `bin` to PATH.

3. Set your OpenAI API key in your environment (see [Configuration](https://github.com/badbayesian/ragonometrics/blob/main/docs/configuration/configuration.md)).

4. Place PDFs in [`papers/`](https://github.com/badbayesian/ragonometrics/tree/main/papers) (e.g., `papers/your.pdf`) or set `PAPERS_DIR`.

5. Run the summarizer.

```bash
python -m ragonometrics.core.main
```

Docker Compose (Current Defaults)
---------------------------------
- Streamlit is exposed on host port `8585` (`8585:8501`), so open `http://localhost:8585`.
- LAN access uses `http://<host-ip>:8585`.
- Containers read papers from `/app/papers`, mounted from host path `${PAPERS_HOST_DIR:-./papers}`.
- In the Streamlit UI, "Papers directory" is read-only and server-configured (not user-editable).

Example `.env` for container paper access:

```bash
PAPERS_HOST_DIR=./papers
```

If Docker runs on Windows and your repo is on a mapped network drive (for example `Z:`),
bind mounts may appear empty in Linux containers. Use a local path instead, for example:

```bash
PAPERS_HOST_DIR=C:/ragonometrics-papers
```

Bring up containers and validate paper visibility:

```bash
docker compose up -d --build
docker compose ps
docker compose exec -T streamlit ls -la /app/papers
```

Bring up only the core runtime stack (UI + Postgres + queue worker):

```bash
docker compose up -d --build streamlit postgres rq-worker
```

pgAdmin (database browser) quick start:

```bash
python tools/standup_pgadmin.py
```

Then open the URL printed by the script (default `http://localhost:5050`) and inspect tables under:
- `workflow.run_records`
- `workflow.workflow_runs`
- `workflow.workflow_steps`
- `workflow.workflow_reports`
- `workflow.workstream_runs`
- `workflow.artifacts`

Recent Updates
--------------
- Streamlit answer rendering now includes an optional math-format review pass that rewrites formula/function notation to Markdown-friendly LaTeX.
- Streamlit now labels the evidence expander as `Snapshots`.
- Paper author lookup now falls back to first-page author parsing when metadata sources are incomplete.
- Workflow persistence is consolidated into `workflow.run_records` (`record_kind`: `run|step|report|question|artifact|workstream_link`) with JSON payloads and indexed metadata.
- Workflow runs now terminate early (after saving partial report/state) if OpenAI returns `insufficient_quota` (`429`).
- Workflow runs now support explicit workstream metadata (`--workstream-id`, `--arm`, `--parent-run-id`, `--trigger-source`) and persist this lineage in `workflow.run_records`.
- Token usage is logged per call with step/question attribution in `observability.token_usage` (`step`, `question_id`, `run_id`, `operation`, token counts).

Workflow (Recommended)
----------------------
The primary entrypoint for reproducible analysis is `ragonometrics workflow`. A workflow run bundles ingest, enrichment, optional agentic Q&A, indexing checks, evaluation, and report generation into one auditable run ID.

Start here for end-to-end paper analysis:

```bash
ragonometrics workflow --papers papers/ --agentic --report-question-set both --question "What is the key contribution?"
```

Workstream-oriented run example:

```bash
ragonometrics workflow --papers "papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf" --agentic --agentic-model gpt-5-nano --agentic-citations --report-question-set both --question "What are the paper's main contribution, identification strategy, key results, and limitations?" --workstream-id calorie-posting-chain-restaurants --arm gpt-5-nano --trigger-source cli
```

Async workstream enqueue (Postgres queue):

```bash
ragonometrics workflow --papers papers/ --agentic --report-question-set both --async --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics" --queue-db-url "postgres://user:pass@localhost:5432/ragonometrics"
python -m ragonometrics.integrations.rq_queue worker --db-url "postgres://user:pass@localhost:5432/ragonometrics"
```

Each run produces audit artifacts under [`reports/`](https://github.com/badbayesian/ragonometrics/tree/main/reports), including:
- `reports/workflow-report-<run_id>.json`
- `reports/prep-manifest-<run_id>.json`
- optional human-readable audit renderings (Markdown/PDF)

Example audit output from a real run:
- [Audit workflow report (Markdown)](reports/audit-workflow-report-1308532de7a9446d813e57129826aa71.md)
- [Audit workflow report (PDF)](reports/audit-workflow-report-1308532de7a9446d813e57129826aa71-latex.pdf)

Regenerate audit LaTeX/PDF from markdown:

```bash
python tools/markdown_to_latex_pdf.py --input reports/audit-workflow-report-1308532de7a9446d813e57129826aa71.md
```

CLI Commands
------------
- `ragonometrics workflow`: run the multi-step workflow (optionally agentic). `--agentic` enables sub-question planning and synthesis. `--agentic-citations` extracts citations from the PDF and injects a compact citations preview into the agentic context.
```bash
ragonometrics workflow --papers papers/
ragonometrics workflow --papers papers/ --agentic --question "What is the key contribution?"
ragonometrics workflow --papers papers/ --agentic --agentic-citations --report-question-set both --question "What is the key contribution?"
ragonometrics workflow --papers papers/ --agentic --report-question-set both --workstream-id my-workstream --arm baseline --trigger-source cli
ragonometrics workflow --papers papers/ --agentic --report-question-set both --async --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics" --queue-db-url "postgres://user:pass@localhost:5432/ragonometrics"
```
- `ragonometrics store-workflow-reports`: backfill report JSON files from `reports/` into Postgres `workflow.run_records` (`record_kind=report|question|artifact`).
```bash
ragonometrics store-workflow-reports --reports-dir reports --recursive --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
```
- `ragonometrics index`: build Postgres vector index + metadata (and FAISS artifact fallback) for fast querying later.
```bash
ragonometrics index --papers-dir papers/ --index-path vectors-3072.index --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
```
- `ragonometrics query`: ask a question against a single PDF.
```bash
ragonometrics query --paper papers/example.pdf --question "What is the research question?" --model gpt-5
```
- `ragonometrics store-metadata`: persist per-paper metadata (authors, DOI(s), OpenAlex/CitEc fields) to Postgres without building vectors.
```bash
ragonometrics store-metadata --papers-dir papers/ --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
```
- `ragonometrics ui`: launch the Streamlit UI for interactive questions
```bash
ragonometrics ui
```



Docs
-------------
Docs root: [docs/](https://github.com/badbayesian/ragonometrics/tree/main/docs)
- [Architecture](https://github.com/badbayesian/ragonometrics/blob/main/docs/architecture/architecture.md): System design, tradeoffs, and reproducibility.
- [Workflow Architecture](https://github.com/badbayesian/ragonometrics/blob/main/docs/architecture/workflow_architecture.md): Workflow steps, artifacts, and state.
- [Pipeline Current-State Audit](https://github.com/badbayesian/ragonometrics/blob/main/docs/architecture/pipeline-current-state-audit.md): Runtime flow inventory, persistence map, and essentialness matrix for simplification.
- [Postgres Unification Plan](https://github.com/badbayesian/ragonometrics/blob/main/docs/architecture/postgres-unification-plan.md): Target schema-by-stage design, DDL, and phased cutover.
- [Configuration](https://github.com/badbayesian/ragonometrics/blob/main/docs/configuration/configuration.md): [`config.toml`](https://github.com/badbayesian/ragonometrics/blob/main/config.toml) + env override reference.
- [Workflow and CLI](https://github.com/badbayesian/ragonometrics/blob/main/docs/guides/workflow.md): CLI commands and workflow usage.
- [Docker](https://github.com/badbayesian/ragonometrics/blob/main/docs/deployment/docker.md): Compose usage and container notes.
- [Indexing and Retrieval](https://github.com/badbayesian/ragonometrics/blob/main/docs/components/indexing.md): pgvector/pgvectorscale, FAISS fallback, Postgres metadata, queueing.
- [Streamlit UI](https://github.com/badbayesian/ragonometrics/blob/main/docs/guides/ui.md): UI launch and behavior.
- [Agentic workflow](https://github.com/badbayesian/ragonometrics/blob/main/docs/guides/agentic.md): Agentic mode overview and notes.
- [Econ schema](https://github.com/badbayesian/ragonometrics/blob/main/docs/data/econ_schema.md): Time-series schema and econ data notes.
- [Cloud deployment](https://github.com/badbayesian/ragonometrics/blob/main/docs/deployment/cloud.md): Deployment scaffolding and guidance.
- [ADRs](https://github.com/badbayesian/ragonometrics/tree/main/docs/adr): Architecture decision records.
