# Current Postgres Tables and Schemas

This document summarizes the currently used Postgres schemas/tables and their columns, based on the active code paths and the current database state.

## ingestion

### ingestion.documents

| Column | Type | Nullable |
| --- | --- | --- |
| doc_id | text | No |
| path | text | No |
| title | text | Yes |
| author | text | Yes |
| extracted_at | timestamptz | Yes |
| file_hash | text | Yes |
| text_hash | text | Yes |

### ingestion.paper_metadata

| Column | Type | Nullable |
| --- | --- | --- |
| doc_id | text | No |
| path | text | No |
| title | text | Yes |
| author | text | Yes |
| authors_json | jsonb | No |
| primary_doi | text | Yes |
| dois_json | jsonb | No |
| openalex_id | text | Yes |
| openalex_doi | text | Yes |
| publication_year | integer | Yes |
| venue | text | Yes |
| repec_handle | text | Yes |
| source_url | text | Yes |
| openalex_json | jsonb | No |
| citec_json | jsonb | No |
| metadata_json | jsonb | No |
| extracted_at | timestamptz | Yes |
| updated_at | timestamptz | No |

### ingestion.prep_manifests

| Column | Type | Nullable |
| --- | --- | --- |
| run_id | text | No |
| created_at | timestamptz | No |
| corpus_hash | text | Yes |
| payload | jsonb | No |

## enrichment

### enrichment.openalex_cache

| Column | Type | Nullable |
| --- | --- | --- |
| cache_key | text | No |
| work_id | text | Yes |
| query | text | Yes |
| response | jsonb | No |
| fetched_at | timestamptz | No |

### enrichment.citec_cache

| Column | Type | Nullable |
| --- | --- | --- |
| cache_key | text | No |
| repec_handle | text | Yes |
| response | jsonb | No |
| fetched_at | timestamptz | No |

## indexing

### indexing.pipeline_runs

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| git_sha | text | Yes |
| extractor_version | text | Yes |
| embedding_model | text | Yes |
| chunk_words | integer | Yes |
| chunk_overlap | integer | Yes |
| normalized | boolean | Yes |
| idempotency_key | text | Yes |
| created_at | timestamptz | No |

### indexing.index_versions

| Column | Type | Nullable |
| --- | --- | --- |
| index_id | text | No |
| created_at | timestamptz | No |
| embedding_model | text | Yes |
| chunk_words | integer | Yes |
| chunk_overlap | integer | Yes |
| corpus_fingerprint | text | Yes |
| index_path | text | Yes |
| shard_path | text | Yes |

### indexing.index_shards

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| shard_name | text | Yes |
| path | text | Yes |
| pipeline_run_id | bigint | Yes |
| index_id | text | Yes |
| created_at | timestamptz | No |
| is_active | boolean | No |

### indexing.vectors

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| doc_id | text | No |
| chunk_id | text | No |
| chunk_hash | text | Yes |
| paper_path | text | Yes |
| page | integer | Yes |
| start_word | integer | Yes |
| end_word | integer | Yes |
| text | text | Yes |
| embedding | vector | Yes |
| pipeline_run_id | bigint | Yes |
| created_at | timestamptz | Yes |

## workflow

### workflow.workflow_runs

| Column | Type | Nullable |
| --- | --- | --- |
| run_id | text | No |
| created_at | timestamptz | No |
| status | text | Yes |
| papers_dir | text | Yes |
| config_hash | text | Yes |
| metadata_json | jsonb | No |

### workflow.workflow_steps

| Column | Type | Nullable |
| --- | --- | --- |
| run_id | text | No |
| step | text | No |
| status | text | Yes |
| started_at | timestamptz | Yes |
| finished_at | timestamptz | Yes |
| output_json | jsonb | No |

### workflow.workflow_reports

| Column | Type | Nullable |
| --- | --- | --- |
| run_id | text | No |
| status | text | Yes |
| started_at | timestamptz | Yes |
| finished_at | timestamptz | Yes |
| papers_dir | text | Yes |
| report_path | text | Yes |
| agentic_status | text | Yes |
| report_questions_set | text | Yes |
| payload | jsonb | No |
| created_at | timestamptz | No |
| updated_at | timestamptz | No |

## retrieval

### retrieval.query_cache

| Column | Type | Nullable |
| --- | --- | --- |
| cache_key | text | No |
| query | text | Yes |
| paper_path | text | Yes |
| model | text | Yes |
| context_hash | text | Yes |
| answer | text | Yes |
| created_at | timestamptz | No |

### retrieval.retrieval_events

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| created_at | timestamptz | No |
| run_id | text | Yes |
| request_id | text | Yes |
| query | text | Yes |
| method | text | Yes |
| top_k | integer | Yes |
| stats_json | jsonb | No |

## observability

### observability.token_usage

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| created_at | timestamptz | No |
| model | text | Yes |
| operation | text | Yes |
| input_tokens | integer | Yes |
| output_tokens | integer | Yes |
| total_tokens | integer | Yes |
| session_id | text | Yes |
| request_id | text | Yes |
| run_id | text | Yes |
| meta | jsonb | No |

### observability.request_failures

| Column | Type | Nullable |
| --- | --- | --- |
| id | bigint | No |
| component | text | Yes |
| error | text | Yes |
| context_json | jsonb | No |
| created_at | timestamptz | No |

## Migration-only Legacy Tables

The following tables are defined/used by SQLite migration code (`ragonometrics/indexing/migrate.py`) and are not part of the normal runtime write path:

- `ingestion.dois`
- `ingestion.citations`

