# UI Guide

This guide covers the web app at `http://localhost:8590`.

## Access and Session

- Login supports `email or username`.
- Account actions available from login view:
  - register
  - forgot password
  - reset password
- Header controls may include:
  - project selector
  - persona selector
  - debug mode toggle
  - theme toggle
  - help modal

## Paper Finder

- `Find paper` is type-to-search.
- Suggestions are deduped by normalized title.
- Filters:
  - author
  - venue
  - year
- Behavior:
  - `Enter` selects top match
  - double-click opens the current filtered list

## Tabs

### Chat
- Ask and Ask (Stream) modes.
- Suggested questions.
- In-flight queue for additional prompts.
- Evidence panel with top citation snippets and viewer jump.
- Provenance badge and warnings.

### Paper Viewer
- In-app PDF viewing.
- Highlighting and notes tied to paper context.

### Structured Workstream
- Refresh cached structured answers.
- Generate missing answers.
- Export in compact or full formats (JSON/PDF).

### OpenAlex Metadata
- View linked paper metadata and entities.
- Apply manual OpenAlex link override when needed.

### Citation Network
- Interactive citation graph.
- Controls for max references, max citing, and hops.
- Debounced auto-reload and reset behavior.

### Usage
- Summary, by-model, and recent usage tables.
- Optional session-only scope toggle.

### Compare
- Cache-first multi-paper matrix.
- Fill missing cells.
- Export JSON/CSV.

## API Envelope

All `/api/v1` responses use:
- success: `{"ok": true, "data": ..., "request_id": "..."}`
- error: `{"ok": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}`
