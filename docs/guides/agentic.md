# Agentic Workflow and State Management

This repo includes a lightweight, multi-step workflow runner that demonstrates:

- Orchestrating multi-step AI pipelines
- Persisting workflow state and step outputs
- Async execution through a Postgres-backed queue
- API-driven enrichment (OpenAlex, CitEc, FRED/World Bank)

Workflow Steps
--------------
1. **ingest**: load PDFs and extract normalized text
2. **enrich**: collect external metadata (OpenAlex with CitEc fallback)
3. **econ_data**: optional FRED/World Bank pulls for econ context
4. **agentic**: LLM-driven sub-question planning, retrieval, and synthesis
5. **index**: build FAISS index + Postgres metadata (if configured)
6. **evaluate**: compute lightweight quality stats
7. **report**: emit a JSON report for downstream review

State Persistence
-----------------
State is persisted to Postgres (schema `workflow`) via [`ragonometrics/pipeline/state.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/state.py) and [`ragonometrics/pipeline/report_store.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/report_store.py):

- `workflow.run_records` is the unified ledger table.
- `record_kind='run'` stores overall run metadata and status.
- `record_kind='step'` stores per-step status, timestamps, and output JSON.
- `record_kind='report'` stores full report payloads.
- `record_kind='question'` stores normalized structured Q&A rows.
- `record_kind='artifact'` stores generated report artifacts (JSON/MD/TEX/PDF paths + hashes).
- `record_kind='workstream_link'` stores workstream lineage/grouping for baseline-vs-variant runs.

Code locations:
- [`ragonometrics/pipeline/state.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/state.py)
- [`ragonometrics/pipeline/workflow.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/pipeline/workflow.py)

Async Execution (Postgres Queue)
--------------------------------
Use the queue helper in [`ragonometrics/integrations/rq_queue.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/rq_queue.py):

```python
from pathlib import Path
from ragonometrics.integrations.rq_queue import enqueue_workflow

job = enqueue_workflow(
    Path("papers/"),
    db_url="postgres://postgres:postgres@localhost:5432/ragonometrics",
)
print("queued job:", job.id)
```

Example input path: [`papers/`](https://github.com/badbayesian/ragonometrics/tree/main/papers).

Operational Notes
-----------------
- For long-running workflows, use durable Postgres storage and backups.
- Use [`reports/`](https://github.com/badbayesian/ragonometrics/tree/main/reports) for archived summaries or push to object storage.

Key Environment Variables
-------------------------
| Name | Description | Default | Type | Notes |
| --- | --- | --- | --- | --- |
| `DATABASE_URL` | Postgres URL for metadata + hybrid retrieval. | empty | string (URL) | Required for indexing/hybrid retrieval. |
| `WORKFLOW_AGENTIC` | Enable agentic step. | `0` | bool | Use `1` or `--agentic`. |
| `WORKFLOW_QUESTION` | Main agentic question. | `Summarize the paper's research question, methods, and key findings.` | string | CLI override `--question`. |
| `WORKFLOW_AGENTIC_CITATIONS` | Enable citation enrichment. | `0` | bool | Use `1` or `--agentic-citations`. |
| `WORKFLOW_REPORT_QUESTIONS_SET` | Report question mode. | `structured` | enum | `structured|agentic|both|none`, CLI `--report-question-set`. |
| `WORKSTREAM_ID` / `WORKFLOW_WORKSTREAM_ID` | Workstream grouping id. | empty | string | CLI `--workstream-id`. |
| `WORKSTREAM_ARM` / `WORKFLOW_ARM` | Variant arm label. | empty | string | CLI `--arm`. |
| `WORKSTREAM_PARENT_RUN_ID` | Parent/baseline run id. | empty | string | CLI `--parent-run-id`. |
| `WORKFLOW_RENDER_AUDIT_ARTIFACTS` | Enable audit markdown/pdf generation. | `1` | bool | |
| `WORKFLOW_RENDER_AUDIT_PDF` | Enable audit PDF generation. | `1` | bool | Requires `pandoc` + TeX engine in PATH. |

See [Configuration](https://github.com/badbayesian/ragonometrics/blob/main/docs/configuration/configuration.md) for the full list.
