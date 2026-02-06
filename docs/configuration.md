# Configuration

This document describes how Ragonometrics loads configuration and which environment variables are supported.

Primary Config File
-------------------
`config.toml` is the primary configuration surface (see `config.toml` in the repo). Env vars override any value, which is useful in containers/CI. To point at a different file, set `RAG_CONFIG=/path/to/config.toml`.

Environment Variables (Overrides)
---------------------------------
- `PAPERS_DIR`: directory with PDFs. Default `PROJECT_ROOT/papers`.
- `MAX_PAPERS`: number of PDFs to process (default `3`).
- `MAX_WORDS`: maximum words per paper (default `12000`).
- `CHUNK_WORDS`: words per chunk (default `350`).
- `CHUNK_OVERLAP`: overlap between chunks (default `50`).
- `TOP_K`: number of chunks retrieved for context (default `6`).
- `EMBED_BATCH`: batch size for embeddings (default `64`).
- `EMBEDDING_MODEL`: embedding model name (default `text-embedding-3-small`).
- `OPENAI_MODEL` or `CHAT_MODEL`: chat model used for summaries (default `gpt-5-nano`).
- `LLM_MODELS`: comma-separated model list for Streamlit dropdown.
- `DATABASE_URL`: Postgres URL for metadata + hybrid retrieval.
- `BM25_WEIGHT`: blend weight for hybrid retrieval (default `0.5`).
- `RERANKER_MODEL`: optional LLM reranker model name.
- `RERANK_TOP_N`: number of candidates to rerank (default `30`).
- `QUERY_EXPANSION`: set to any value to enable query expansion.
- `QUERY_EXPAND_MODEL`: model override for query expansion.
- `FORCE_OCR`: set to any value to bypass `pdftotext` and use OCR (if available).
- `SECTION_AWARE_CHUNKING`: set to any value to enable section-aware chunking.
- `STREAMLIT_USERNAME`: optional username to protect the Streamlit app.
- `STREAMLIT_PASSWORD`: optional password to protect the Streamlit app.
- `SEMANTIC_SCHOLAR_API_KEY`: API key for Semantic Scholar (optional).
- `FRED_API_KEY`: API key for FRED time-series data (optional).
- `ECON_SERIES_IDS`: comma-separated FRED series ids for workflow econ step (optional).
- `WORKFLOW_AGENTIC`: set to `1` to enable agentic sub-question workflow.
- `WORKFLOW_QUESTION`: main question for the agentic workflow.
- `WORKFLOW_AGENTIC_MODEL`: optional model override for the agentic step.
- `WORKFLOW_AGENTIC_CITATIONS`: set to `1` to include citation extraction in the agentic step.
- `WORKFLOW_REPORT_QUESTIONS`: set to `0` to skip the structured report questions (default `1`).
- `WORKFLOW_REPORT_QUESTIONS_SET`: `structured` (A-D list), `agentic` (previous sub-questions), `both`, or `none`.
