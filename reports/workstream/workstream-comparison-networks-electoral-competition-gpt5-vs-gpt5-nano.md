# Workstream Comparison: NetworksElectoralCompetition (GPT-5 vs GPT-5-nano)

## Inputs
- Baseline (archive): `reports/archived/workflow-report-cf1d92ff869046f39b7319007e9a7835.json`
- Variant (current): `reports/workflow-report-c52a4b6842704aac8c4258c71def4e29.json`

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
| networks-electoral-competition-gpt5-vs-gpt5-nano-v1 | archive | reports/archived/workflow-report-cf1d92ff869046f39b7319007e9a7835.json | cf1d92ff869046f39b7319007e9a7835 | papers\NetworksElectoralCompetition.pdf | 2026-02-15T19:29:20.818045+00:00 | 2026-02-15T19:40:51.819956+00:00 | 691.0 | gpt-5 | text-embedding-3-large | both | 84 | completed | skipped | db_unreachable | skipped | 16de593c95c2edb0cfcbb108e9affaa80f59531e97d33d12381e0b725da73b2e | ed36f9c374bd26f642bca2aef6a1df3ce5139b582ea5b545619c7caf2071f355 | 0.2921069186783523 | high=0, medium=6, low=77 | - Core contribution: Formalizes and empirically tests how district homophily governs the diffusion of information about elected officials—modeled as a network statistic that determines the equilibrium share of informed voters—and links t... |
| networks-electoral-competition-gpt5-vs-gpt5-nano-v1 | current | reports/workflow-report-c52a4b6842704aac8c4258c71def4e29.json | c52a4b6842704aac8c4258c71def4e29 | papers\NetworksElectoralCompetition.pdf | 2026-02-15T21:37:57.144102+00:00 | 2026-02-15T21:43:52.685368+00:00 | 355.54 | gpt-5-nano | text-embedding-3-large | both | 84 | completed | indexed |  | stored | 16de593c95c2edb0cfcbb108e9affaa80f59531e97d33d12381e0b725da73b2e | 8d63faf73797db1e19a30727d3482d381e1228b44649dd760b815556e9b3b8b1 | 0.29217296599402814 | high=0, medium=6, low=77 | - Core contribution: A novel roll-off metric that quantifies abstention by top-of-ticket voters in House races (defined as the difference between top-ticket votes and House votes, as a share of top-ticket votes), linking to Swing Voter’s... |

## Comparison Notes

- Both runs process the same paper path: `papers\NetworksElectoralCompetition.pdf`.
- Baseline model: `gpt-5`; variant model: `gpt-5-nano`.
- Baseline duration: `691.0` sec; variant duration: `355.54` sec.
- Baseline confidence mean: `0.2921069186783523`; variant confidence mean: `0.29217296599402814`.
- Baseline index status: `skipped` (db_unreachable); variant index status: `indexed` (None).
- Baseline report store: `skipped`; variant report store: `stored`.
