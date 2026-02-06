Ragonometrics - RAG pipeline for economics papers
============================================

Overview
--------
Ragonometrics ingests PDFs, extracts per-page text for provenance, chunks with overlap, embeds chunks, indexes with FAISS, and serves retrieval + LLM summaries via CLI and a Streamlit UI. External metadata is enriched via Semantic Scholar and CitEc when available, and DOI metadata can be fetched from Crossref and cached. The system is designed to be reproducible, auditable, and scalable from local runs to a Postgres-backed deployment.

This repo is a combination of coding + vibe coding.

Quick Start
-----------
1. Install dependencies in your enviroment.

```bash
python -m pip install -e .
```

2. Install Poppler (provides `pdftotext` and `pdfinfo`). On Windows, add Poppler `bin` to PATH.

3. Set your OpenAI API key in the `.env` file.

4. Place PDFs in `papers/` (e.g., `papers/example.pdf`) or set `PAPERS_DIR`.

5. Run the summarizer.

```bash
python -m ragonometrics.core.main
```

Docs (GitHub)
-------------
Docs root: https://github.com/badbayesian/ragonometrics/tree/main/docs
- Architecture: https://github.com/badbayesian/ragonometrics/blob/main/docs/architecture.md
- Workflow Architecture: https://github.com/badbayesian/ragonometrics/blob/main/docs/workflow_architecture.md
- Configuration: https://github.com/badbayesian/ragonometrics/blob/main/docs/configuration.md
- Workflow and CLI: https://github.com/badbayesian/ragonometrics/blob/main/docs/workflow.md
- Docker: https://github.com/badbayesian/ragonometrics/blob/main/docs/docker.md
- Indexing and Retrieval: https://github.com/badbayesian/ragonometrics/blob/main/docs/indexing.md
- Streamlit UI: https://github.com/badbayesian/ragonometrics/blob/main/docs/ui.md
- Troubleshooting: https://github.com/badbayesian/ragonometrics/blob/main/docs/troubleshooting.md
- Agentic workflow: https://github.com/badbayesian/ragonometrics/blob/main/docs/agentic.md
- Econ schema: https://github.com/badbayesian/ragonometrics/blob/main/docs/econ_schema.md
- Cloud deployment: https://github.com/badbayesian/ragonometrics/blob/main/docs/cloud.md
- Onboarding: https://github.com/badbayesian/ragonometrics/blob/main/docs/onboarding.md
- Contributing: https://github.com/badbayesian/ragonometrics/blob/main/docs/contributing.md
- ADRs: https://github.com/badbayesian/ragonometrics/tree/main/docs/adr
