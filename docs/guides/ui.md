# UI Guide

## Streamlit UI

Run UI locally:

```bash
ragonometrics ui
```

Docker UI URL:
- `http://localhost:8585`

Tabs
----
- `Chat`: retrieval + grounded answers with provenance snapshots.
- `Structured Workstream`: cached structured questions, generation controls, export (`Compact`/`Full`).
- `OpenAlex Metadata`: matched OpenAlex payload and bibliographic details.
- `Citation Network`: OpenAlex citation neighborhood visualization.
- `Usage`: token usage and model usage summaries.

Structured Workstream
---------------------
The tab reads/writes structured answers in `workflow.run_records` and supports:
- cache scope: `Selected model only` or `Any model`
- export scope: filtered subset or full question set
- export format:
  - `Compact`: minimal row shape
  - `Full`: includes structured fields such as `confidence_score`, `retrieval_method`, `citation_anchors`, and workflow-record metadata when present

If a paper has compact-only history, use:
- `Regenerate Missing Full Fields (Export Scope)` in the UI, or
- `python tools/backfill_structured_question_fields.py --db-url "$DATABASE_URL" --apply`.

Authentication
--------------
Credential precedence:
1. active DB users in `auth.streamlit_users`
2. env fallback (`STREAMLIT_USERS_JSON` or `STREAMLIT_USERNAME`/`STREAMLIT_PASSWORD`)

If DB auth tables are empty, env credentials can auto-bootstrap when
`STREAMLIT_AUTH_BOOTSTRAP_FROM_ENV=1`.

Notes
-----
- Math/function formatting review pass can render Markdown-friendly LaTeX.
- Optional page snapshots require `pdf2image` + Poppler; OCR highlighting uses `pytesseract`.
- OpenAlex and CitEc metadata are injected as auxiliary answer context when available.

## Flask + React Web
Incremental migration adds a Flask API and React SPA while Streamlit remains available.

Run locally:

```bash
ragonometrics web --host 0.0.0.0 --port 8590
```

Or in Docker:

```bash
docker compose --profile web up -d --build web
```

Base URL:
- `http://localhost:8590`

### Current Web UX
- Login supports `email or username`.
- Create-account and forgot/reset-password flows are available on the login screen.
- Top bar includes:
  - `Project` selector (if multiple projects are available)
  - `Persona` selector (if enabled for selected project)
  - `Debug: On/Off` toggle
  - `Dark Mode/Light Mode` toggle
  - `Help / How To` modal
- Debug-only tabs:
  - `Workflow Cache`
  - `Cache Inspector`

### Paper Finder
- `Find paper` is type-to-search with datalist suggestions.
- Finder options are deduped by normalized title to avoid repeated entries.
- Filter fields:
  - `Author`
  - `Venue`
  - `Year`
- Finder behavior:
  - `Enter` picks the top current match
  - double-click opens the list for current filtered papers (without clearing active filters)

### Web Tabs
- `Chat`
  - suggested questions
  - `Ask` and `Ask (Stream)` modes
  - queue support while a request is in flight
  - server-side history restore and clear
  - evidence panel with top 3 citations and open-in-viewer link
  - provenance badge + warning count
- `Paper Viewer`
  - in-app PDF view
  - highlight and notes workflow
- `Structured Workstream`
  - refresh cache
  - generate missing
  - compact/full export (JSON/PDF)
- `OpenAlex Metadata`
  - linked paper/source/topic/concept/author entities
  - manual OpenAlex link override (`Apply Link`)
- `Citation Network`
  - interactive graph
  - controls: `Max references`, `Max citing`, `Hops`
  - debounced auto-reload after control changes
  - reset button
  - defaults: references `10`, citing `10`, hops `1`
- `Usage`
  - account-scoped by default
  - optional session-only toggle
  - summary + by-model + recent usage tables
- `Compare`
  - cache-first multi-paper comparison runs
  - fill-missing generation
  - JSON/CSV export

API contracts:
- Success envelope: `{"ok": true, "data": ..., "request_id": "..."}`
- Error envelope: `{"ok": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}`

Core endpoints:
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/projects/{project_id}/select`
- `POST /api/v1/projects/{project_id}/personas/{persona_id}/select`
- `GET /api/v1/papers`
- `GET /api/v1/chat/suggestions`
- `GET /api/v1/chat/history`
- `DELETE /api/v1/chat/history`
- `POST /api/v1/chat/turn`
- `POST /api/v1/chat/turn-stream` (NDJSON)
- `GET /api/v1/openalex/metadata`
- `POST /api/v1/openalex/metadata/manual-link`
- `GET /api/v1/openalex/citation-network`
- `GET /api/v1/structured/questions`
- `GET /api/v1/structured/answers`
- `POST /api/v1/structured/generate`
- `POST /api/v1/structured/generate-missing`
- `POST /api/v1/structured/export`
- `GET /api/v1/usage/summary`
- `GET /api/v1/usage/by-model`
- `GET /api/v1/usage/recent`
- `GET /api/v1/compare/similar-papers`
- `POST /api/v1/compare/runs`
- `GET /api/v1/compare/runs`
- `GET /api/v1/compare/runs/<comparison_id>`
- `POST /api/v1/compare/runs/<comparison_id>/fill-missing`
- `POST /api/v1/compare/runs/<comparison_id>/export`

Compare (Web + CLI)
-------------------
The Compare tab supports `2..10` papers and custom question sets (`1..50`) to build a
cache-first matrix:
- `cached`: answer loaded from `retrieval.query_cache`
- `missing`: no cache hit yet
- `generated`: populated by explicit `Fill Missing`
- `failed`: generation failed; error retained in cell metadata

Runs are persisted in:
- `retrieval.paper_comparison_runs`
- `retrieval.paper_comparison_cells`

CLI commands:
- `ragonometrics compare suggest --paper-id <id> [--limit 20]`
- `ragonometrics compare create --paper-id <id>... --question \"...\"... [--name ...] [--model ...]`
- `ragonometrics compare show --comparison-id <id>`
- `ragonometrics compare fill-missing --comparison-id <id> [--paper-id ...] [--question-id ...]`
- `ragonometrics compare export --comparison-id <id> --format json|csv --out <path>`

OpenAlex Citation Network
-------------------------
`GET /api/v1/openalex/citation-network` supports:
- `paper_id` (required)
- `max_references` (optional, default `10`)
- `max_citing` (optional, default `10`)
- `n_hops` (optional, default `1`, clamped to `1..5`)

Response includes cache metadata:
- `cache.status`: `fresh_hit|stale_hit|miss_or_hard_expired`
- `cache.generated_at`, `cache.expires_at`, `cache.stale_until`
- `cache.refresh_enqueued`
