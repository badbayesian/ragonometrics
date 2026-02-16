# Audit Report: Workflow `60658975eb134d069c52a1f7f9a41d53`

## Overview
- Source JSON: `reports\workflow-report-60658975eb134d069c52a1f7f9a41d53.json`
- Run ID: `60658975eb134d069c52a1f7f9a41d53`
- Papers input: `papers\Use_of_Cumulative_Sums_of_Squares_for_Re.pdf`
- Started at: `2026-02-16T05:31:11.484326+00:00`
- Finished at: `2026-02-16T05:31:16.586316+00:00`
- Duration: `0:00:05.101990`

## Effective Configuration
- Chat model: `gpt-5-nano`
- Embedding model: `text-embedding-3-large`
- Top K: `10`
- Chunk words / overlap: `350` / `75`
- Batch size: `64`
- Database URL configured: `True`

## Step Outcomes
- `prep`: `completed`
- `ingest`: `num_pdfs=1, num_papers=1`
- `enrich`: `openalex=0, citec=0`
- `econ_data`: `fetched`
- `agentic`: `skipped`
- `index`: `skipped (reason: `db_unreachable`)`
- `report_store`: `pending`

## Agentic Summary
- Status: `skipped`
- Main question: n/a
- Report question set: `n/a`
- Structured questions generated: `0`
- Confidence mean/median: `n/a` / `n/a`
- Confidence labels: low=0, medium=0, high=0

### Final Answer

n/a

### Sub-Answers

_No sub-answers recorded._

## Structured Q&A Appendix

This section mirrors `agentic.report_questions` for audit traceability.

_No structured report questions found._
