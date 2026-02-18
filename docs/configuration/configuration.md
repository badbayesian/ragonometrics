# Configuration

This document describes how Ragonometrics loads configuration and which environment variables are supported.

Primary Config File
-------------------
[`config.toml`](https://github.com/badbayesian/ragonometrics/blob/main/config.toml) is the primary configuration surface (see the config file in the repo). Env vars override any value, which is useful in containers/CI. To point at a different file, set `RAG_CONFIG=/path/to/config.toml`.

Environment Variables (Overrides)
---------------------------------
These env vars override `config.toml` under `[ragonometrics]` unless noted.

Core pipeline settings (config + env overrides)
| Name | Config key | Description | Default | Type |
| --- | --- | --- | --- | --- |
| `PAPERS_DIR` | `papers_dir` | Directory with PDFs. | `PROJECT_ROOT/papers` (see [`papers/`](https://github.com/badbayesian/ragonometrics/tree/main/papers)) | path |
| `MAX_PAPERS` | `max_papers` | Max PDFs to process per run. | `3` | int |
| `MAX_WORDS` | `max_words` | Max words per paper before truncation. | `12000` | int |
| `CHUNK_WORDS` | `chunk_words` | Words per chunk. | `350` | int |
| `CHUNK_OVERLAP` | `chunk_overlap` | Overlap between chunks. | `50` | int |
| `TOP_K` | `top_k` | Chunks retrieved for context. | `6` | int |
| `EMBED_BATCH` | `batch_size` | Batch size for embeddings. | `64` | int |
| `EMBEDDING_MODEL` | `embedding_model` | Embedding model name. | `text-embedding-3-small` | string |
| `OPENAI_MODEL` / `CHAT_MODEL` | `chat_model` | Chat model used for summaries. | `gpt-5-nano` | string |
| `DATABASE_URL` | `database_url` | Postgres URL for metadata + hybrid retrieval. | empty | string (URL) |
| `BM25_WEIGHT` | `bm25_weight` | Blend weight for hybrid retrieval. | `0.5` | float |
| `RERANKER_MODEL` | `reranker_model` | Optional LLM reranker model name. | empty | string |
| `RERANK_TOP_N` | `rerank_top_n` | Candidates to rerank. | `30` | int |
| `QUERY_EXPANSION` | `query_expansion` | Enable query expansion when non-empty. | empty | string/flag |
| `QUERY_EXPAND_MODEL` | `query_expand_model` | Model override for query expansion. | empty | string |
| `SECTION_AWARE_CHUNKING` | `section_aware_chunking` | Enable section-aware chunking. | `false` | bool |
| `FORCE_OCR` | `force_ocr` | Force OCR instead of `pdftotext`. | `false` | bool |
| `INDEX_IDEMPOTENT_SKIP` | `index_idempotent_skip` | Skip indexing when an identical index exists. | `true` | bool |
| `ALLOW_UNVERIFIED_INDEX` | `allow_unverified_index` | Allow retrieval if index versions disagree. | `false` | bool |

Runtime + workflow settings (env-only)
| Name | Description | Default | Type | Notes |
| --- | --- | --- | --- | --- |
| `RAG_CONFIG` | Path to alternate config file. | [`config.toml`](https://github.com/badbayesian/ragonometrics/blob/main/config.toml) | path | Used in [`ragonometrics/core/main.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/core/main.py). |
| `OPENAI_API_KEY` | OpenAI API key for embeddings + chat. | unset | string | Required for most runs. |
| `LLM_MODELS` | Extra models shown in Streamlit dropdown. | empty | CSV string | Example: `gpt-5,gpt-4.1-mini`. |
| `MATH_LATEX_REVIEW_ENABLED` | Enable post-answer review that formats math/function notation to LaTeX in UI answers. | `1` | bool | Set `0` to disable. |
| `MATH_LATEX_REVIEW_MODEL` | Model override for the math-format review pass. | `OPENAI_MODEL` | string | Falls back to the selected chat model when unset. |
| `STREAMLIT_USERS_JSON` | Optional multi-user auth map for Streamlit. | unset | JSON object | Example: `{\"admin\":\"pass\",\"tester\":\"pass\"}`. Takes precedence over single-user vars. |
| `STREAMLIT_USERNAME` | Optional UI username. | unset | string | Login disabled if missing. |
| `STREAMLIT_PASSWORD` | Optional UI password. | unset | string | Login disabled if missing. |
| `STREAMLIT_AUTH_BOOTSTRAP_FROM_ENV` | Auto-seed env credentials into `auth.streamlit_users` when DB table is empty. | `1` | bool | Set `0` to disable bootstrap seeding. |
| `OPENALEX_API_KEY` | OpenAlex API key. | unset | string | Required for higher rate limits. |
| `OPENALEX_MAILTO` | Contact email for OpenAlex polite pool. | unset | string | Recommended by OpenAlex. |
| `FRED_API_KEY` | FRED API key for econ step. | unset | string | Enables econ step. |
| `ECON_SERIES_IDS` | FRED series IDs (comma-separated). | empty | CSV string | Defaults to `GDPC1,FEDFUNDS` when econ step enabled with no list. |
| `WORKFLOW_AGENTIC` | Enable agentic workflow. | `0` | bool | Use `1` to enable. |
| `WORKFLOW_QUESTION` | Main workflow question. | `Summarize the paper's research question, methods, and key findings.` | string | Uses the built-in summary question when unset. |
| `WORKFLOW_AGENTIC_MODEL` | Model override for agentic step. | `CHAT_MODEL` | string | Falls back to chat model. |
| `WORKFLOW_AGENTIC_MAX_SUBQUESTIONS` | Max agentic sub-questions. | `3` | int | |
| `WORKFLOW_AGENTIC_CITATIONS` | Enable citation extraction in agentic step. | `0` | bool | Use `1` to enable. |
| `WORKFLOW_AGENTIC_CITATIONS_MAX` | Max citations to include. | `12` | int | |
| `WORKFLOW_REPORT_QUESTIONS` | Enable structured report questions. | `1` | bool | Use `0` to disable. |
| `WORKFLOW_REPORT_QUESTIONS_SET` | Report question set to run. | `structured` | enum | `structured|agentic|both|none`. |
| `WORKFLOW_REPORT_QUESTION_WORKERS` | Concurrency for report questions. | `8` | int | |
| `WORKSTREAM_ID` / `WORKFLOW_WORKSTREAM_ID` | Workstream grouping id for lineage/comparison runs. | empty | string | Also available as CLI `--workstream-id`. |
| `WORKSTREAM_ARM` / `WORKFLOW_ARM` | Variant arm label (for example `gpt-5`, `gpt-5-nano`). | empty | string | Also available as CLI `--arm`. |
| `WORKSTREAM_PARENT_RUN_ID` | Parent/baseline run id for comparisons. | empty | string | Also available as CLI `--parent-run-id`. |
| `WORKFLOW_TRIGGER_SOURCE` | Run trigger source label. | auto | string | Also available as CLI `--trigger-source`. |
| `GIT_SHA` | Override git commit stored with the run. | auto-detected | string | |
| `GIT_BRANCH` | Override git branch stored with the run. | auto-detected | string | |
| `WORKFLOW_RENDER_AUDIT_ARTIFACTS` | Enable audit markdown/pdf artifact rendering. | `1` | bool | |
| `WORKFLOW_RENDER_AUDIT_PDF` | Enable PDF rendering for audit artifacts. | `1` | bool | Requires `pandoc` + TeX engine in PATH. |
| `DISKANN_QUERY_RESCORE` | Optional `pgvectorscale` query-time rescore count. | unset | int | Applied with `SET LOCAL diskann.query_rescore`. |
| `DISKANN_QUERY_SEARCH_LIST_SIZE` | Optional `pgvectorscale` search list size. | unset | int | Applied with `SET LOCAL diskann.query_search_list_size`. |
| `PREP_HASH_FILES` | Hash PDF files during prep. | `1` | bool | Set `0` for faster scans. |
| `PREP_VALIDATE_TEXT` | Run text extraction during prep. | `0` | bool | Enables empty-text detection. |
| `PREP_FAIL_ON_EMPTY` | Fail workflow if corpus is empty. | `0` | bool | Treats no PDFs or no text as failure. |
| `PREP_VALIDATE_ONLY` | Exit after prep step. | `0` | bool | Writes report and skips ingest/agentic/index. |

Streamlit Auth Precedence
-------------------------
Streamlit resolves login credentials in this order:
1. Active users in Postgres table `auth.streamlit_users`.
2. If DB has no active users, fallback to env credentials from:
   - `STREAMLIT_USERS_JSON` (JSON object map of `{username: password}`), then
   - legacy single-user pair `STREAMLIT_USERNAME` + `STREAMLIT_PASSWORD`.

Example multi-user value:

```bash
STREAMLIT_USERS_JSON={"admin":"REDACTED_STREAMLIT_PASSWORD","tester":"REDACTED_STREAMLIT_PASSWORD"}
```

Config Persistence
------------------
Workflow runs persist resolved config values in Postgres:
- `workflow.run_records.config_hash` (for `record_kind='run'`)
- `workflow.run_records.config_effective_json` (for `record_kind='run'`)
- `workflow.run_records.metadata_json` (includes `config_path` when present)

The full run report JSON (including the full `config` snapshot) is also stored in:
- `workflow.run_records.payload_json` (for `record_kind='report'`)

Structured Workstream Export Notes
----------------------------------
Streamlit structured export supports two formats:
- `Compact`: minimal fields for question/answer cache inspection.
- `Full`: includes richer structured-question fields from `workflow.run_records` question payloads (for example `confidence_score`, `retrieval_method`, `citation_anchors`).

For historical compact-only rows, use one of:
- Streamlit button: `Regenerate Missing Full Fields (Export Scope)`.
- CLI tool: `python tools/backfill_structured_question_fields.py --db-url "$DATABASE_URL" --apply`.
