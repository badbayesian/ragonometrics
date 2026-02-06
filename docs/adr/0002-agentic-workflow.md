# ADR 0002: Workflow State + Async Queue

Date: 2026-02-06

Status
------
Accepted

Context
-------
The pipeline needs a repeatable, multi-step workflow with durable state and
optional asynchronous execution for long-running tasks.

Decision
--------
Implement a SQLite-backed workflow state store and provide a multi-step runner
with optional Redis/RQ execution.

Consequences
------------
- Reproducible workflow runs with per-step status and outputs.
- Async execution via RQ for background indexing and evaluation.
- Additional state DB file to manage in environments with strict persistence.
