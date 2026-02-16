# Workstream Comparison: Use of Cumulative Sums of Squares (GPT-5 vs GPT-5-nano)

## Inputs
- Baseline (archive): `reports/archived/workflow-report-1308532de7a9446d813e57129826aa71.json`
- Variant (current): `reports/workflow-report-34c88b2bf0194f5b9b72793845290a52.json`

## Schema

| Field | Type | Purpose |
| --- | --- | --- |
| `comparison_group_id` | `text` | Logical key that groups runs into one composed workstream comparison. |
| `source_bucket` | `enum(archive|current)` | Where the source report lives; helps provenance and filtering. |
| `report_path` | `text` | Path to the source workflow-report JSON used for this row. |
| `run_id` | `text` | Unique workflow execution identifier. |
| `papers_dir` | `text` | Input paper path(s) processed by the run. |
| `started_at` | `timestamptz` | Workflow start timestamp. |
| `finished_at` | `timestamptz` | Workflow end timestamp. |
| `duration_sec` | `numeric` | Run duration in seconds for cost/latency comparison. |
| `chat_model` | `text` | Primary LLM used by the run. |
| `embedding_model` | `text` | Embedding model used for retrieval/chunk similarity. |
| `report_question_set` | `enum` | Question set mode (structured|agentic|both|none). |
| `report_question_count` | `integer` | Number of structured/agentic report question records emitted. |
| `agentic_status` | `text` | Top-level status for agentic stage. |
| `index_status` | `text` | Top-level status for index stage. |
| `index_reason_or_error` | `text` | Failure/skipped reason to diagnose differences. |
| `report_store_status` | `text` | Whether report was stored to Postgres. |
| `prep_corpus_hash` | `text` | Fingerprint of corpus content; validates same input set. |
| `config_hash` | `text` | Effective configuration hash; validates run comparability. |
| `confidence_mean` | `numeric` | Mean confidence score across structured questions. |
| `confidence_labels` | `text` | Label distribution summary for quick quality profile. |
| `final_answer_preview` | `text` | Truncated synthesis preview for side-by-side semantic comparison. |

## Records

| comparison_group_id | source_bucket | report_path | run_id | papers_dir | started_at | finished_at | duration_sec | chat_model | embedding_model | report_question_set | report_question_count | agentic_status | index_status | index_reason_or_error | report_store_status | prep_corpus_hash | config_hash | confidence_mean | confidence_labels | final_answer_preview |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| icss-gpt5-vs-gpt5-nano-v1 | archive | reports/archived/workflow-report-1308532de7a9446d813e57129826aa71.json | 1308532de7a9446d813e57129826aa71 | papers\Use_of_Cumulative_Sums_of_Squares_for_Re.pdf | 2026-02-15T17:40:01.994514+00:00 | 2026-02-15T17:53:00.314525+00:00 | 778.32 | gpt-5 | text-embedding-3-large | structured | 83 | completed | skipped | db_unreachable | skipped | 5f8df89d3695858097ecae0e151ea277f490e3ad831113e01010c76e2243a47e | ed36f9c374bd26f642bca2aef6a1df3ce5139b582ea5b545619c7caf2071f355 | 0.27249828745446014 | high=0, medium=6, low=77 | - Primary: Introduces the ICSS procedure for variance change-point detection—built on a statistic $D_k$ that is a monotone transform of the two-sample variance F statistic $F_{T-k,k}=\frac{(C_T-C_k)/(T-k)}{C_k/k}$ with $C_k=\sum_{t=1}^k ... |
| icss-gpt5-vs-gpt5-nano-v1 | current | reports/workflow-report-34c88b2bf0194f5b9b72793845290a52.json | 34c88b2bf0194f5b9b72793845290a52 | papers\Use_of_Cumulative_Sums_of_Squares_for_Re.pdf | 2026-02-15T21:46:06.642379+00:00 | 2026-02-15T21:52:34.727145+00:00 | 388.08 | gpt-5-nano | text-embedding-3-large | structured | 83 | completed | indexed |  | stored | 5f8df89d3695858097ecae0e151ea277f490e3ad831113e01010c76e2243a47e | 8d63faf73797db1e19a30727d3482d381e1228b44649dd760b815556e9b3b8b1 | 0.2724441085027672 | high=0, medium=6, low=77 | - Key contribution   - Introduces ICSS (Cumulative Sums of Squares) as a fast, retrospective detector of variance changes in time series, using the D_k statistic to flag potential change points. (Inclán & Tiao, page 3, words 0-349)  - Co... |

## Comparison Notes

- Both runs process the same paper path: `papers\Use_of_Cumulative_Sums_of_Squares_for_Re.pdf`.
- Baseline model: `gpt-5`; variant model: `gpt-5-nano`.
- Baseline duration: `778.32` sec; variant duration: `388.08` sec.
- Baseline confidence mean: `0.27249828745446014`; variant confidence mean: `0.2724441085027672`.
- Baseline report-question count: `83`; variant report-question count: `83`.
- Baseline index status: `skipped` (db_unreachable); variant index status: `indexed` (None).
- Baseline report store: `skipped`; variant report store: `stored`.
