# ADR 0001: Modular Package Layout

Date: 2026-02-06

Status
------
Accepted

Context
-------
Ragonometrics expanded beyond a single pipeline into multiple subsystems:
ingestion, retrieval, indexing, integrations, and UI. The original layout made
dependencies ambiguous and complicated tooling.

Decision
--------
Adopt a modular package layout:

- `core/` for ingestion + configuration
- `pipeline/` for LLM orchestration + caching
- `indexing/` for FAISS + Postgres metadata
- `integrations/` for external APIs + queues
- `ui/` for Streamlit
- `eval/` for evaluation and benchmarking

Consequences
------------
Clearer ownership boundaries, easier tests, and predictable imports, at the cost
of additional module wiring and refactors.
