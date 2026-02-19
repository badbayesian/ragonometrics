# Postgres ERD (Web App)

This ERD summarizes the main runtime tables used by the web app and workflow runtime.

## Entity Relationship Diagram

```mermaid
erDiagram
    AUTH_USERS ||--o{ AUTH_SESSIONS : owns
    AUTH_USERS ||--o{ PROJECT_MEMBERSHIPS : joins
    PROJECTS ||--o{ PROJECT_MEMBERSHIPS : has
    PROJECTS ||--o{ PROJECT_PAPERS : allowlists
    PROJECTS ||--o{ PROJECT_PERSONAS : has
    PROJECTS ||--|| PROJECT_SETTINGS : configures

    AUTH_SESSIONS ||--o{ CHAT_HISTORY_TURNS : produces
    AUTH_USERS ||--o{ CHAT_HISTORY_TURNS : asks
    PROJECTS ||--o{ CHAT_HISTORY_TURNS : scopes

    AUTH_USERS ||--o{ PAPER_NOTES : writes
    PROJECTS ||--o{ PAPER_NOTES : scopes

    PROJECTS ||--o{ PAPER_COMPARISON_RUNS : owns
    PAPER_COMPARISON_RUNS ||--o{ PAPER_COMPARISON_CELLS : contains

    WORKFLOW_RUN_RECORDS ||--o{ TOKEN_USAGE : emits
    WORKFLOW_RUN_RECORDS ||--o{ REQUEST_FAILURES : may_log
    PROJECTS ||--o{ PROJECT_QUERY_CACHE : scopes

    AUTH_USERS {
      bigint id PK
      text username
      text email
      bool is_active
    }
    AUTH_SESSIONS {
      text session_id PK
      bigint user_id FK
      text current_project_id
      text current_persona_id
      timestamp authenticated_at
    }
    PROJECTS {
      text project_id PK
      text name
      text slug
      bool is_active
    }
    PROJECT_MEMBERSHIPS {
      bigint id PK
      text project_id FK
      bigint user_id FK
      text role
      bool is_active
    }
    PROJECT_PAPERS {
      bigint id PK
      text project_id FK
      text paper_id
      text paper_path
      bigint added_by_user_id
    }
    PROJECT_PERSONAS {
      text persona_id PK
      text project_id FK
      text name
      bool is_default
      bool is_active
    }
    PROJECT_SETTINGS {
      text project_id PK
      bool allow_cross_project_answer_reuse
      bool allow_custom_question_sharing
      text default_persona_id
    }
    CHAT_HISTORY_TURNS {
      bigint id PK
      bigint user_id FK
      text session_id FK
      text project_id FK
      text paper_id
      text query
      text answer
      timestamp created_at
    }
    PAPER_NOTES {
      bigint id PK
      bigint user_id FK
      text project_id FK
      text paper_id
      int page_number
      text note_text
    }
    PAPER_COMPARISON_RUNS {
      text comparison_id PK
      bigint created_by_user_id FK
      text project_id FK
      text status
      timestamp created_at
    }
    PAPER_COMPARISON_CELLS {
      bigint id PK
      text comparison_id FK
      text paper_id
      text question_id
      text cell_status
      text answer
    }
    WORKFLOW_RUN_RECORDS {
      bigint id PK
      text run_id
      text record_kind
      text step
      text status
      text project_id
      timestamp created_at
    }
    PROJECT_QUERY_CACHE {
      text project_cache_key PK
      text project_id FK
      text cache_key
      text query
      text answer
    }
    TOKEN_USAGE {
      bigint id PK
      text run_id
      text model
      int total_tokens
      timestamp created_at
    }
    REQUEST_FAILURES {
      bigint id PK
      text component
      text error
      timestamp created_at
    }
```

## Table Purpose Reference

The ERD uses concise logical names (for readability). The table list below explains what each table does in runtime behavior.

### Auth and tenancy

| ERD Table | Purpose | Key fields | Typical writes |
| --- | --- | --- | --- |
| `AUTH_USERS` | Account identity and activation state. | `id`, `username`, `email`, `is_active` | Registration, admin activation/deactivation, password updates. |
| `AUTH_SESSIONS` | Browser/API session tracking and current project/persona context. | `session_id`, `user_id`, `current_project_id`, `current_persona_id` | Login, logout/revoke, project/persona selection changes. |
| `PROJECTS` | Top-level workspace boundary for data and access control. | `project_id`, `name`, `slug`, `is_active` | Project create/update and soft-disable operations. |
| `PROJECT_MEMBERSHIPS` | User membership and role per project. | `project_id`, `user_id`, `role`, `is_active` | Invite/add member, role changes, membership revocation. |
| `PROJECT_PAPERS` | Paper allowlist for a project. | `project_id`, `paper_id`, `paper_path`, `added_by_user_id` | Add/remove paper mappings for scoped discovery. |
| `PROJECT_PERSONAS` | Project-specific assistant personas and defaults. | `persona_id`, `project_id`, `name`, `is_default`, `is_active` | Persona create/update/select and default toggles. |
| `PROJECT_SETTINGS` | Project-level behavior toggles. | `project_id`, `allow_cross_project_answer_reuse`, `allow_custom_question_sharing`, `default_persona_id` | Settings updates from admin/config UIs. |

### User-facing retrieval and analysis

| ERD Table | Purpose | Key fields | Typical writes |
| --- | --- | --- | --- |
| `CHAT_HISTORY_TURNS` | Persisted Q&A turns for chat history and replay. | `user_id`, `session_id`, `project_id`, `paper_id`, `query`, `answer`, `created_at` | Every completed chat turn (sync or stream-done event). |
| `PAPER_NOTES` | User annotations anchored to paper/page context. | `user_id`, `project_id`, `paper_id`, `page_number`, `note_text` | Note create/update/delete in the paper viewer. |
| `PAPER_COMPARISON_RUNS` | Header record for a multi-paper comparison matrix run. | `comparison_id`, `created_by_user_id`, `project_id`, `status`, `created_at` | Compare-run creation and status transitions. |
| `PAPER_COMPARISON_CELLS` | Per-paper, per-question cell outputs for a comparison run. | `comparison_id`, `paper_id`, `question_id`, `cell_status`, `answer` | Cache hits, generated fills, and per-cell failures. |
| `PROJECT_QUERY_CACHE` | Project-scoped cache indirection for reusable answers. | `project_cache_key`, `project_id`, `cache_key`, `query`, `answer` | Cache population during chat/structured processing. |

### Workflow and observability

| ERD Table | Purpose | Key fields | Typical writes |
| --- | --- | --- | --- |
| `WORKFLOW_RUN_RECORDS` | Canonical lineage table for run, stage, and question artifacts. | `run_id`, `record_kind`, `step`, `status`, `project_id`, `created_at` | Workflow stage progress, outputs, structured answers, report metadata. |
| `TOKEN_USAGE` | Token/cost telemetry used for usage dashboards and audits. | `run_id`, `model`, `total_tokens`, `created_at` | LLM call accounting during chat/workflow tasks. |
| `REQUEST_FAILURES` | Structured error log for operational debugging. | `component`, `error`, `created_at` | API/service failure handlers and retry diagnostics. |

### Relationship notes

- `PROJECTS` is the main scope boundary; most user-facing data is keyed directly or indirectly to a project.
- `WORKFLOW_RUN_RECORDS` is the primary lineage spine; other telemetry tables reference run context.
- `PAPER_COMPARISON_RUNS` and `PAPER_COMPARISON_CELLS` model a two-level matrix: run header plus per-cell materialization.
- `AUTH_SESSIONS` carries current project/persona pointers so each request resolves tenancy without re-selecting context.

## Core Schemas by Responsibility

- `auth`: users, sessions, projects, memberships, personas, settings, rate limits
- `workflow`: run records and async jobs
- `retrieval`: query cache and project query cache
- `observability`: token usage and failures
- `enrichment`: OpenAlex/CitEc metadata and citation graph cache
- `ingestion` and `indexing`: extracted documents, metadata, vectors, shard/version data

## Related Documents

- [System architecture](architecture.md)
- [Workflow architecture](workflow_architecture.md)
- [Workflow guide](../guides/workflow.md)
