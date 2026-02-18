BEGIN;

ALTER TABLE retrieval.chat_history_turns
    ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE retrieval.chat_history_turns
    ADD COLUMN IF NOT EXISTS persona_id TEXT;

ALTER TABLE retrieval.paper_notes
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE workflow.run_records
    ADD COLUMN IF NOT EXISTS persona_id TEXT;

ALTER TABLE retrieval.paper_comparison_runs
    ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE retrieval.paper_comparison_runs
    ADD COLUMN IF NOT EXISTS persona_id TEXT;

ALTER TABLE retrieval.paper_comparison_cells
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE observability.token_usage
    ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE observability.token_usage
    ADD COLUMN IF NOT EXISTS persona_id TEXT;

UPDATE retrieval.chat_history_turns
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

UPDATE retrieval.paper_notes
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

UPDATE workflow.run_records
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

UPDATE retrieval.paper_comparison_runs
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

UPDATE retrieval.paper_comparison_cells
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

UPDATE observability.token_usage
SET project_id = 'default-shared'
WHERE COALESCE(project_id, '') = '';

CREATE INDEX IF NOT EXISTS retrieval_chat_history_project_user_paper_idx
    ON retrieval.chat_history_turns(project_id, user_id, paper_id, created_at DESC);
CREATE INDEX IF NOT EXISTS retrieval_chat_history_project_session_paper_idx
    ON retrieval.chat_history_turns(project_id, session_id, paper_id, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_paper_notes_project_user_paper_page_idx
    ON retrieval.paper_notes(project_id, user_id, paper_id, page_number, created_at DESC);

CREATE INDEX IF NOT EXISTS workflow_run_records_project_run_kind_idx
    ON workflow.run_records(project_id, run_id, record_kind, step);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_runs_project_created_idx
    ON retrieval.paper_comparison_runs(project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS retrieval_paper_comparison_cells_project_cmp_idx
    ON retrieval.paper_comparison_cells(project_id, comparison_id, question_id, paper_id);

CREATE INDEX IF NOT EXISTS observability_token_usage_project_created_idx
    ON observability.token_usage(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS observability_token_usage_project_session_idx
    ON observability.token_usage(project_id, session_id);

COMMIT;

