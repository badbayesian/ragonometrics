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
Implement a Postgres-backed workflow state store and provide a multi-step runner
with optional asynchronous execution backed by Postgres queue records (`workflow.async_jobs`) consumed by `rq-worker`.

Consequences
------------
- Reproducible workflow runs with per-step status and outputs.
- Async execution via worker processes polling `workflow.async_jobs`.
- Unified operational storage in Postgres across workflow, retrieval cache, and observability.
