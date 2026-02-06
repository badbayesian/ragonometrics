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

- [`ragonometrics/core/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/core) for ingestion + configuration
- [`ragonometrics/pipeline/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/pipeline) for LLM orchestration + caching
- [`ragonometrics/indexing/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/indexing) for FAISS + Postgres metadata
- [`ragonometrics/integrations/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/integrations) for external APIs + queues
- [`ragonometrics/ui/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/ui) for Streamlit
- [`ragonometrics/eval/`](https://github.com/badbayesian/ragonometrics/tree/main/ragonometrics/eval) for evaluation and benchmarking

Consequences
------------
Clearer ownership boundaries, easier tests, and predictable imports, at the cost
of additional module wiring and refactors.
