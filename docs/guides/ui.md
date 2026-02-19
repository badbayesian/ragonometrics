# UI Guide

This guide covers the web app at `http://localhost:8590`.

## Access and Session

- Login supports either email or username.
- Account actions from login:
  - register
  - forgot password
  - reset password
- Header controls may include project and persona selectors.

## Paper Selection

- `Find paper` supports type-to-search.
- Suggestions are deduped by normalized title.
- Filters include author, venue, and year.

## Main Tabs

### Chat

- `Ask` and `Ask (Stream)` modes
- Suggested prompts
- In-flight queue for additional prompts while prior asks are running
- Evidence panel with citations and PDF viewer jumps
- Provenance badge with score details

### Paper Viewer

- In-app PDF viewing
- Highlights and notes scoped to the selected paper

### Structured Workstream

- Refresh cached structured answers
- Generate only missing answers
- Export compact or full output in JSON/PDF

### OpenAlex Metadata

- View linked metadata and entities
- Apply manual OpenAlex link override if needed

### Citation Network

- Interactive citation graph
- Controls for references, citing count, and hop depth

### Usage

- Summary, by-model, and recent usage tables
- Optional session-only scope

### Compare

- Cache-first multi-paper comparison matrix
- Fill missing cells
- Export JSON/CSV

## API Envelope

All `/api/v1` responses use a typed envelope:

- success: `{"ok": true, "data": ..., "request_id": "..."}`
- error: `{"ok": false, "error": {"code": "...", "message": "..."}, "request_id": "..."}`

## Related Documents

- [Workflow guide](workflow.md)
- [System architecture](../architecture/architecture.md)
- [Docker deployment](../deployment/docker.md)
