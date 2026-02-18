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
