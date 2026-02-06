# Onboarding Guide

Welcome! This guide gets you productive quickly and establishes expectations for
quality and documentation.

Setup
-----
1. Install dependencies: `python -m pip install -e .[dev]`
2. Install Poppler (pdftotext/pdfinfo) and ensure it is on PATH
3. Configure `.env` with `OPENAI_API_KEY` and optional `DATABASE_URL`

First Tasks
-----------
- Run tests: `pytest`
- Start the UI: `ragonometrics ui`
- Run a workflow: `ragonometrics workflow --papers-dir papers/`

Engineering Expectations
------------------------
- Add or update tests for any behavior change.
- Document new modules in `docs/` and update `docs/architecture/architecture.md`.
- Keep schema changes backward-compatible or add migration notes.

Knowledge Sharing
-----------------
- Weekly 30-minute walkthrough of recent changes.
- Pairing on complex PRs.
- Short architecture notes for meaningful decisions (see `docs/adr/`).
