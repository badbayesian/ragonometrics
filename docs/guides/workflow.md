# Workflow and CLI Usage

Console Entrypoints
-------------------
After installation, use:

```bash
ragonometrics query --paper papers/example.pdf --question "What is the research question?" --model gpt-5
ragonometrics store-metadata --papers-dir papers/ --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
ragonometrics store-workflow-reports --reports-dir reports --recursive --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
ragonometrics benchmark --papers-dir papers/ --out bench/benchmark.csv --limit 5
ragonometrics workflow --papers papers/
ragonometrics workflow --papers papers/ --agentic --question "What is the key contribution?"
ragonometrics workflow --papers papers/ --agentic --agentic-citations --question "What is the key contribution?"
```

Example inputs live in [`papers/`](https://github.com/badbayesian/ragonometrics/tree/main/papers).

Commands that require Docker (Postgres):
```bash
ragonometrics index --papers-dir papers/ --index-path vectors-3072.index --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics"
```

Index artifacts default to [`vectors-3072.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors-3072.index) and are versioned in [`indexes/`](https://github.com/badbayesian/ragonometrics/tree/main/indexes). Keep legacy [`vectors.index`](https://github.com/badbayesian/ragonometrics/blob/main/vectors.index) for backward compatibility with 1536-dim runs.

Workflow Notes
--------------
- `--papers` accepts a directory or a single PDF file.
- Use `--report-question-set structured|agentic|both|none` to control report questions.
- For faster runs, reduce `TOP_K` or use `report-question-set agentic`.
- Each run writes a prep manifest to [`reports/prep-manifest-<run_id>.json`](https://github.com/badbayesian/ragonometrics/tree/main/reports) for corpus profiling.
- Completed workflow runs also attempt to persist the full report payload into Postgres table `workflow_reports` as `jsonb` when `DATABASE_URL` (or `--meta-db-url`) is reachable.
- If OpenAI returns `insufficient_quota` (`429`), the workflow now writes partial state/report and terminates early with workflow status `failed`.

Workflow Choices
----------------
You can combine these flags and env vars to tailor runtime behavior.

CLI Flags
```bash
ragonometrics workflow --papers papers/ \
  --agentic \
  --agentic-citations \
  --question "What is the key contribution?" \
  --agentic-model gpt-5 \
  --report-question-set both \
  --meta-db-url "postgres://user:pass@localhost:5432/ragonometrics" \
  --config-path config.toml
```

Environment Variables
| Name | What It Does | Default |
| --- | --- | --- |
| `WORKFLOW_AGENTIC` | Enable agentic sub-question planning. | `0` |
| `WORKFLOW_QUESTION` | Main workflow question. | `Summarize the paper's research question, methods, and key findings.` |
| `WORKFLOW_AGENTIC_MODEL` | Model override for the agentic step. | `CHAT_MODEL` |
| `WORKFLOW_AGENTIC_CITATIONS` | Include citations in the agentic context. | `0` |
| `WORKFLOW_REPORT_QUESTIONS` | Enable structured report questions. | `1` |
| `WORKFLOW_REPORT_QUESTIONS_SET` | Report set: `structured|agentic|both|none`. | `structured` |
| `WORKFLOW_REPORT_QUESTION_WORKERS` | Concurrency for report questions. | `8` |
| `DATABASE_URL` | Enable indexing + hybrid retrieval. | empty |
| `FRED_API_KEY` | Enable econ data step. | unset |
| `ECON_SERIES_IDS` | FRED series IDs. | empty |
| `PREP_HASH_FILES` | Hash PDFs in prep step. | `1` |
| `PREP_VALIDATE_TEXT` | Validate text extraction in prep step. | `0` |
| `PREP_FAIL_ON_EMPTY` | Fail workflow if corpus is empty. | `0` |
| `PREP_VALIDATE_ONLY` | Exit after prep step. | `0` |
