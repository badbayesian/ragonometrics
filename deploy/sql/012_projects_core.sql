BEGIN;

CREATE TABLE IF NOT EXISTS auth.projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id BIGINT REFERENCES auth.streamlit_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(project_id)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(slug)) > 0)
);
CREATE INDEX IF NOT EXISTS auth_projects_active_idx
    ON auth.projects(is_active, updated_at DESC);

CREATE TABLE IF NOT EXISTS auth.project_memberships (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES auth.projects(project_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES auth.streamlit_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (role IN ('owner', 'editor', 'viewer')),
    UNIQUE (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS auth_project_memberships_user_idx
    ON auth.project_memberships(user_id, is_active, updated_at DESC);

CREATE TABLE IF NOT EXISTS auth.project_papers (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES auth.projects(project_id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL,
    paper_path TEXT NOT NULL,
    added_by_user_id BIGINT REFERENCES auth.streamlit_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, paper_id)
);
CREATE INDEX IF NOT EXISTS auth_project_papers_project_path_idx
    ON auth.project_papers(project_id, paper_path);

CREATE TABLE IF NOT EXISTS auth.project_personas (
    persona_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES auth.projects(project_id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    system_prompt_suffix TEXT NOT NULL DEFAULT '',
    default_model TEXT NOT NULL DEFAULT '',
    retrieval_profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(persona_id)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (length(trim(slug)) > 0),
    UNIQUE (project_id, slug)
);
CREATE INDEX IF NOT EXISTS auth_project_personas_project_idx
    ON auth.project_personas(project_id, is_active, updated_at DESC);

CREATE TABLE IF NOT EXISTS auth.project_settings (
    project_id TEXT PRIMARY KEY REFERENCES auth.projects(project_id) ON DELETE CASCADE,
    allow_cross_project_answer_reuse BOOLEAN NOT NULL DEFAULT TRUE,
    allow_custom_question_sharing BOOLEAN NOT NULL DEFAULT FALSE,
    default_persona_id TEXT REFERENCES auth.project_personas(persona_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE auth.streamlit_sessions
    ADD COLUMN IF NOT EXISTS current_project_id TEXT;
ALTER TABLE auth.streamlit_sessions
    ADD COLUMN IF NOT EXISTS current_persona_id TEXT;

CREATE INDEX IF NOT EXISTS auth_streamlit_sessions_current_project_idx
    ON auth.streamlit_sessions(current_project_id, authenticated_at DESC);

INSERT INTO auth.projects (project_id, name, slug, is_active, created_at, updated_at)
SELECT 'default-shared', 'Default Shared Project', 'default-shared', TRUE, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM auth.projects WHERE project_id = 'default-shared'
);

INSERT INTO auth.project_personas
    (persona_id, project_id, slug, name, system_prompt_suffix, default_model, retrieval_profile_json, is_default, is_active, created_at, updated_at)
SELECT
    'default-shared-persona',
    'default-shared',
    'default',
    'Default Persona',
    '',
    '',
    '{}'::jsonb,
    TRUE,
    TRUE,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM auth.project_personas WHERE persona_id = 'default-shared-persona'
);

INSERT INTO auth.project_settings
    (project_id, allow_cross_project_answer_reuse, allow_custom_question_sharing, default_persona_id, updated_at)
SELECT
    'default-shared',
    TRUE,
    FALSE,
    'default-shared-persona',
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM auth.project_settings WHERE project_id = 'default-shared'
);

INSERT INTO auth.project_memberships
    (project_id, user_id, role, is_active, created_at, updated_at)
SELECT
    'default-shared',
    u.id,
    'owner',
    TRUE,
    NOW(),
    NOW()
FROM auth.streamlit_users u
WHERE NOT EXISTS (
    SELECT 1
    FROM auth.project_memberships pm
    WHERE pm.project_id = 'default-shared'
      AND pm.user_id = u.id
);

UPDATE auth.streamlit_sessions
SET current_project_id = COALESCE(NULLIF(current_project_id, ''), 'default-shared')
WHERE COALESCE(current_project_id, '') = '';

UPDATE auth.streamlit_sessions
SET current_persona_id = COALESCE(NULLIF(current_persona_id, ''), 'default-shared-persona')
WHERE COALESCE(current_persona_id, '') = '';

COMMIT;

