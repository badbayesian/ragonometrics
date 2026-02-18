# Streamlit UI Guide

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

Flask + React Web Guide
-----------------------
Incremental migration adds a Flask API and React SPA while Streamlit remains available.

Run locally:

```bash
ragonometrics web --host 0.0.0.0 --port 8590
```

Or in Docker:

```bash
docker compose up -d --build web
```

Base URL:
- `http://localhost:8590`

API contracts:
- Success envelope: `{"ok": true, "data": ..., "request_id": "..."}`
- Error envelope: `{"ok": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}`

Core endpoints:
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET /api/v1/papers`
- `GET /api/v1/chat/suggestions`
- `GET /api/v1/chat/history`
- `DELETE /api/v1/chat/history`
- `POST /api/v1/chat/turn`
- `POST /api/v1/chat/turn-stream` (NDJSON)
- `GET /api/v1/openalex/metadata`
- `GET /api/v1/openalex/citation-network`
- `GET /api/v1/structured/questions`
- `GET /api/v1/structured/answers`
- `POST /api/v1/structured/generate`
- `POST /api/v1/structured/generate-missing`
- `POST /api/v1/structured/export`
- `GET /api/v1/usage/summary`
- `GET /api/v1/usage/by-model`
- `GET /api/v1/usage/recent`
