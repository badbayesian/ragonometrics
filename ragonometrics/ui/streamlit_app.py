"""Streamlit UI for interactive RAG over papers with citations and usage tracking."""

from __future__ import annotations

import os
import time
import math
import json
import base64
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from dataclasses import replace
import hashlib
import hmac
import html
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st
import streamlit.components.v1 as components

from ragonometrics.db.connection import connect, pooled_connection
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.core.main import (
    Settings,
    Paper,
    embed_texts,
    load_papers,
    load_settings,
    prepare_chunks_for_paper,
    top_k_context,
)
from ragonometrics.integrations.openalex import format_openalex_context, request_json as openalex_request_json
from ragonometrics.integrations.citec import format_citec_context
from ragonometrics.core.prompts import MATH_LATEX_REVIEW_PROMPT, RESEARCHER_QA_PROMPT
from ragonometrics.pipeline import call_llm
from ragonometrics.pipeline.query_cache import DEFAULT_CACHE_PATH, get_cached_answer, make_cache_key, set_cached_answer
from ragonometrics.pipeline.token_usage import DEFAULT_USAGE_DB, get_recent_usage, get_usage_by_model, get_usage_summary, record_usage

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None

try:
    import pytesseract
    from PIL import ImageDraw
except Exception:
    pytesseract = None
    ImageDraw = None


st.set_page_config(page_title="Ragonometrics Chat", layout="wide")

_QUERY_TIMINGS_KEY = "ui_query_timings"
_INVALID_STRUCTURED_QUESTION_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)


def list_papers(papers_dir: Path) -> List[Path]:
    """List PDF files in the provided directory.

    Args:
        papers_dir (Path): Directory containing input paper files.

    Returns:
        List[Path]: List result produced by the operation.
    """
    if not papers_dir.exists():
        return []
    return sorted(papers_dir.glob("*.pdf"))


def _reset_query_timings() -> None:
    """Reset per-render query timing rows."""
    st.session_state[_QUERY_TIMINGS_KEY] = []


def _record_query_timing(label: str, elapsed_ms: float) -> None:
    """Append one query timing row for optional debug rendering.

    Args:
        label (str): Short query label.
        elapsed_ms (float): Elapsed milliseconds for the query call.
    """
    rows = st.session_state.setdefault(_QUERY_TIMINGS_KEY, [])
    rows.append({"query": label, "elapsed_ms": round(float(elapsed_ms), 2)})


def _timed_call(label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a callable and record elapsed time in session state.

    Args:
        label (str): Short query label shown in debug timings.
        fn (Callable[..., Any]): Callable to execute.
        *args (Any): Positional args for ``fn``.
        **kwargs (Any): Keyword args for ``fn``.

    Returns:
        Any: Callable return value.
    """
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    _record_query_timing(label, (time.perf_counter() - start) * 1000.0)
    return result


@st.cache_data(ttl=900, show_spinner=False)
def _db_openalex_metadata_for_paper(path: Path) -> Optional[Dict[str, Any]]:
    """Load OpenAlex metadata for a paper from Postgres enrichment table.

    Args:
        path (Path): Paper path selected in the UI.

    Returns:
        Optional[Dict[str, Any]]: Stored OpenAlex payload when available.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return None

    normalized_path = str(path).replace("\\", "/")
    basename_suffix = f"%/{path.name.lower()}"
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT openalex_json
                FROM enrichment.paper_openalex_metadata
                WHERE match_status = 'matched'
                  AND (
                        lower(replace(paper_path, '\\', '/')) = lower(%s)
                     OR lower(replace(paper_path, '\\', '/')) LIKE %s
                  )
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized_path, basename_suffix),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0]
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    return None
                return parsed if isinstance(parsed, dict) else None
            return None
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def load_and_prepare(path: Path, settings: Settings):
    """Load a paper, prepare chunks/embeddings, and cache the result.

    Args:
        path (Path): Filesystem path value.
        settings (Settings): Loaded application settings.

    Returns:
        Any: Return value produced by the operation.
    """
    papers = load_papers([path])
    paper = papers[0]
    if not isinstance(paper.openalex, dict) or not paper.openalex:
        db_openalex = _db_openalex_metadata_for_paper(path)
        if isinstance(db_openalex, dict) and db_openalex:
            paper = replace(paper, openalex=db_openalex)
    chunks = prepare_chunks_for_paper(paper, settings)
    if not chunks:
        return paper, [], []
    client = build_llm_runtime(settings)
    chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
    embeddings = embed_texts(client, chunk_texts, settings.embedding_model, settings.batch_size)
    return paper, chunks, embeddings


def parse_context_chunks(context: str) -> List[dict]:
    """Parse concatenated context into structured chunks.

    Args:
        context (str): Input value for context.

    Returns:
        List[dict]: Dictionary containing the computed result payload.
    """
    chunks: List[dict] = []
    for block in context.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        meta = None
        text = block
        page: Optional[int] = None
        start_word: Optional[int] = None
        end_word: Optional[int] = None
        section: Optional[str] = None
        if block.startswith("(page "):
            parts = block.split("\n", 1)
            meta = parts[0].strip()
            text = parts[1].strip() if len(parts) > 1 else ""
            m = re.search(r"\(page\s+(\d+)\b", meta)
            if m:
                try:
                    page = int(m.group(1))
                except ValueError:
                    page = None
            m_words = re.search(r"\bwords\s+(\d+)-(\d+)\b", meta)
            if m_words:
                try:
                    start_word = int(m_words.group(1))
                    end_word = int(m_words.group(2))
                except ValueError:
                    start_word = None
                    end_word = None
            m_section = re.search(r"\bsection\s+(.+?)\)$", meta)
            if m_section:
                section = str(m_section.group(1) or "").strip() or None
        chunks.append(
            {
                "meta": meta,
                "text": text,
                "page": page,
                "start_word": start_word,
                "end_word": end_word,
                "section": section,
            }
        )
    return chunks


def _streamlit_confidence_from_retrieval_stats(stats: Dict[str, Any]) -> Tuple[str, float, str]:
    """Compute coarse confidence labels from retrieval stats."""
    method = str(stats.get("method") or "unknown")
    raw_score = stats.get("score_mean_norm")
    if not isinstance(raw_score, (int, float)):
        raw_score = stats.get("score_mean")
    try:
        score = float(raw_score)
    except Exception:
        score = 0.0
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    if score >= 0.75:
        label = "high"
    elif score >= 0.45:
        label = "medium"
    else:
        label = "low"
    return label, score, method


def _streamlit_structured_fields_from_context(
    *,
    question: str,
    answer: str,
    context: str,
    retrieval_stats: Dict[str, Any],
    top_k: int,
) -> Dict[str, Any]:
    """Build structured-question metadata for Streamlit-generated answers."""
    parsed_chunks = parse_context_chunks(context)
    confidence, confidence_score, retrieval_method = _streamlit_confidence_from_retrieval_stats(retrieval_stats)
    anchors = []
    for chunk in parsed_chunks[: max(1, int(top_k))]:
        anchors.append(
            {
                "page": chunk.get("page"),
                "start_word": chunk.get("start_word"),
                "end_word": chunk.get("end_word"),
                "section": chunk.get("section"),
                "note": None,
            }
        )
    quote_snippet = ""
    if parsed_chunks:
        quote_snippet = str(parsed_chunks[0].get("text") or "").strip()[:200]
    return {
        "question_tokens_estimate": len([token for token in str(question or "").split() if token]),
        "evidence_type": "retrieved_context",
        "confidence": confidence,
        "confidence_score": confidence_score,
        "retrieval_method": retrieval_method,
        "citation_anchors": anchors,
        "quote_snippet": quote_snippet,
        "table_figure": None,
        "data_source": None,
        "assumption_flag": None,
        "assumption_notes": None,
        "related_questions": [],
        "answer_length_chars": len(str(answer or "")),
    }


def build_chat_history_context(history: List[dict], *, paper_path: Path, max_turns: int = 6, max_answer_chars: int = 800) -> str:
    """Build a compact conversation transcript for prompt grounding.

    Args:
        history (List[dict]): Mapping containing history.
        paper_path (Path): Path to a single paper file.
        max_turns (int): Input value for max turns.
        max_answer_chars (int): Input value for max answer chars.

    Returns:
        str: Computed string result.
    """
    turns: List[tuple[str, str]] = []
    for item in history:
        if isinstance(item, tuple):
            if len(item) >= 2:
                q = str(item[0] or "").strip()
                a = str(item[1] or "").strip()
                if q and a:
                    turns.append((q, a))
            continue

        if not isinstance(item, dict):
            continue
        item_paper_path = item.get("paper_path")
        if item_paper_path and Path(item_paper_path) != paper_path:
            continue
        q = str(item.get("query") or "").strip()
        a = str(item.get("answer") or "").strip()
        if not q or not a:
            continue
        turns.append((q, a))

    if not turns:
        return ""

    selected = turns[-max_turns:]
    lines: List[str] = []
    for idx, (q, a) in enumerate(selected, start=1):
        answer_excerpt = a if len(a) <= max_answer_chars else a[:max_answer_chars].rstrip() + "..."
        lines.append(f"User {idx}: {q}")
        lines.append(f"Assistant {idx}: {answer_excerpt}")
    return "\n".join(lines)


def suggested_paper_questions(paper: Paper) -> List[str]:
    """Return a concise list of starter questions for the selected paper.

    Args:
        paper (Paper): Input value for paper.

    Returns:
        List[str]: List result produced by the operation.
    """
    questions = [
        "What is the main research question of this paper?",
        "What identification strategy does the paper use?",
        "What dataset and sample period are used?",
        "What are the key quantitative findings?",
        "What are the main limitations and caveats?",
        "What policy implications follow from the results?",
    ]
    if paper.title:
        questions[0] = f'What is the main research question in "{paper.title}"?'
    return questions


@st.cache_data(ttl=3600, show_spinner=False)
def _structured_report_questions() -> List[Dict[str, str]]:
    """Load canonical structured report questions from workflow module.

    Returns:
        List[Dict[str, str]]: Question definitions with ``id``, ``category``, ``question``.
    """
    # Lazy import avoids startup overhead/cycles until this tab is opened.
    from ragonometrics.pipeline.workflow import _build_report_questions

    return _build_report_questions()


def _normalize_question_key(value: Any) -> str:
    """Normalize question text for stable cache-map lookups."""
    return " ".join(str(value or "").strip().split())


def _is_valid_structured_question_text(value: Any) -> bool:
    """Return whether a structured question text looks valid for persistence."""
    text = _normalize_question_key(value)
    if not text:
        return False
    if len(text) > 600:
        return False
    for pattern in _INVALID_STRUCTURED_QUESTION_PATTERNS:
        if pattern.search(text):
            return False
    return True


def _structured_payload_from_question_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the structured payload object from a question record."""
    if not isinstance(record, dict):
        return {}
    output_obj = record.get("output_json") if isinstance(record.get("output_json"), dict) else {}
    if output_obj:
        return output_obj
    payload_obj = record.get("payload_json") if isinstance(record.get("payload_json"), dict) else {}
    return payload_obj


def _payload_has_full_structured_fields(payload: Optional[Dict[str, Any]]) -> bool:
    """Return whether a structured payload contains full-report fields."""
    if not isinstance(payload, dict) or not payload:
        return False
    anchors = payload.get("citation_anchors")
    has_anchors = isinstance(anchors, list)
    has_confidence_score = payload.get("confidence_score") is not None
    has_retrieval_method = bool(str(payload.get("retrieval_method") or "").strip())
    return has_anchors and has_confidence_score and has_retrieval_method


def _streamlit_structured_run_id(*, paper_path: str, model: str) -> str:
    """Return deterministic run_id used for streamlit structured cache rows."""
    key = f"{paper_path}||{model}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"streamlit-structured-{digest}"


def _upsert_workflow_structured_answer(
    *,
    paper_path: Path,
    selected_model: str,
    question_id: str,
    category: str,
    question: str,
    answer: str,
    structured_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Persist one structured answer into ``workflow.run_records``.

    Returns:
        Dict[str, str]: Metadata including ``run_id`` and ``question_id`` when persisted.
    """
    if not _is_valid_structured_question_text(question):
        return {}

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}

    normalized_path = str(paper_path).replace("\\", "/")
    run_id = _streamlit_structured_run_id(paper_path=normalized_path, model=selected_model)
    question_id_clean = str(question_id or "").strip()
    if not question_id_clean:
        question_id_clean = hashlib.sha256(_normalize_question_key(question).encode("utf-8")).hexdigest()[:10]
    created_at = datetime.now(timezone.utc).isoformat()

    run_payload = {"source": "streamlit_structured_workstream", "cache": "workflow.run_records"}
    run_meta = {"source": "streamlit_structured_workstream", "model": selected_model}
    question_payload = {
        "id": question_id_clean,
        "category": str(category or ""),
        "question": str(question or ""),
        "answer": str(answer or ""),
        "source": "workflow.run_records",
        "model": selected_model,
        "cached_at": created_at,
    }
    if isinstance(structured_fields, dict):
        for key in [
            "question_tokens_estimate",
            "evidence_type",
            "confidence",
            "confidence_score",
            "retrieval_method",
            "citation_anchors",
            "quote_snippet",
            "table_figure",
            "data_source",
            "assumption_flag",
            "assumption_notes",
            "related_questions",
            "answer_length_chars",
        ]:
            if key in structured_fields:
                question_payload[key] = structured_fields.get(key)
    question_meta = {
        "source": "streamlit_structured_workstream",
        "category": str(category or ""),
        "model": selected_model,
    }

    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO workflow.run_records
                (
                    run_id, record_kind, step, record_key,
                    status, papers_dir, arm, trigger_source,
                    config_effective_json, report_question_set,
                    started_at, finished_at, created_at, updated_at,
                    payload_json, metadata_json
                )
                VALUES (
                    %s, 'run', '', 'main',
                    'completed', %s, %s, %s,
                    %s::jsonb, %s,
                    %s, %s, NOW(), NOW(),
                    %s::jsonb, %s::jsonb
                )
                ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    papers_dir = COALESCE(EXCLUDED.papers_dir, workflow.run_records.papers_dir),
                    arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
                    trigger_source = COALESCE(EXCLUDED.trigger_source, workflow.run_records.trigger_source),
                    config_effective_json = EXCLUDED.config_effective_json,
                    report_question_set = COALESCE(EXCLUDED.report_question_set, workflow.run_records.report_question_set),
                    started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
                    finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
                    payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
                    metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    run_id,
                    normalized_path,
                    selected_model,
                    "streamlit_structured_workstream",
                    json.dumps({"chat_model": selected_model}, ensure_ascii=False),
                    "structured",
                    created_at,
                    created_at,
                    json.dumps(run_payload, ensure_ascii=False),
                    json.dumps(run_meta, ensure_ascii=False),
                ),
            )

            cur.execute(
                """
                INSERT INTO workflow.run_records
                (
                    run_id, record_kind, step, record_key, status,
                    question_id, report_question_set, created_at, updated_at,
                    payload_json, metadata_json
                )
                VALUES (
                    %s, 'question', 'agentic', %s, %s,
                    %s, %s, NOW(), NOW(),
                    %s::jsonb, %s::jsonb
                )
                ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    question_id = EXCLUDED.question_id,
                    report_question_set = COALESCE(EXCLUDED.report_question_set, workflow.run_records.report_question_set),
                    payload_json = EXCLUDED.payload_json,
                    metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    run_id,
                    question_id_clean,
                    "cached",
                    question_id_clean,
                    "structured",
                    json.dumps(question_payload, ensure_ascii=False),
                    json.dumps(question_meta, ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception:
        return {}

    return {"run_id": run_id, "question_id": question_id_clean, "created_at": created_at}


@st.cache_data(ttl=600, show_spinner=False)
def _db_cached_answers_for_paper(path: Path, model: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """Load latest cached answers for a paper from Postgres query cache.

    Args:
        path (Path): Selected paper path.
        model (Optional[str]): Optional model filter.

    Returns:
        Dict[str, Dict[str, str]]: Mapping ``question -> {answer, model, created_at}``.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}

    normalized_path = str(path).replace("\\", "/")
    basename_suffix = f"%/{path.name.lower()}"
    rows: Dict[str, Dict[str, str]] = {}
    latest_ts_by_key: Dict[str, str] = {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            sql = """
                SELECT DISTINCT ON (qc.query)
                    qc.query,
                    qc.answer,
                    qc.model,
                    qc.created_at
                FROM retrieval.query_cache qc
                WHERE (
                        lower(replace(qc.paper_path, '\\', '/')) = lower(%s)
                     OR lower(replace(qc.paper_path, '\\', '/')) LIKE %s
                )
            """
            params: List[Any] = [normalized_path, basename_suffix]
            if model:
                sql += " AND qc.model = %s"
                params.append(model)
            sql += " ORDER BY qc.query, qc.created_at DESC"
            cur.execute(sql, tuple(params))
            for query, answer, cache_model, created_at in cur.fetchall():
                key = _normalize_question_key(query)
                if not key:
                    continue
                created_at_text = str(created_at.isoformat() if created_at is not None else "")
                if key in latest_ts_by_key and latest_ts_by_key[key] >= created_at_text:
                    continue
                rows[key] = {
                    "answer": str(answer or ""),
                    "model": str(cache_model or ""),
                    "created_at": created_at_text,
                }
                latest_ts_by_key[key] = created_at_text
    except Exception:
        return {}
    return rows


@st.cache_data(ttl=600, show_spinner=False)
def _db_workflow_structured_answers_for_paper(path: Path, model: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """Load latest structured answers for a paper from workflow run records.

    Args:
        path (Path): Selected paper path.
        model (Optional[str]): Optional model filter.

    Returns:
        Dict[str, Dict[str, str]]: Mapping ``question -> answer metadata``.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}

    normalized_path = str(path).replace("\\", "/")
    basename_suffix = f"%/{path.name.lower()}"
    rows: Dict[str, Dict[str, str]] = {}
    latest_ts_by_key: Dict[str, str] = {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            sql = """
                WITH candidate_runs AS (
                    SELECT
                        run_id,
                        COALESCE(arm, '') AS arm,
                        COALESCE(config_effective_json ->> 'chat_model', '') AS chat_model,
                        CASE
                            WHEN lower(replace(COALESCE(papers_dir, ''), '\\', '/')) = lower(%s) THEN 0
                            WHEN lower(replace(COALESCE(papers_dir, ''), '\\', '/')) LIKE %s THEN 0
                            WHEN
                                lower(replace(COALESCE(papers_dir, ''), '\\', '/')) <> ''
                                AND lower(replace(COALESCE(papers_dir, ''), '\\', '/')) NOT LIKE '%%.pdf'
                                AND lower(%s) LIKE lower(replace(COALESCE(papers_dir, ''), '\\', '/')) || '/%%'
                            THEN 1
                            ELSE 9
                        END AS match_rank
                    FROM workflow.run_records
                    WHERE record_kind = 'run'
                      AND (
                            lower(replace(COALESCE(papers_dir, ''), '\\', '/')) = lower(%s)
                         OR lower(replace(COALESCE(papers_dir, ''), '\\', '/')) LIKE %s
                         OR (
                                lower(replace(COALESCE(papers_dir, ''), '\\', '/')) <> ''
                                AND lower(replace(COALESCE(papers_dir, ''), '\\', '/')) NOT LIKE '%%.pdf'
                                AND lower(%s) LIKE lower(replace(COALESCE(papers_dir, ''), '\\', '/')) || '/%%'
                            )
                       )
                ),
                question_rows AS (
                    SELECT
                        q.run_id,
                        COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') AS question_text,
                        COALESCE(q.payload_json ->> 'answer', q.output_json ->> 'answer', '') AS answer_text,
                        COALESCE(q.question_id, q.payload_json ->> 'id', q.output_json ->> 'id', '') AS question_id,
                        q.created_at,
                        cr.arm,
                        cr.chat_model,
                        cr.match_rank
                    FROM workflow.run_records q
                    JOIN candidate_runs cr ON cr.run_id = q.run_id
                    WHERE q.record_kind = 'question'
                      AND COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') <> ''
                )
                SELECT DISTINCT ON (question_text)
                    question_text,
                    answer_text,
                    question_id,
                    run_id,
                    COALESCE(NULLIF(chat_model, ''), NULLIF(arm, ''), '') AS model_label,
                    created_at,
                    match_rank
                FROM question_rows
            """
            params: List[Any] = [
                normalized_path,
                basename_suffix,
                normalized_path,
                normalized_path,
                basename_suffix,
                normalized_path,
            ]
            if model:
                sql += " WHERE (chat_model = %s OR arm = %s)"
                params.extend([model, model])
            sql += " ORDER BY question_text, match_rank ASC, created_at DESC"
            cur.execute(sql, tuple(params))
            for question, answer, question_id, run_id, model_label, created_at, match_rank in cur.fetchall():
                key = _normalize_question_key(question)
                if not key:
                    continue
                created_at_text = str(created_at.isoformat() if created_at is not None else "")
                if key in latest_ts_by_key and latest_ts_by_key[key] >= created_at_text:
                    continue
                source_label = "workflow.run_records"
                if int(match_rank or 9) > 0:
                    source_label = "workflow.run_records (directory fallback)"
                rows[key] = {
                    "answer": str(answer or ""),
                    "model": str(model_label or ""),
                    "created_at": created_at_text,
                    "source": source_label,
                    "run_id": str(run_id or ""),
                    "question_id": str(question_id or ""),
                }
                latest_ts_by_key[key] = created_at_text
    except Exception:
        return {}
    return rows


@st.cache_data(ttl=600, show_spinner=False)
def _db_workflow_question_records_for_paper(path: Path, model: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Load detailed workflow question records for a paper from workflow ledger.

    Returns records indexed by:
    - ``<run_id>::<question_id>``
    - ``<run_id>::q::<normalized_question>``
    - ``q::<normalized_question>`` (best available record across candidate runs)
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}

    normalized_path = str(path).replace("\\", "/")
    basename_suffix = f"%/{path.name.lower()}"
    out: Dict[str, Dict[str, Any]] = {}
    best_rank_by_question: Dict[str, Tuple[int, int, str]] = {}

    def _payload_signal_score(record: Dict[str, Any]) -> int:
        payload_obj = _structured_payload_from_question_record(record)
        score = 0
        anchors = payload_obj.get("citation_anchors")
        if isinstance(anchors, list) and anchors:
            score += 4
        if payload_obj.get("confidence_score") is not None:
            score += 2
        if payload_obj.get("retrieval_method"):
            score += 1
        if payload_obj.get("evidence_type"):
            score += 1
        if payload_obj.get("quote_snippet"):
            score += 1
        return score

    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            sql = """
                WITH candidate_runs AS (
                    SELECT
                        run_id,
                        COALESCE(arm, '') AS arm,
                        COALESCE(config_effective_json ->> 'chat_model', '') AS chat_model,
                        CASE
                            WHEN lower(replace(COALESCE(papers_dir, ''), '\\', '/')) = lower(%s) THEN 0
                            WHEN lower(replace(COALESCE(papers_dir, ''), '\\', '/')) LIKE %s THEN 0
                            WHEN
                                lower(replace(COALESCE(papers_dir, ''), '\\', '/')) <> ''
                                AND lower(replace(COALESCE(papers_dir, ''), '\\', '/')) NOT LIKE '%%.pdf'
                                AND lower(%s) LIKE lower(replace(COALESCE(papers_dir, ''), '\\', '/')) || '/%%'
                            THEN 1
                            ELSE 9
                        END AS match_rank
                    FROM workflow.run_records
                    WHERE record_kind = 'run'
                      AND (
                            lower(replace(COALESCE(papers_dir, ''), '\\', '/')) = lower(%s)
                         OR lower(replace(COALESCE(papers_dir, ''), '\\', '/')) LIKE %s
                         OR (
                                lower(replace(COALESCE(papers_dir, ''), '\\', '/')) <> ''
                                AND lower(replace(COALESCE(papers_dir, ''), '\\', '/')) NOT LIKE '%%.pdf'
                                AND lower(%s) LIKE lower(replace(COALESCE(papers_dir, ''), '\\', '/')) || '/%%'
                            )
                       )
                )
                SELECT
                    q.run_id,
                    COALESCE(q.question_id, q.payload_json ->> 'id', q.output_json ->> 'id', '') AS question_id,
                    COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') AS question_text,
                    COALESCE(q.status, '') AS status,
                    COALESCE(q.step, '') AS step,
                    COALESCE(q.record_key, '') AS record_key,
                    q.created_at,
                    q.updated_at,
                    q.payload_json,
                    q.output_json,
                    q.metadata_json,
                    cr.match_rank,
                    COALESCE(NULLIF(cr.chat_model, ''), NULLIF(cr.arm, ''), '') AS model_label
                FROM workflow.run_records q
                JOIN candidate_runs cr ON cr.run_id = q.run_id
                WHERE q.record_kind = 'question'
                  AND COALESCE(q.payload_json ->> 'question', q.output_json ->> 'question', '') <> ''
            """
            params: List[Any] = [
                normalized_path,
                basename_suffix,
                normalized_path,
                normalized_path,
                basename_suffix,
                normalized_path,
            ]
            if model:
                sql += " AND (cr.chat_model = %s OR cr.arm = %s)"
                params.extend([model, model])
            sql += " ORDER BY q.created_at DESC"
            cur.execute(sql, tuple(params))
            for (
                run_id,
                question_id,
                question_text,
                status,
                step,
                record_key,
                created_at,
                updated_at,
                payload_json,
                output_json,
                metadata_json,
                match_rank,
                model_label,
            ) in cur.fetchall():
                run_id_text = str(run_id or "").strip()
                question_id_text = str(question_id or "").strip()
                question_key = _normalize_question_key(question_text)
                created_at_text = str(created_at.isoformat() if created_at is not None else "")
                record = {
                    "run_id": run_id_text,
                    "question_id": question_id_text,
                    "question_text": str(question_text or ""),
                    "status": str(status or ""),
                    "step": str(step or ""),
                    "record_key": str(record_key or ""),
                    "created_at": created_at_text,
                    "updated_at": str(updated_at.isoformat() if updated_at is not None else ""),
                    "payload_json": payload_json if isinstance(payload_json, dict) else {},
                    "output_json": output_json if isinstance(output_json, dict) else {},
                    "metadata_json": metadata_json if isinstance(metadata_json, dict) else {},
                    "match_rank": int(match_rank or 9),
                    "model_label": str(model_label or ""),
                }
                if run_id_text and question_id_text:
                    id_key = f"{run_id_text}::{question_id_text}"
                    if id_key not in out:
                        out[id_key] = record
                if run_id_text and question_key:
                    question_map_key = f"{run_id_text}::q::{question_key}"
                    if question_map_key not in out:
                        out[question_map_key] = record
                if question_key:
                    global_key = f"q::{question_key}"
                    rank = (
                        _payload_signal_score(record),
                        -int(record.get("match_rank") or 9),
                        created_at_text,
                    )
                    previous = best_rank_by_question.get(global_key)
                    if previous is None or rank > previous:
                        best_rank_by_question[global_key] = rank
                        out[global_key] = record
    except Exception:
        return {}
    return out


def render_structured_workstream_tab(
    *,
    paper: Paper,
    chunks: List[Dict[str, Any]] | List[str],
    chunk_embeddings: List[List[float]],
    client: Any,
    settings: Settings,
    selected_model: str,
) -> None:
    """Render structured-question workstream view with cache integration.

    Args:
        paper (Paper): Selected paper.
        chunks (List[Dict[str, Any]] | List[str]): Prepared chunk payloads.
        chunk_embeddings (List[List[float]]): Embeddings aligned to ``chunks``.
        client (Any): LLM runtime/provider client.
        settings (Settings): Active settings.
        selected_model (str): Model selected in the UI.
    """
    st.subheader("Structured Question Workstream")
    st.caption("Uses workflow structured questions and reads/writes cached answers from `workflow.run_records`.")

    questions = _structured_report_questions()
    if not questions:
        st.info("No structured questions are configured.")
        return

    model_scope = st.radio(
        "Cache scope",
        options=["Selected model only", "Any model"],
        horizontal=True,
        key=f"structured_cache_scope_{paper.path.name}",
    )
    model_filter = selected_model if model_scope == "Selected model only" else None
    workflow_map = _db_workflow_structured_answers_for_paper(paper.path, model=model_filter)
    cached_map: Dict[str, Dict[str, str]] = dict(workflow_map)

    total_questions = len(questions)
    cached_questions = sum(1 for q in questions if _normalize_question_key(q.get("question")) in cached_map)
    cols = st.columns(3)
    cols[0].metric("Structured Questions", str(total_questions))
    cols[1].metric("Cached Answers", str(cached_questions))
    cols[2].metric("Uncached", str(total_questions - cached_questions))

    top_controls = st.columns([2, 2, 1])
    category_options = ["All"] + sorted({str(q.get("category") or "") for q in questions})
    selected_category = top_controls[0].selectbox(
        "Category",
        options=category_options,
        key=f"structured_category_{paper.path.name}",
    )
    text_filter = top_controls[1].text_input(
        "Filter question text",
        value="",
        key=f"structured_filter_{paper.path.name}",
        placeholder="Type to filter questions...",
    )
    if top_controls[2].button("Refresh Cache", key=f"structured_refresh_{paper.path.name}", use_container_width=True):
        _db_workflow_structured_answers_for_paper.clear()
        _db_workflow_question_records_for_paper.clear()
        st.rerun()

    filtered: List[Dict[str, str]] = []
    filter_text = text_filter.strip().lower()
    for item in questions:
        if selected_category != "All" and item.get("category") != selected_category:
            continue
        question_text = str(item.get("question") or "")
        if filter_text and filter_text not in question_text.lower():
            continue
        filtered.append(item)

    if not filtered:
        st.info("No questions match the selected filters.")
        return

    table_rows: List[Dict[str, Any]] = []
    for item in filtered:
        cached = cached_map.get(_normalize_question_key(item.get("question")))
        table_rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "cached": bool(cached),
                "source": (cached or {}).get("source", "workflow.run_records" if cached else ""),
                "cache_model": (cached or {}).get("model", ""),
                "cached_at": (cached or {}).get("created_at", ""),
                "question": item["question"],
            }
        )
    st.dataframe(table_rows, hide_index=True, use_container_width=True, height=320)

    st.markdown("---")
    st.markdown("**Workstream Export**")
    export_controls = st.columns(2)
    export_scope = export_controls[0].radio(
        "Export scope",
        options=["Filtered questions", "All structured questions"],
        horizontal=True,
        key=f"structured_export_scope_{paper.path.name}",
    )
    export_format = export_controls[1].radio(
        "Export format",
        options=["Compact", "Full"],
        horizontal=True,
        key=f"structured_export_format_{paper.path.name}",
    )
    export_questions = filtered if export_scope == "Filtered questions" else questions
    if export_format == "Full":
        st.caption("Full export includes detailed `workflow.run_records` question payload/metadata when available.")
    uncached_for_export = [q for q in export_questions if _normalize_question_key(q.get("question")) not in cached_map]
    st.caption(
        f"Export rows: {len(export_questions)} "
        f"(cached={len(export_questions) - len(uncached_for_export)}, uncached={len(uncached_for_export)})"
    )

    if st.button(
        "Generate Missing (Export Scope)",
        key=f"structured_generate_missing_export_{paper.path.name}",
        use_container_width=False,
        disabled=not bool(uncached_for_export),
    ):
        progress = st.progress(0.0)
        for idx, item in enumerate(uncached_for_export, start=1):
            progress.progress((idx - 1) / max(1, len(uncached_for_export)), text=f"Generating {item['id']}...")
            answer = _generate_and_cache_structured_answer(
                question_id=item["id"],
                category=item["category"],
                question=item["question"],
                paper=paper,
                chunks=chunks,
                chunk_embeddings=chunk_embeddings,
                client=client,
                settings=settings,
                selected_model=selected_model,
                session_id=st.session_state.session_id,
            )
            if not str(answer or "").strip():
                continue
            question_key = _normalize_question_key(item.get("question"))
            run_id = _streamlit_structured_run_id(paper_path=str(paper.path).replace("\\", "/"), model=selected_model)
            cached_map[question_key] = {
                "answer": answer,
                "model": selected_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "workflow.run_records",
                "run_id": run_id,
                "question_id": str(item.get("id") or ""),
            }
        progress.progress(1.0, text="Done")
        _db_workflow_structured_answers_for_paper.clear()
        _db_workflow_question_records_for_paper.clear()
        st.success("Generated and cached missing answers for export scope.")

    detailed_records: Dict[str, Dict[str, Any]] = {}
    missing_full_for_export: List[Dict[str, str]] = []
    if export_format == "Full":
        detailed_records = _db_workflow_question_records_for_paper(paper.path, model=model_filter)
        for item in export_questions:
            question_key = _normalize_question_key(item.get("question"))
            hit = cached_map.get(question_key) or {}
            run_id_text = str(hit.get("run_id") or "").strip()
            question_id_text = str(hit.get("question_id") or "").strip()
            record: Dict[str, Any] = {}
            if run_id_text and question_id_text:
                record = detailed_records.get(f"{run_id_text}::{question_id_text}") or {}
            if run_id_text and not record:
                record = detailed_records.get(f"{run_id_text}::q::{question_key}") or {}
            if not record:
                record = detailed_records.get(f"q::{question_key}") or {}
            payload_obj = _structured_payload_from_question_record(record)
            if not _payload_has_full_structured_fields(payload_obj):
                missing_full_for_export.append(item)
        st.caption(f"Rows missing full structured fields: {len(missing_full_for_export)}")

    if export_format == "Full" and st.button(
        "Regenerate Missing Full Fields (Export Scope)",
        key=f"structured_regenerate_full_export_{paper.path.name}",
        use_container_width=False,
        disabled=not bool(missing_full_for_export),
    ):
        progress = st.progress(0.0)
        for idx, item in enumerate(missing_full_for_export, start=1):
            progress.progress(
                (idx - 1) / max(1, len(missing_full_for_export)),
                text=f"Regenerating {item['id']}...",
            )
            answer = _generate_and_cache_structured_answer(
                question_id=item["id"],
                category=item["category"],
                question=item["question"],
                paper=paper,
                chunks=chunks,
                chunk_embeddings=chunk_embeddings,
                client=client,
                settings=settings,
                selected_model=selected_model,
                session_id=st.session_state.session_id,
            )
            if not str(answer or "").strip():
                continue
            question_key = _normalize_question_key(item.get("question"))
            run_id = _streamlit_structured_run_id(paper_path=str(paper.path).replace("\\", "/"), model=selected_model)
            cached_map[question_key] = {
                "answer": answer,
                "model": selected_model,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "workflow.run_records",
                "run_id": run_id,
                "question_id": str(item.get("id") or ""),
            }
        progress.progress(1.0, text="Done")
        _db_workflow_structured_answers_for_paper.clear()
        _db_workflow_question_records_for_paper.clear()
        st.success("Regenerated missing full structured fields for export scope.")
        st.rerun()

    export_bundle = _structured_workstream_export_bundle(
        paper=paper,
        questions=export_questions,
        cached_map=cached_map,
        selected_model=selected_model,
        cache_scope=model_scope,
        export_format=export_format,
        question_records=detailed_records,
    )
    export_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", paper.path.stem).strip("-").lower() or "paper"
    export_format_slug = "full" if export_format == "Full" else "compact"
    export_json = json.dumps(export_bundle, ensure_ascii=False, indent=2)
    st.download_button(
        f"Download Structured Workstream JSON ({export_format})",
        data=export_json,
        file_name=f"structured-workstream-{export_id}-{export_format_slug}.json",
        mime="application/json",
        key=f"structured_download_json_{paper.path.name}",
    )
    pdf_bytes = _structured_workstream_pdf_bytes(export_bundle)
    if pdf_bytes is None:
        st.caption("PDF export unavailable (missing `fpdf2` dependency in runtime).")
    else:
        st.download_button(
            f"Download Structured Workstream PDF ({export_format})",
            data=pdf_bytes,
            file_name=f"structured-workstream-{export_id}-{export_format_slug}.pdf",
            mime="application/pdf",
            key=f"structured_download_pdf_{paper.path.name}",
        )

    st.markdown("---")
    labels = [f"{item['id']} | {item['question']}" for item in filtered]
    selected_label = st.selectbox(
        "Question detail",
        options=labels,
        key=f"structured_selected_{paper.path.name}",
    )
    selected_item = filtered[labels.index(selected_label)]
    selected_question = selected_item["question"]

    st.markdown(f"**{selected_item['id']}**  \n{selected_question}")
    if st.button(
        "Ask This In Chat",
        key=f"structured_send_chat_{paper.path.name}_{selected_item['id']}",
        use_container_width=False,
    ):
        st.session_state["queued_query"] = selected_question
        st.success("Question queued for Chat tab.")

    cached = cached_map.get(_normalize_question_key(selected_question))
    if cached:
        source = cached.get("source") or "workflow.run_records"
        run_note = f"; run_id={cached.get('run_id')}" if source == "workflow.run_records" and cached.get("run_id") else ""
        st.caption(
            f"Cache hit from `{source}` ({cached.get('model') or 'unknown model'}{run_note})"
            + (f" at {cached.get('created_at')}" if cached.get("created_at") else "")
        )
        st.markdown(cached.get("answer") or "")
        return

    st.info("No cached answer for this question with current scope.")
    if st.button(
        "Generate and Cache Answer",
        key=f"structured_generate_{paper.path.name}_{selected_item['id']}",
        use_container_width=False,
    ):
        with st.spinner("Retrieving context and generating answer..."):
            request_id = uuid4().hex
            context, retrieval_stats = top_k_context(
                chunks,
                chunk_embeddings,
                query=selected_question,
                client=client,
                settings=settings,
                paper_path=paper.path,
                session_id=st.session_state.session_id,
                request_id=request_id,
                return_stats=True,
            )
            openalex_context = format_openalex_context(paper.openalex)
            citec_context = format_citec_context(paper.citec)
            user_input = f"Context:\n{context}\n\nQuestion: {selected_question}"
            prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
            if prefix_parts:
                user_input = f"{chr(10).join(prefix_parts)}\n\n{user_input}"

            answer_holder = st.empty()
            answer = stream_openai_answer(
                client=client,
                model=selected_model,
                instructions=RESEARCHER_QA_PROMPT,
                user_input=user_input,
                temperature=None,
                usage_context="structured_workstream_answer",
                session_id=st.session_state.session_id,
                request_id=request_id,
                on_delta=lambda txt: answer_holder.markdown(txt + "|"),
            )
            reviewed_answer = maybe_review_math_latex(
                client=client,
                answer=answer,
                source_model=selected_model,
                session_id=st.session_state.session_id,
                request_id=request_id,
            )
            if reviewed_answer:
                answer = reviewed_answer
            answer_holder.markdown(answer)

            cache_key = make_cache_key(selected_question, str(paper.path), selected_model, context)
            set_cached_answer(
                DEFAULT_CACHE_PATH,
                cache_key=cache_key,
                query=selected_question,
                paper_path=str(paper.path),
                model=selected_model,
                context=context,
                answer=answer,
            )
            persisted = _upsert_workflow_structured_answer(
                paper_path=paper.path,
                selected_model=selected_model,
                question_id=str(selected_item.get("id") or ""),
                category=str(selected_item.get("category") or ""),
                question=selected_question,
                answer=answer,
                structured_fields=_streamlit_structured_fields_from_context(
                    question=selected_question,
                    answer=answer,
                    context=context,
                    retrieval_stats=retrieval_stats if isinstance(retrieval_stats, dict) else {},
                    top_k=settings.top_k,
                ),
            )
            cached_map[_normalize_question_key(selected_question)] = {
                "answer": answer,
                "model": selected_model,
                "created_at": str(persisted.get("created_at") or datetime.now(timezone.utc).isoformat()),
                "source": "workflow.run_records",
                "run_id": str(persisted.get("run_id") or ""),
                "question_id": str(persisted.get("question_id") or selected_item.get("id") or ""),
            }
            _db_workflow_structured_answers_for_paper.clear()
            _db_workflow_question_records_for_paper.clear()
            st.success("Answer generated and cached.")


def _generate_and_cache_structured_answer(
    *,
    question_id: str,
    category: str,
    question: str,
    paper: Paper,
    chunks: List[Dict[str, Any]] | List[str],
    chunk_embeddings: List[List[float]],
    client: Any,
    settings: Settings,
    selected_model: str,
    session_id: Optional[str],
) -> str:
    """Generate one structured answer and persist it to both cache stores."""
    if not _is_valid_structured_question_text(question):
        return ""

    request_id = uuid4().hex
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=question,
        client=client,
        settings=settings,
        paper_path=paper.path,
        session_id=session_id,
        request_id=request_id,
        return_stats=True,
    )
    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    user_input = f"Context:\n{context}\n\nQuestion: {question}"
    prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
    if prefix_parts:
        user_input = f"{chr(10).join(prefix_parts)}\n\n{user_input}"

    answer = stream_openai_answer(
        client=client,
        model=selected_model,
        instructions=RESEARCHER_QA_PROMPT,
        user_input=user_input,
        temperature=None,
        usage_context="structured_workstream_answer",
        session_id=session_id,
        request_id=request_id,
        on_delta=lambda _txt: None,
    )
    reviewed_answer = maybe_review_math_latex(
        client=client,
        answer=answer,
        source_model=selected_model,
        session_id=session_id,
        request_id=request_id,
    )
    if reviewed_answer:
        answer = reviewed_answer

    cache_key = make_cache_key(question, str(paper.path), selected_model, context)
    set_cached_answer(
        DEFAULT_CACHE_PATH,
        cache_key=cache_key,
        query=question,
        paper_path=str(paper.path),
        model=selected_model,
        context=context,
        answer=answer,
    )
    _upsert_workflow_structured_answer(
        paper_path=paper.path,
        selected_model=selected_model,
        question_id=question_id,
        category=category,
        question=question,
        answer=answer,
        structured_fields=_streamlit_structured_fields_from_context(
            question=question,
            answer=answer,
            context=context,
            retrieval_stats=retrieval_stats if isinstance(retrieval_stats, dict) else {},
            top_k=settings.top_k,
        ),
    )
    return answer


def _pdf_safe_text(value: Any) -> str:
    """Return text compatible with core PDF fonts (latin-1)."""
    text = str(value or "")
    return text.encode("latin-1", "replace").decode("latin-1")


def _pdf_write_wrapped(pdf: Any, *, line_height: float, text: Any) -> None:
    """Write wrapped text using the full printable width, anchored to the left margin."""
    printable_width = float(getattr(pdf, "w", 0.0)) - float(getattr(pdf, "l_margin", 0.0)) - float(
        getattr(pdf, "r_margin", 0.0)
    )
    if printable_width <= 10.0:
        printable_width = 10.0
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(printable_width, line_height, _pdf_safe_text(text))


def _structured_workstream_export_bundle(
    *,
    paper: Paper,
    questions: List[Dict[str, str]],
    cached_map: Dict[str, Dict[str, str]],
    selected_model: str,
    cache_scope: str,
    export_format: str = "Compact",
    question_records: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build export payload for structured workstream outputs."""
    export_format_key = "full" if str(export_format or "").strip().lower() == "full" else "compact"
    records_map = question_records or {}

    def _record_payload_obj(record: Dict[str, Any]) -> Dict[str, Any]:
        return _structured_payload_from_question_record(record)

    def _record_signal_score(record: Dict[str, Any]) -> int:
        payload_obj = _record_payload_obj(record)
        score = 0
        anchors = payload_obj.get("citation_anchors")
        if isinstance(anchors, list) and anchors:
            score += 4
        if payload_obj.get("confidence_score") is not None:
            score += 2
        if payload_obj.get("retrieval_method"):
            score += 1
        if payload_obj.get("evidence_type"):
            score += 1
        if payload_obj.get("quote_snippet"):
            score += 1
        return score

    rows: List[Dict[str, Any]] = []
    for item in questions:
        question_text = str(item.get("question") or "")
        question_key = _normalize_question_key(question_text)
        hit = cached_map.get(question_key) or {}
        answer_text = str(hit.get("answer") or "")
        row: Dict[str, Any] = {
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
            "answer": answer_text,
            "cached": bool(hit),
            "source": str(hit.get("source") or ("workflow.run_records" if hit else "")),
            "model": str(hit.get("model") or ""),
            "cached_at": str(hit.get("created_at") or ""),
            "run_id": str(hit.get("run_id") or ""),
            "question_id": str(hit.get("question_id") or ""),
        }
        if export_format_key == "full":
            run_id_text = str(hit.get("run_id") or "").strip()
            question_id_text = str(hit.get("question_id") or "").strip()
            workflow_record: Dict[str, Any] = {}
            if run_id_text and question_id_text:
                workflow_record = records_map.get(f"{run_id_text}::{question_id_text}") or {}
            if run_id_text and not workflow_record:
                workflow_record = records_map.get(f"{run_id_text}::q::{question_key}") or {}
            global_record = records_map.get(f"q::{question_key}") if isinstance(records_map.get(f"q::{question_key}"), dict) else {}
            if global_record:
                if not workflow_record:
                    workflow_record = global_record
                elif _record_signal_score(global_record) > _record_signal_score(workflow_record):
                    workflow_record = global_record

            structured_obj = _record_payload_obj(workflow_record)
            anchors = structured_obj.get("citation_anchors")
            if not isinstance(anchors, list):
                anchors = []
            row.update(
                {
                    "question_key": question_key,
                    "has_answer": bool(answer_text.strip()),
                    "answer_length_chars": len(answer_text),
                    "answer_word_count": len([token for token in answer_text.split() if token]),
                    "cache_entry": dict(hit) if isinstance(hit, dict) else {},
                    "workflow_record": workflow_record,
                    "structured_fields": {
                        "evidence_type": structured_obj.get("evidence_type"),
                        "confidence": structured_obj.get("confidence"),
                        "confidence_score": structured_obj.get("confidence_score"),
                        "retrieval_method": structured_obj.get("retrieval_method"),
                        "citation_anchors": anchors,
                        "quote_snippet": structured_obj.get("quote_snippet"),
                        "table_figure": structured_obj.get("table_figure"),
                        "data_source": structured_obj.get("data_source"),
                        "assumption_flag": structured_obj.get("assumption_flag"),
                        "assumption_notes": structured_obj.get("assumption_notes"),
                        "related_questions": (
                            structured_obj.get("related_questions")
                            if isinstance(structured_obj.get("related_questions"), list)
                            else []
                        ),
                    },
                }
            )
        rows.append(row)

    bundle: Dict[str, Any] = {
        "export_type": "structured_workstream",
        "export_format": export_format_key,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "paper": {
            "path": str(paper.path),
            "title": paper.title,
            "author": paper.author,
        },
        "model_scope": {
            "selected_model": selected_model,
            "cache_scope": cache_scope,
        },
        "summary": {
            "total_questions": len(rows),
            "cached_questions": sum(1 for r in rows if r["cached"]),
            "uncached_questions": sum(1 for r in rows if not r["cached"]),
        },
        "questions": rows,
    }
    if export_format_key == "full":
        bundle["full_summary"] = {
            "rows_with_answer": sum(1 for r in rows if r.get("has_answer")),
            "rows_with_workflow_record": sum(
                1 for r in rows if isinstance(r.get("workflow_record"), dict) and bool(r.get("workflow_record"))
            ),
            "rows_with_citation_anchors": sum(
                1
                for r in rows
                if isinstance(r.get("structured_fields"), dict)
                and isinstance((r.get("structured_fields") or {}).get("citation_anchors"), list)
                and bool((r.get("structured_fields") or {}).get("citation_anchors"))
            ),
        }
    return bundle


def _structured_workstream_pdf_bytes(bundle: Dict[str, Any]) -> Optional[bytes]:
    """Render structured workstream bundle into PDF bytes.

    Returns:
        Optional[bytes]: PDF bytes, or ``None`` when PDF dependency is unavailable.
    """
    try:
        from fpdf import FPDF
    except Exception:
        return None

    paper = bundle.get("paper") or {}
    summary = bundle.get("summary") or {}
    questions = bundle.get("questions") or []
    model_scope = bundle.get("model_scope") or {}
    export_format = str(bundle.get("export_format") or "compact").strip().lower()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_title(_pdf_safe_text(f"Structured Workstream - {paper.get('title') or 'paper'}"))

    pdf.set_font("Helvetica", "B", 14)
    _pdf_write_wrapped(pdf, line_height=8, text="Structured Workstream Export")
    pdf.set_font("Helvetica", "", 10)
    _pdf_write_wrapped(pdf, line_height=6, text=f"Paper: {paper.get('title') or 'n/a'}")
    _pdf_write_wrapped(pdf, line_height=6, text=f"Author: {paper.get('author') or 'n/a'}")
    _pdf_write_wrapped(pdf, line_height=6, text=f"Path: {paper.get('path') or 'n/a'}")
    _pdf_write_wrapped(pdf, line_height=6, text=f"Exported At: {bundle.get('exported_at') or ''}")
    _pdf_write_wrapped(
        pdf,
        line_height=6,
        text=(
            f"Model Scope: selected_model={model_scope.get('selected_model') or ''}; "
            f"cache_scope={model_scope.get('cache_scope') or ''}"
        ),
    )
    _pdf_write_wrapped(pdf, line_height=6, text=f"Export Format: {export_format}")
    _pdf_write_wrapped(
        pdf,
        line_height=6,
        text=(
            f"Summary: total={summary.get('total_questions', 0)}, "
            f"cached={summary.get('cached_questions', 0)}, "
            f"uncached={summary.get('uncached_questions', 0)}"
        ),
    )
    pdf.ln(2)

    for item in questions:
        status = "cached" if item.get("cached") else "uncached"
        header = f"{item.get('id')}: {item.get('question')} [{status}]"
        pdf.set_font("Helvetica", "B", 10)
        _pdf_write_wrapped(pdf, line_height=6, text=header)

        meta = (
            f"category={item.get('category') or ''}; "
            f"source={item.get('source') or ''}; "
            f"model={item.get('model') or ''}; "
            f"cached_at={item.get('cached_at') or ''}"
        )
        pdf.set_font("Helvetica", "I", 9)
        _pdf_write_wrapped(pdf, line_height=5, text=meta)
        if export_format == "full":
            pdf.set_font("Helvetica", "", 9)
            _pdf_write_wrapped(
                pdf,
                line_height=5,
                text=(
                    f"answer_chars={item.get('answer_length_chars', 0)}; "
                    f"answer_words={item.get('answer_word_count', 0)}"
                ),
            )
            workflow_record = item.get("workflow_record") if isinstance(item.get("workflow_record"), dict) else {}
            if workflow_record:
                _pdf_write_wrapped(
                    pdf,
                    line_height=5,
                    text=(
                        f"record_status={workflow_record.get('status') or ''}; "
                        f"step={workflow_record.get('step') or ''}; "
                        f"record_key={workflow_record.get('record_key') or ''}; "
                        f"updated_at={workflow_record.get('updated_at') or ''}"
                    ),
                )
            structured_fields = item.get("structured_fields") if isinstance(item.get("structured_fields"), dict) else {}
            if structured_fields:
                anchors = structured_fields.get("citation_anchors")
                anchors_count = len(anchors) if isinstance(anchors, list) else 0
                _pdf_write_wrapped(
                    pdf,
                    line_height=5,
                    text=(
                        f"confidence={structured_fields.get('confidence') or ''}; "
                        f"confidence_score={structured_fields.get('confidence_score') or ''}; "
                        f"retrieval_method={structured_fields.get('retrieval_method') or ''}; "
                        f"evidence_type={structured_fields.get('evidence_type') or ''}; "
                        f"citation_anchors={anchors_count}"
                    ),
                )
                quote_snippet = str(structured_fields.get("quote_snippet") or "").strip()
                if quote_snippet:
                    _pdf_write_wrapped(pdf, line_height=5, text=f"quote_snippet: {quote_snippet}")

        pdf.set_font("Helvetica", "", 10)
        answer = str(item.get("answer") or "").strip()
        if not answer:
            answer = "[No cached answer]"
        _pdf_write_wrapped(pdf, line_height=5, text=answer)
        pdf.ln(2)

    payload = pdf.output(dest="S")
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    return str(payload).encode("latin-1", "replace")


def _openalex_author_names(meta: Optional[dict]) -> List[str]:
    """Extract author display names from an OpenAlex metadata payload.

    Args:
        meta (Optional[dict]): OpenAlex work payload.

    Returns:
        List[str]: Ordered author display names.
    """
    if not isinstance(meta, dict):
        return []
    names: List[str] = []
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if name:
            names.append(name)
    return names


def _openalex_venue(meta: Optional[dict]) -> str:
    """Return a best-effort venue name from OpenAlex metadata.

    Args:
        meta (Optional[dict]): OpenAlex work payload.

    Returns:
        str: Venue name when present; otherwise empty string.
    """
    if not isinstance(meta, dict):
        return ""
    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = str(source.get("display_name") or "").strip()
    if venue:
        return venue
    host = meta.get("host_venue") or {}
    return str(host.get("display_name") or "").strip()


def render_openalex_metadata_tab(paper: Paper) -> None:
    """Render the OpenAlex metadata tab for the selected paper.

    Args:
        paper (Paper): Selected paper object.
    """
    st.subheader("OpenAlex Metadata")
    st.caption("Metadata shown below is the raw OpenAlex payload associated with this paper.")
    meta = paper.openalex
    if not isinstance(meta, dict) or not meta:
        st.info("No OpenAlex metadata found for this paper.")
        return

    id_value = str(meta.get("id") or "")
    title_value = str(meta.get("display_name") or meta.get("title") or "")
    year_value = meta.get("publication_year")
    doi_value = str(meta.get("doi") or "")
    cited_by_value = meta.get("cited_by_count")
    refs_value = meta.get("referenced_works_count")
    venue_value = _openalex_venue(meta)
    primary = meta.get("primary_location") or {}
    landing_url = str(primary.get("landing_page_url") or "")
    authors = _openalex_author_names(meta)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**OpenAlex ID:** `{id_value or 'n/a'}`")
        st.markdown(f"**Title:** {title_value or 'n/a'}")
        st.markdown(f"**Publication Year:** {year_value if year_value is not None else 'n/a'}")
        st.markdown(f"**DOI:** {doi_value or 'n/a'}")
    with col2:
        st.markdown(f"**Venue:** {venue_value or 'n/a'}")
        st.markdown(f"**Cited By Count:** {cited_by_value if cited_by_value is not None else 'n/a'}")
        st.markdown(f"**Referenced Works Count:** {refs_value if refs_value is not None else 'n/a'}")
        if landing_url:
            st.markdown(f"**Landing URL:** {landing_url}")
        else:
            st.markdown("**Landing URL:** n/a")

    st.markdown("**Authors**")
    if authors:
        for idx, author in enumerate(authors, start=1):
            st.markdown(f"{idx}. {author}")
    else:
        st.caption("No author list available in OpenAlex payload.")

    st.markdown("---")
    st.markdown("**Raw OpenAlex JSON**")
    st.json(meta, expanded=False)


def _openalex_work_id(value: Any) -> str:
    """Normalize an OpenAlex work identifier to ``W...`` format.

    Args:
        value (Any): Work id string or URL.

    Returns:
        str: OpenAlex work key (for example ``W2032518524``), or empty string.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    return text


def _openalex_work_url(value: Any) -> str:
    """Build an OpenAlex work URL from id/URL variants.

    Args:
        value (Any): OpenAlex id, API URL, or canonical URL.

    Returns:
        str: Canonical ``https://openalex.org/W...`` URL when resolvable.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    if text.startswith("https://openalex.org/"):
        return text
    if text.startswith("https://api.openalex.org/"):
        key = text.rsplit("/", 1)[-1]
        if key:
            return f"https://openalex.org/{key}"
        return ""
    key = _openalex_work_id(text)
    if key:
        return f"https://openalex.org/{key}"
    return ""


def _openalex_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Call OpenAlex and return JSON payload.

    Args:
        url (str): Endpoint URL.
        params (Optional[Dict[str, Any]]): Query parameters.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Parsed payload on success.
    """
    data = openalex_request_json(url, params=params, timeout=timeout)
    if isinstance(data, dict):
        return data
    return None


def _openalex_work_summary(work_id: str, *, include_references: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch a compact OpenAlex work payload.

    Args:
        work_id (str): OpenAlex work id (``W...`` or URL).
        include_references (bool): Whether to include ``referenced_works``.

    Returns:
        Optional[Dict[str, Any]]: Work payload.
    """
    key = _openalex_work_id(work_id)
    if not key:
        return None
    fields = ["id", "display_name", "publication_year", "doi", "cited_by_count"]
    if include_references:
        fields.append("referenced_works")
    url = f"https://api.openalex.org/works/{key}"
    payload = _openalex_request_json(url, params={"select": ",".join(fields)})
    return payload if isinstance(payload, dict) else None


@st.cache_data(ttl=3600, show_spinner=False)
def _openalex_citation_network(center_work_id: str, max_references: int, max_citing: int) -> Dict[str, Any]:
    """Build OpenAlex citation neighborhood for one center work.

    Args:
        center_work_id (str): OpenAlex center work id or URL.
        max_references (int): Max number of referenced papers (left side).
        max_citing (int): Max number of citing papers (right side).

    Returns:
        Dict[str, Any]: ``center``, ``references``, and ``citing`` lists.
    """
    center = _openalex_work_summary(center_work_id, include_references=True)
    if not center:
        return {"center": None, "references": [], "citing": []}

    all_reference_ids = center.get("referenced_works") or []
    reference_candidate_cap = max(
        int(max_references),
        int(os.environ.get("OPENALEX_NETWORK_REFERENCE_CANDIDATES", "200")),
    )
    references: List[Dict[str, Any]] = []
    for ref in all_reference_ids[:reference_candidate_cap]:
        ref_summary = _openalex_work_summary(str(ref), include_references=False)
        if ref_summary:
            references.append(ref_summary)
    references.sort(key=lambda w: int(w.get("cited_by_count") or 0), reverse=True)
    references = references[: max(0, int(max_references))]

    center_key = _openalex_work_id(center.get("id"))
    citing: List[Dict[str, Any]] = []
    if center_key and max_citing > 0:
        search = _openalex_request_json(
            "https://api.openalex.org/works",
            params={
                "filter": f"cites:{center_key}",
                "per-page": int(max_citing),
                "select": "id,display_name,publication_year,doi,cited_by_count",
                "sort": "cited_by_count:desc",
            },
        )
        results = (search or {}).get("results") if isinstance(search, dict) else None
        if isinstance(results, list):
            citing = [item for item in results if isinstance(item, dict)]
    citing.sort(key=lambda w: int(w.get("cited_by_count") or 0), reverse=True)
    citing = citing[: max(0, int(max_citing))]

    return {"center": center, "references": references, "citing": citing}


def _work_title(work: Dict[str, Any]) -> str:
    """Return display title for one OpenAlex work payload.

    Args:
        work (Dict[str, Any]): OpenAlex work.

    Returns:
        str: Work title fallback.
    """
    return str(work.get("display_name") or work.get("title") or work.get("id") or "Unknown paper")


def _work_label(work: Dict[str, Any], *, max_chars: int = 80) -> str:
    """Return a short plot label for an OpenAlex work.

    Args:
        work (Dict[str, Any]): OpenAlex work.
        max_chars (int): Maximum text length.

    Returns:
        str: Label with optional year.
    """
    title = _work_title(work)
    if len(title) > max_chars:
        title = title[: max_chars - 3].rstrip() + "..."
    year = work.get("publication_year")
    return f"{title} ({year})" if year else title


def _network_ys(n: int) -> List[float]:
    """Generate vertically distributed y-coordinates for side nodes.

    Args:
        n (int): Number of nodes.

    Returns:
        List[float]: Y positions.
    """
    if n <= 0:
        return []
    if n == 1:
        return [0.0]
    top = 1.0
    bottom = -1.0
    step = (top - bottom) / (n - 1)
    return [top - idx * step for idx in range(n)]


def _citation_node_size(cited_by_count: Any) -> float:
    """Compute displayed node size from citation count.

    Args:
        cited_by_count (Any): Citation count value.

    Returns:
        float: Pixel size for the visual node marker.
    """
    try:
        value = float(cited_by_count or 0.0)
    except Exception:
        value = 0.0
    value = max(value, 1.0)
    return max(14.0, min(44.0, 8.0 + math.sqrt(value)))


def _citation_count_int(value: Any) -> int:
    """Coerce citation count into a non-negative integer.

    Args:
        value (Any): Citation count candidate.

    Returns:
        int: Non-negative citation count.
    """
    try:
        parsed = int(float(value or 0))
    except Exception:
        parsed = 0
    return max(parsed, 0)


def _sort_works_by_size_desc(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort works from largest to smallest visual node size.

    Args:
        works (List[Dict[str, Any]]): OpenAlex works.

    Returns:
        List[Dict[str, Any]]: Sorted works.
    """
    return sorted(
        works,
        key=lambda item: (
            -_citation_node_size(item.get("cited_by_count")),
            -_citation_count_int(item.get("cited_by_count")),
            _work_title(item).lower(),
        ),
    )


def _work_hover_html(work: Dict[str, Any], group_label: str) -> str:
    """Build HTML tooltip content for one work node.

    Args:
        work (Dict[str, Any]): OpenAlex work payload.
        group_label (str): Display group label.

    Returns:
        str: HTML tooltip text.
    """
    title = html.escape(_work_title(work))
    openalex_id = html.escape(str(work.get("id") or "n/a"))
    year = html.escape(str(work.get("publication_year") or "n/a"))
    cited_by = html.escape(str(work.get("cited_by_count") or 0))
    doi = html.escape(str(work.get("doi") or "n/a"))
    return (
        f"<b>{html.escape(group_label)}</b><br>"
        f"{title}<br>"
        f"Year: {year}<br>"
        f"Citations: {cited_by}<br>"
        f"DOI: {doi}<br>"
        f"<span style='font-family:monospace'>{openalex_id}</span>"
    )


def _citation_network_html(center: Dict[str, Any], references: List[Dict[str, Any]], citing: List[Dict[str, Any]]) -> str:
    """Build citation neighborhood as draggable vis-network HTML.

    Args:
        center (Dict[str, Any]): Center paper.
        references (List[Dict[str, Any]]): Referenced papers.
        citing (List[Dict[str, Any]]): Papers citing the center paper.

    Returns:
        str: HTML snippet containing a draggable network.
    """
    graph_id = f"citation-network-{uuid4().hex}"
    ref_y = _network_ys(len(references))
    cit_y = _network_ys(len(citing))
    y_scale = 300
    left_x = -580
    center_x = 0
    right_x = 580

    center_id = _openalex_work_id(center.get("id")) or "center"
    nodes: List[Dict[str, Any]] = [
        {
            "id": center_id,
            "label": _work_label(center, max_chars=56),
            "title": _work_hover_html(center, "Selected Paper"),
            "openalex_url": _openalex_work_url(center.get("id")),
            "x": center_x,
            "y": 0,
            "size": _citation_node_size(center.get("cited_by_count")),
            "group": "selected",
        }
    ]
    edges: List[Dict[str, Any]] = []

    for idx, (work, y) in enumerate(zip(references, ref_y)):
        node_id = _openalex_work_id(work.get("id")) or f"ref-{idx}"
        nodes.append(
            {
                "id": node_id,
                "label": _work_label(work, max_chars=56),
                "title": _work_hover_html(work, "Reference (cited by selected paper)"),
                "openalex_url": _openalex_work_url(work.get("id")),
                "x": left_x,
                "y": int(y * y_scale),
                "size": _citation_node_size(work.get("cited_by_count")),
                "group": "reference",
            }
        )
        edges.append({"from": center_id, "to": node_id, "arrows": "to"})

    for idx, (work, y) in enumerate(zip(citing, cit_y)):
        node_id = _openalex_work_id(work.get("id")) or f"cit-{idx}"
        nodes.append(
            {
                "id": node_id,
                "label": _work_label(work, max_chars=56),
                "title": _work_hover_html(work, "Citing paper (cites selected paper)"),
                "openalex_url": _openalex_work_url(work.get("id")),
                "x": right_x,
                "y": int(y * y_scale),
                "size": _citation_node_size(work.get("cited_by_count")),
                "group": "citing",
            }
        )
        edges.append({"from": node_id, "to": center_id, "arrows": "to"})

    payload_nodes = json.dumps(nodes)
    payload_edges = json.dumps(edges)
    return f"""
<div style="margin: 0 0 8px 0; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; font-weight: 600;">
  <div style="text-align: left; color: #C44536;">References (Left)</div>
  <div style="text-align: center; color: #2A6F97;">Selected Paper (Center)</div>
  <div style="text-align: right; color: #2A9D8F;">Cited By (Right)</div>
</div>
<div id="{graph_id}" style="width: 100%; height: 660px; border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff;"></div>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script>
  (function() {{
    const container = document.getElementById("{graph_id}");
    const nodes = new vis.DataSet({payload_nodes});
    const edges = new vis.DataSet({payload_edges});
    const data = {{ nodes, edges }};
    const options = {{
      autoResize: true,
      physics: false,
      interaction: {{
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
      }},
      nodes: {{
        shape: "dot",
        font: {{
          color: "#1f2937",
          size: 13,
          face: "Arial",
          multi: "html"
        }},
        borderWidth: 1,
        borderWidthSelected: 2
      }},
      groups: {{
        selected: {{ color: {{ background: "#2A6F97", border: "#1f4f6e" }} }},
        reference: {{ color: {{ background: "#C44536", border: "#8c2f25" }} }},
        citing: {{ color: {{ background: "#2A9D8F", border: "#1f756a" }} }}
      }},
      edges: {{
        color: {{ color: "rgba(100, 116, 139, 0.45)" }},
        smooth: {{
          enabled: true,
          type: "cubicBezier",
          forceDirection: "horizontal",
          roundness: 0.35
        }},
        arrows: {{
          to: {{
            enabled: true,
            scaleFactor: 0.45
          }}
        }}
      }}
    }};
    const network = new vis.Network(container, data, options);
    network.on("hoverNode", function(params) {{
      const node = nodes.get(params.node);
      container.style.cursor = node && node.openalex_url ? "pointer" : "default";
    }});
    network.on("blurNode", function() {{
      container.style.cursor = "default";
    }});
    network.on("click", function(params) {{
      if (!params.nodes || params.nodes.length === 0) return;
      const node = nodes.get(params.nodes[0]);
      if (!node || !node.openalex_url) return;
      window.open(node.openalex_url, "_blank", "noopener,noreferrer");
    }});
  }})();
</script>
"""


def _works_rows(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAlex works into table rows.

    Args:
        works (List[Dict[str, Any]]): OpenAlex works.

    Returns:
        List[Dict[str, Any]]: Table rows.
    """
    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(works, start=1):
        rows.append(
            {
                "rank": idx,
                "title": _work_title(item),
                "node_size": round(_citation_node_size(item.get("cited_by_count")), 2),
                "cited_by_count": _citation_count_int(item.get("cited_by_count")),
                "year": item.get("publication_year"),
                "doi": item.get("doi"),
                "openalex_id": item.get("id"),
            }
        )
    return rows


def render_openalex_citation_network_tab(paper: Paper) -> None:
    """Render interactive citation network around the selected paper.

    Args:
        paper (Paper): Selected paper.
    """
    st.subheader("Citation Network")
    st.caption(
        "Center node is the selected paper, left nodes are papers it cites, "
        "and right nodes are papers that cite it (OpenAlex)."
    )
    st.caption(
        "Nodes on each side are ranked by their own citation count and truncated to the top 10. "
        "Node size is sqrt(cited_by_count). Drag nodes to rearrange the graph. "
        "Click any node to open that paper on OpenAlex."
    )

    meta = paper.openalex
    work_id = ""
    if isinstance(meta, dict):
        work_id = str(meta.get("id") or "").strip()
    if not work_id:
        st.info("No OpenAlex work id found for this paper, so the citation network is unavailable.")
        return

    max_references = 10
    max_citing = 10

    with st.spinner("Loading citation neighborhood from OpenAlex..."):
        network = _openalex_citation_network(work_id, int(max_references), int(max_citing))

    center = network.get("center")
    references = _sort_works_by_size_desc(network.get("references") or [])
    citing = _sort_works_by_size_desc(network.get("citing") or [])
    if not isinstance(center, dict) or not center:
        st.warning("Unable to load OpenAlex data for this paper.")
        return

    reset_key = re.sub(r"[^a-zA-Z0-9_-]+", "_", _openalex_work_id(work_id) or "paper")
    control_col, help_col = st.columns([1, 4])
    with control_col:
        if st.button("Reset layout", key=f"reset-network-{reset_key}"):
            st.rerun()
    with help_col:
        st.caption("Tip: drag nodes to explore local structure, then use reset to restore lane layout.")

    components.html(_citation_network_html(center, references, citing), height=710, scrolling=False)

    summary_cols = st.columns(3)
    summary_cols[0].metric("Center Paper", 1)
    summary_cols[1].metric("References Shown", len(references))
    summary_cols[2].metric("Citing Papers Shown", len(citing))

    st.markdown("---")
    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown("**Referenced Papers (Left)**")
        rows = _works_rows(references)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.caption("No referenced papers returned.")
    with right_col:
        st.markdown("**Citing Papers (Right)**")
        rows = _works_rows(citing)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.caption("No citing papers returned.")


def _response_text_from_final_response(response: object) -> str:
    """Extract best-effort text from a completed OpenAI response object.

    Args:
        response (object): Response payload returned by the upstream call.

    Returns:
        str: Computed string result.
    """
    text = getattr(response, "output_text", None) or getattr(response, "text", None)
    if text:
        return str(text).strip()
    parts: List[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "output_text":
                chunk = getattr(content, "text", None)
                if chunk:
                    parts.append(str(chunk))
    return "\n".join(parts).strip()


def stream_llm_answer(
    *,
    client: Any,
    model: str,
    instructions: str,
    user_input: str,
    temperature: Optional[float],
    usage_context: str,
    session_id: Optional[str],
    request_id: Optional[str],
    on_delta: Callable[[str], None],
) -> str:
    """Stream answer tokens from provider-routed LLM runtime and return text.

    Args:
        client (Any): Provider client instance.
        model (str): Model name used for this operation.
        instructions (str): Input value for instructions.
        user_input (str): Input value for user input.
        temperature (Optional[float]): Input value for temperature.
        usage_context (str): Input value for usage context.
        session_id (Optional[str]): Session identifier.
        request_id (Optional[str]): Request identifier.
        on_delta (Callable[[str], None]): Input value for on delta.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    for attempt in range(max_retries + 1):
        try:
            if hasattr(client, "stream"):
                response = client.stream(
                    model=model,
                    instructions=instructions,
                    user_input=user_input,
                    temperature=temperature,
                    max_output_tokens=None,
                    on_delta=on_delta,
                    metadata={"capability": "stream_chat"},
                )
            elif hasattr(client, "chat") and hasattr(client.chat, "stream"):
                response = client.chat.stream(
                    model=model,
                    instructions=instructions,
                    user_input=user_input,
                    temperature=temperature,
                    max_output_tokens=None,
                    on_delta=on_delta,
                    metadata={"capability": "stream_chat"},
                )
            else:
                # Fallback to non-streaming call when provider does not stream.
                text = call_llm(
                    client,
                    model=model,
                    instructions=instructions,
                    user_input=user_input,
                    max_output_tokens=None,
                    temperature=temperature,
                    usage_context=usage_context,
                    session_id=session_id,
                    request_id=request_id,
                    step=usage_context,
                    meta={"capability": "stream_chat"},
                )
                on_delta(text)
                return text

            try:
                usage_meta = {
                    "provider": getattr(response, "provider", None),
                    "capability": getattr(response, "capability", "stream_chat"),
                    "fallback_from": getattr(response, "fallback_from", None),
                }
                record_usage(
                    model=model,
                    operation=usage_context,
                    step=usage_context,
                    input_tokens=int(getattr(response, "input_tokens", 0) or 0),
                    output_tokens=int(getattr(response, "output_tokens", 0) or 0),
                    total_tokens=int(getattr(response, "total_tokens", 0) or 0),
                    session_id=session_id,
                    request_id=request_id,
                    provider_request_id=getattr(response, "provider_request_id", None),
                    meta=usage_meta,
                )
            except Exception:
                pass

            return str(getattr(response, "text", "") or "").strip()
        except Exception as exc:
            if attempt >= max_retries:
                db_url = os.environ.get("DATABASE_URL")
                if db_url:
                    try:
                        from ragonometrics.indexing import metadata

                        conn = connect(db_url, require_migrated=True)
                        metadata.record_failure(
                            conn,
                            "llm",
                            str(exc),
                            {"model": model, "streaming": True, "temperature": temperature},
                        )
                        conn.close()
                    except Exception:
                        pass
                raise
            time.sleep(0.5 * (attempt + 1))


def stream_openai_answer(
    *,
    client: Any,
    model: str,
    instructions: str,
    user_input: str,
    temperature: Optional[float],
    usage_context: str,
    session_id: Optional[str],
    request_id: Optional[str],
    on_delta: Callable[[str], None],
) -> str:
    """Backward-compatible alias for provider-routed streaming answer generation."""
    return stream_llm_answer(
        client=client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        temperature=temperature,
        usage_context=usage_context,
        session_id=session_id,
        request_id=request_id,
        on_delta=on_delta,
    )


_MATH_SIGNAL_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z]_\{[^}]+\}|"
    r"[A-Za-z]_[A-Za-z0-9]+|"
    r"[A-Za-z]\^\{[^}]+\}|"
    r"\bp\([^)\n]*\|[^)\n]*\)|"
    r"\bargmax\b|\bargmin\b|"
    r"\u2211|\u222b|\u221a|\u2248|\u2260|\u2264|\u2265|"
    r"\bE\[[^\]]+\]"
    r")"
)


def _truthy_env(name: str, default: bool) -> bool:
    """Truthy env.

    Args:
        name (str): Input value for name.
        default (bool): Default value used when primary input is missing.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _should_review_math_latex(answer: str) -> bool:
    """Should review math latex.

    Args:
        answer (str): Input value for answer.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    if not answer or not answer.strip():
        return False
    if "$" in answer and (_MATH_SIGNAL_PATTERN.search(answer) is None):
        return False
    if _MATH_SIGNAL_PATTERN.search(answer):
        return True
    # catch plain assignments/ranges often written without delimiters
    if re.search(r"\b[A-Za-z][A-Za-z0-9]*\s*=\s*[^,\n]{1,40}", answer):
        return True
    return False


def _estimate_review_max_tokens(answer: str) -> int:
    # Roughly map characters to tokens; keep bounded for latency/cost.
    """Estimate review max tokens.

    Args:
        answer (str): Input value for answer.

    Returns:
        int: Computed integer result.
    """
    approx = int(len(answer) / 3.0) + 128
    return max(256, min(3072, approx))


def maybe_review_math_latex(
    *,
    client: Any,
    answer: str,
    source_model: str,
    session_id: Optional[str],
    request_id: Optional[str],
) -> str:
    """Optionally run an AI formatting pass so math renders with LaTeX.

    Args:
        client (OpenAI): Provider client instance.
        answer (str): Input value for answer.
        source_model (str): Input value for source model.
        session_id (Optional[str]): Session identifier.
        request_id (Optional[str]): Request identifier.

    Returns:
        str: Computed string result.
    """
    if not _truthy_env("MATH_LATEX_REVIEW_ENABLED", True):
        return answer
    if not _should_review_math_latex(answer):
        return answer

    review_model = os.environ.get("MATH_LATEX_REVIEW_MODEL", "").strip() or source_model
    try:
        reviewed = call_llm(
            client,
            model=review_model,
            instructions=MATH_LATEX_REVIEW_PROMPT,
            user_input=f"Answer:\n{answer}",
            max_output_tokens=_estimate_review_max_tokens(answer),
            usage_context="math_latex_review",
            session_id=session_id,
            request_id=request_id,
            step="math_latex_review",
            meta={"capability": "chat"},
        ).strip()
        return reviewed or answer
    except Exception:
        return answer


def scroll_chat_to_top() -> None:
    """Request a smooth scroll to the top of the Streamlit app.
    """
    components.html(
        """
        <script>
        const doc = window.parent.document;
        const app = doc.querySelector('[data-testid="stAppViewContainer"]');
        if (app) {
          app.scrollTo({ top: 0, behavior: "smooth" });
        } else {
          window.parent.scrollTo({ top: 0, behavior: "smooth" });
        }
        </script>
        """,
        height=0,
    )


def extract_highlight_terms(query: str, max_terms: int = 6) -> List[str]:
    """Extract key terms from a query for highlighting.

    Args:
        query (str): Input query text.
        max_terms (int): Input value for max terms.

    Returns:
        List[str]: List result produced by the operation.
    """
    stop = {
        "the", "and", "or", "but", "a", "an", "of", "to", "in", "for", "on", "with",
        "is", "are", "was", "were", "be", "been", "it", "this", "that", "these",
        "those", "as", "at", "by", "from", "about", "into", "over", "after", "before",
        "what", "which", "who", "whom", "why", "how", "when", "where",
    }
    tokens = re.findall(r"[A-Za-z0-9]{3,}", query.lower())
    terms = []
    for tok in tokens:
        if tok in stop:
            continue
        if tok not in terms:
            terms.append(tok)
        if len(terms) >= max_terms:
            break
    return terms


def highlight_text_html(text: str, terms: List[str]) -> str:
    """Return HTML with highlight marks for matching terms.

    Args:
        text (str): Input text value.
        terms (List[str]): Collection of terms.

    Returns:
        str: Computed string result.
    """
    if not terms:
        return html.escape(text)
    escaped = html.escape(text)
    for term in terms:
        pattern = re.compile(rf"\b({re.escape(term)})\b", re.IGNORECASE)
        escaped = pattern.sub(r"<mark>\1</mark>", escaped)
    return escaped


def highlight_image_terms(image, terms: List[str]):
    """Highlight matched terms on a PIL image using OCR.

    Args:
        image (Any): Input value for image.
        terms (List[str]): Collection of terms.

    Returns:
        Any: Return value produced by the operation.
    """
    if not terms or not pytesseract or not ImageDraw:
        return image
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception:
        return image
    if not data or "text" not in data:
        return image

    img = image.convert("RGBA")
    overlay = ImageDraw.Draw(img, "RGBA")
    terms_lower = {t.lower() for t in terms}
    for i, word in enumerate(data["text"]):
        if not word:
            continue
        w = word.strip().lower()
        if w in terms_lower:
            x = data["left"][i]
            y = data["top"][i]
            w_box = data["width"][i]
            h_box = data["height"][i]
            overlay.rectangle([x, y, x + w_box, y + h_box], fill=(255, 235, 59, 120), outline=(255, 193, 7, 200))
    return img


def render_citation_snapshot(path: Path, citation: dict, key_prefix: str, query: str) -> None:
    """Render a highlighted text snapshot and optional page image for a citation chunk.

    Args:
        path (Path): Filesystem path value.
        citation (dict): Mapping containing citation.
        key_prefix (str): Input value for key prefix.
        query (str): Input query text.
    """
    meta = citation.get("meta") or "Context chunk"
    text = citation.get("text") or ""
    page = citation.get("page")
    terms = extract_highlight_terms(query)

    st.markdown(f"**{meta}**")
    if text:
        snippet = text if len(text) <= 1200 else text[:1200] + "..."
        highlighted = highlight_text_html(snippet, terms)
        st.markdown(
            f"<div style='font-family: monospace; white-space: pre-wrap;'>{highlighted}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("No text available for this chunk.")

    if page and convert_from_path:
        show_key = f"{key_prefix}_show_page_{page}"
        if st.checkbox(f"Show page {page} snapshot", key=show_key):
            try:
                images = convert_from_path(str(path), first_page=page, last_page=page)
                if images:
                    img = highlight_image_terms(images[0], terms)
                    st.image(img, caption=f"Page {page}")
            except Exception as exc:
                st.warning(f"Failed to render page {page}: {exc}")
    elif page:
        st.caption(f"Page {page} (snapshot requires pdf2image + poppler)")


def load_streamlit_credentials() -> Dict[str, str]:
    """Load configured Streamlit login credentials from environment variables.

    Precedence:
    1. ``STREAMLIT_USERS_JSON`` JSON object (``{username: password}``).
    2. Legacy single-user env pair ``STREAMLIT_USERNAME``/``STREAMLIT_PASSWORD``.

    Returns:
        Dict[str, str]: Username/password mapping for login validation.
    """
    raw_users_json = (os.getenv("STREAMLIT_USERS_JSON") or "").strip()
    credentials: Dict[str, str] = {}

    if raw_users_json:
        try:
            parsed = json.loads(raw_users_json)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for raw_user, raw_password in parsed.items():
                user = str(raw_user or "").strip()
                password = str(raw_password or "").strip()
                if user and password:
                    credentials[user] = password
        if credentials:
            return credentials

    expected_user = (os.getenv("STREAMLIT_USERNAME") or "").strip()
    expected_pass = (os.getenv("STREAMLIT_PASSWORD") or "").strip()
    if expected_user and expected_pass:
        credentials[expected_user] = expected_pass
    return credentials


_STREAMLIT_HASH_PREFIX = "pbkdf2_sha256"
_STREAMLIT_HASH_MIN_ITERATIONS = 100_000
_STREAMLIT_HASH_DEFAULT_ITERATIONS = 390_000


def _streamlit_password_hash(password: str, *, iterations: int | None = None, salt: bytes | None = None) -> str:
    """Build a PBKDF2-SHA256 hash for a Streamlit password."""
    secret = str(password or "")
    if not secret:
        return ""
    iter_count = int(iterations or os.getenv("STREAMLIT_AUTH_PBKDF2_ITERATIONS", _STREAMLIT_HASH_DEFAULT_ITERATIONS))
    iter_count = max(_STREAMLIT_HASH_MIN_ITERATIONS, iter_count)
    salt_bytes = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt_bytes, iter_count)
    salt_text = base64.urlsafe_b64encode(salt_bytes).decode("ascii").rstrip("=")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{_STREAMLIT_HASH_PREFIX}${iter_count}${salt_text}${digest_text}"


def _streamlit_password_verify(password: str, password_hash: str) -> bool:
    """Verify plaintext password against hash (or legacy plaintext value)."""
    secret = str(password or "")
    stored = str(password_hash or "")
    if not secret or not stored:
        return False
    if not stored.startswith(f"{_STREAMLIT_HASH_PREFIX}$"):
        return hmac.compare_digest(secret, stored)
    parts = stored.split("$", 3)
    if len(parts) != 4:
        return False
    _, iter_text, salt_text, digest_text = parts
    try:
        iter_count = max(_STREAMLIT_HASH_MIN_ITERATIONS, int(iter_text))
        salt_pad = "=" * (-len(salt_text) % 4)
        digest_pad = "=" * (-len(digest_text) % 4)
        salt_bytes = base64.urlsafe_b64decode((salt_text + salt_pad).encode("ascii"))
        expected = base64.urlsafe_b64decode((digest_text + digest_pad).encode("ascii"))
    except Exception:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt_bytes, iter_count)
    return hmac.compare_digest(computed, expected)


def _streamlit_auth_db_url() -> str:
    """Return the configured auth DB URL, or empty when unset."""
    return (os.environ.get("DATABASE_URL") or "").strip()


def _streamlit_auth_tables_ready(db_url: str) -> bool:
    """Check whether auth tables are present."""
    if not db_url:
        return False
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    to_regclass('auth.streamlit_users'),
                    to_regclass('auth.streamlit_sessions')
                """
            )
            row = cur.fetchone()
            return bool(row and row[0] is not None and row[1] is not None)
    except Exception:
        return False


def _streamlit_active_db_user_count(db_url: str) -> int:
    """Count active DB-backed Streamlit users."""
    if not db_url:
        return 0
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM auth.streamlit_users WHERE is_active = TRUE")
            row = cur.fetchone()
            return int((row or [0])[0] or 0)
    except Exception:
        return 0


def _upsert_streamlit_users_from_env(db_url: str, credentials: Dict[str, str]) -> int:
    """Upsert plaintext env credentials into DB as password hashes."""
    if not db_url or not credentials:
        return 0
    upserted = 0
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            for raw_username, raw_password in credentials.items():
                username = str(raw_username or "").strip()
                password = str(raw_password or "")
                if not username or not password:
                    continue
                password_hash = _streamlit_password_hash(password)
                if not password_hash:
                    continue
                cur.execute(
                    """
                    INSERT INTO auth.streamlit_users
                        (username, password_hash, is_active, updated_at)
                    VALUES
                        (%s, %s, TRUE, NOW())
                    ON CONFLICT ((lower(username)))
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (username, password_hash),
                )
                upserted += 1
            conn.commit()
    except Exception:
        return 0
    return upserted


def _verify_streamlit_db_login(
    db_url: str,
    *,
    username: str,
    password: str,
    session_id: str,
) -> Tuple[bool, str]:
    """Validate one DB-backed login and record session state."""
    if not db_url:
        return False, ""
    entered_user = str(username or "").strip()
    if not entered_user:
        return False, ""
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, username, password_hash
                FROM auth.streamlit_users
                WHERE lower(username) = lower(%s)
                  AND is_active = TRUE
                LIMIT 1
                """,
                (entered_user,),
            )
            row = cur.fetchone()
            if not row:
                return False, ""
            user_id = int(row[0])
            canonical_username = str(row[1] or entered_user).strip() or entered_user
            stored_hash = str(row[2] or "")
            if not _streamlit_password_verify(password, stored_hash):
                return False, ""

            cur.execute(
                "UPDATE auth.streamlit_users SET last_login_at = NOW(), updated_at = NOW() WHERE id = %s",
                (user_id,),
            )
            cur.execute(
                """
                INSERT INTO auth.streamlit_sessions
                    (session_id, user_id, username, source, authenticated_at, revoked_at, updated_at)
                VALUES
                    (%s, %s, %s, 'streamlit_ui', NOW(), NULL, NOW())
                ON CONFLICT (session_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    username = EXCLUDED.username,
                    source = EXCLUDED.source,
                    authenticated_at = EXCLUDED.authenticated_at,
                    revoked_at = NULL,
                    updated_at = NOW()
                """,
                (session_id, user_id, canonical_username),
            )
            conn.commit()
            return True, canonical_username
    except Exception:
        return False, ""


def auth_gate() -> None:
    """Simple username/password gate for the Streamlit app.

    Credential precedence:
    1. Active users in Postgres ``auth.streamlit_users``.
    2. Environment variables ``STREAMLIT_USERS_JSON`` or ``STREAMLIT_USERNAME``/``STREAMLIT_PASSWORD``.
    """
    db_url = _streamlit_auth_db_url()
    db_tables_ready = _streamlit_auth_tables_ready(db_url)
    env_credentials = load_streamlit_credentials()
    session_id = str(st.session_state.setdefault("session_id", uuid4().hex))

    if db_tables_ready and env_credentials and _truthy_env("STREAMLIT_AUTH_BOOTSTRAP_FROM_ENV", True):
        _upsert_streamlit_users_from_env(db_url, env_credentials)

    db_user_count = _streamlit_active_db_user_count(db_url) if db_tables_ready else 0
    use_db_auth = db_tables_ready and db_user_count > 0

    if not use_db_auth and not env_credentials:
        st.sidebar.info(
            "Login disabled (configure auth.streamlit_users or set STREAMLIT_USERS_JSON / STREAMLIT_USERNAME / STREAMLIT_PASSWORD)."
        )
        return

    if st.session_state.get("authenticated"):
        return

    st.sidebar.subheader("Login")
    with st.sidebar.form("login_form"):
        user = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        entered_user = (user or "").strip()
        authenticated = False
        authenticated_user = entered_user

        if use_db_auth:
            authenticated, authenticated_user = _verify_streamlit_db_login(
                db_url,
                username=entered_user,
                password=password,
                session_id=session_id,
            )
        else:
            expected_password = env_credentials.get(entered_user)
            authenticated = bool(
                expected_password and hmac.compare_digest(str(expected_password), str(password or ""))
            )

        if authenticated:
            st.session_state.authenticated = True
            st.session_state.authenticated_user = authenticated_user
            st.sidebar.success("Logged in.")
            return
        st.sidebar.error("Invalid credentials.")

    st.stop()


def main():
    """Run the Streamlit app.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    st.title("Ragonometrics -- Paper Chatbot")

    settings = load_settings()
    auth_gate()
    client = build_llm_runtime(settings)

    st.sidebar.markdown("### Welcome")
    st.sidebar.caption(
        "Ask evidence-grounded questions about one paper at a time. "
        "Select a PDF, use starter prompts, and continue with follow-up questions in chat."
    )
    st.sidebar.markdown("---")
    st.sidebar.header("Settings")
    papers_dir = Path(settings.papers_dir)
    st.sidebar.text_input(
        "Papers directory",
        value=str(papers_dir),
        disabled=True,
        help="Configured by the server environment. This path is read-only in the UI.",
    )
    top_k = st.sidebar.number_input(
        "Top K context chunks",
        value=int(settings.top_k),
        min_value=1,
        max_value=30,
        step=1,
    )
    model_options = [settings.chat_model]
    extra_models = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
    for m in extra_models:
        if m not in model_options:
            model_options.append(m)
    selected_model = st.sidebar.selectbox("LLM model", options=model_options, index=0)

    files = list_papers(papers_dir)

    if not files:
        st.warning(f"No PDF files found in {papers_dir}")
        return

    file_choice = st.selectbox("Select a paper", options=[p.name for p in files])
    selected_path = next(p for p in files if p.name == file_choice)

    with st.spinner("Loading and preparing paper..."):
        paper, chunks, chunk_embeddings = load_and_prepare(selected_path, settings)

    st.subheader(paper.title)
    st.caption(f"Author: {paper.author} -- {paper.path.name}")

    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    if openalex_context or citec_context:
        with st.expander("External Metadata", expanded=False):
            if openalex_context:
                st.markdown("**OpenAlex**")
                st.code(openalex_context, language="text")
            if citec_context:
                st.markdown("**CitEc**")
                st.code(citec_context, language="text")

    if not chunks:
        st.info("No text could be extracted from this PDF.")
        return

    if "history" not in st.session_state:
        st.session_state.history = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid4().hex
        st.session_state.session_started_at = datetime.now(timezone.utc).isoformat()
    if "last_request_id" not in st.session_state:
        st.session_state.last_request_id = None

    if st.sidebar.button("Clear chat history"):
        st.session_state.history = []

    retrieval_settings = settings
    if int(top_k) != settings.top_k:
        retrieval_settings = replace(settings, top_k=int(top_k))

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Paper Questions**")
    st.sidebar.caption(
        "Starter prompts for the selected paper. Click any question to send it to chat, "
        "then continue with follow-ups in your own words."
    )
    st.sidebar.caption(f"Current paper: `{selected_path.name}`")
    starter_questions = suggested_paper_questions(paper)
    for idx, starter in enumerate(starter_questions):
        if st.sidebar.button(starter, key=f"paper_starter_{selected_path.name}_{idx}", use_container_width=True):
            st.session_state["queued_query"] = starter

    tab_chat, tab_structured, tab_metadata, tab_network, tab_usage = st.tabs(
        ["Chat", "Structured Workstream", "OpenAlex Metadata", "Citation Network", "Usage"]
    )

    with tab_chat:
        st.caption("Conversation mode is on. Follow-up questions use recent chat turns for continuity.")
        use_variation = st.toggle(
            "Variation mode",
            value=False,
            help="Use a higher temperature for slightly different wording.",
        )
        with st.container():
            query = st.chat_input("Ask a question about this paper")
            queued_query = st.session_state.pop("queued_query", None)
            if not query and queued_query:
                query = str(queued_query)
            rendered_current_turn = False
            if query:
                request_id = uuid4().hex
                st.session_state.last_request_id = request_id

                with st.chat_message("user"):
                    st.markdown(query)

                with st.chat_message("assistant"):
                    answer_placeholder = st.empty()
                    with st.spinner("Retrieving context and querying model..."):
                        context = top_k_context(
                            chunks,
                            chunk_embeddings,
                            query=query,
                            client=client,
                            settings=retrieval_settings,
                            paper_path=paper.path,
                            session_id=st.session_state.session_id,
                            request_id=request_id,
                        )

                        temperature = None
                        cache_allowed = True
                        if use_variation:
                            cache_allowed = False
                            try:
                                temperature = float(os.getenv("RAG_VARIATION_TEMPERATURE", "0.7"))
                            except Exception:
                                temperature = 0.7

                        cached = None
                        cache_key = None
                        try:
                            history_turns = max(1, int(os.getenv("CHAT_HISTORY_TURNS", "6")))
                        except Exception:
                            history_turns = 6
                        history_context = build_chat_history_context(
                            st.session_state.history,
                            paper_path=paper.path,
                            max_turns=history_turns,
                        )

                        if cache_allowed:
                            cache_context = context
                            if history_context:
                                cache_context = f"Conversation History:\n{history_context}\n\n{context}"
                            cache_key = make_cache_key(query, str(paper.path), selected_model, cache_context)
                            cached = get_cached_answer(DEFAULT_CACHE_PATH, cache_key)

                        if cached is not None:
                            answer = cached
                        else:
                            openalex_context = format_openalex_context(paper.openalex)
                            citec_context = format_citec_context(paper.citec)
                            user_input_parts = []
                            if history_context:
                                user_input_parts.append(
                                    "Prior conversation (for continuity; prefer current question + evidence context if conflicts):\n"
                                    f"{history_context}"
                                )
                            user_input_parts.append(f"Context:\n{context}\n\nQuestion: {query}")
                            user_input = "\n\n".join(user_input_parts)
                            prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
                            if prefix_parts:
                                prefix = "\n\n".join(prefix_parts)
                                user_input = f"{prefix}\n\n{user_input}"

                            try:
                                answer = stream_llm_answer(
                                    client=client,
                                    model=selected_model,
                                    instructions=RESEARCHER_QA_PROMPT,
                                    user_input=user_input,
                                    temperature=temperature,
                                    usage_context="answer",
                                    session_id=st.session_state.session_id,
                                    request_id=request_id,
                                    on_delta=lambda txt: answer_placeholder.markdown(txt + "|"),
                                )
                            except Exception as exc:
                                err = str(exc).lower()
                                if temperature is not None and "temperature" in err and "unsupported" in err:
                                    st.warning(
                                        "The selected model does not support temperature. "
                                        "Retrying without variation."
                                    )
                                    answer = stream_llm_answer(
                                        client=client,
                                        model=selected_model,
                                        instructions=RESEARCHER_QA_PROMPT,
                                        user_input=user_input,
                                        temperature=None,
                                        usage_context="answer",
                                        session_id=st.session_state.session_id,
                                        request_id=request_id,
                                        on_delta=lambda txt: answer_placeholder.markdown(txt + "|"),
                                    )
                                else:
                                    raise

                        reviewed_answer = maybe_review_math_latex(
                            client=client,
                            answer=answer,
                            source_model=selected_model,
                            session_id=st.session_state.session_id,
                            request_id=request_id,
                        )
                        if reviewed_answer:
                            answer = reviewed_answer
                        answer_placeholder.markdown(answer)

                        if cache_allowed and cache_key is not None:
                            set_cached_answer(
                                DEFAULT_CACHE_PATH,
                                cache_key=cache_key,
                                query=query,
                                paper_path=str(paper.path),
                                model=selected_model,
                                context=context,
                                answer=answer,
                            )
                            _db_cached_answers_for_paper.clear()
                            _db_workflow_structured_answers_for_paper.clear()

                        citations = parse_context_chunks(context)
                        if citations:
                            with st.expander("Snapshots", expanded=False):
                                st.caption(
                                    f"Showing {len(citations)} chunks (top_k={retrieval_settings.top_k}, total_chunks={len(chunks)})"
                                )
                                tab_labels = []
                                for c_idx, c in enumerate(citations, start=1):
                                    page = c.get("page")
                                    suffix = f" (p{page})" if page else ""
                                    tab_labels.append(f"Citation {c_idx}{suffix}")
                                tabs = st.tabs(tab_labels)
                                for c_idx, (tab, c) in enumerate(zip(tabs, citations), start=1):
                                    with tab:
                                        key_prefix = f"citation_{request_id}_{c_idx}"
                                        render_citation_snapshot(paper.path, c, key_prefix=key_prefix, query=query)

                    st.session_state.history.append(
                        {
                            "query": query,
                            "answer": answer,
                            "context": context,
                            "citations": citations,
                            "paper_path": str(paper.path),
                            "request_id": request_id,
                        }
                    )
                    rendered_current_turn = True
                    scroll_chat_to_top()

            if st.session_state.history:
                history_items = list(reversed(st.session_state.history))
                if rendered_current_turn and history_items:
                    # The latest turn was already rendered above while streaming.
                    history_items = history_items[1:]
                for item in history_items:
                    q = ""
                    a = ""
                    citations: List[dict] = []
                    citation_path = paper.path
                    request_id = None
                    if isinstance(item, tuple):
                        if len(item) >= 2:
                            q = str(item[0] or "")
                            a = str(item[1] or "")
                    elif isinstance(item, dict):
                        q = str(item.get("query") or "")
                        a = str(item.get("answer") or "")
                        context = item.get("context")
                        citations = item.get("citations")
                        item_paper_path = item.get("paper_path")
                        request_id = item.get("request_id")
                        if context:
                            citations = parse_context_chunks(context)
                        elif citations is None:
                            citations = []
                        if item_paper_path:
                            citation_path = Path(item_paper_path)

                    history_id = request_id
                    if not history_id:
                        token = f"{q}|{a}"
                        history_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]

                    with st.chat_message("user"):
                        st.markdown(q)
                    with st.chat_message("assistant"):
                        st.markdown(a)
                        if citations:
                            with st.expander("Snapshots", expanded=False):
                                st.caption(
                                    f"Showing {len(citations)} chunks (top_k={retrieval_settings.top_k}, total_chunks={len(chunks)})"
                                )
                                tab_labels = []
                                for c_idx, c in enumerate(citations, start=1):
                                    page = c.get("page")
                                    suffix = f" (p{page})" if page else ""
                                    tab_labels.append(f"Citation {c_idx}{suffix}")
                                tabs = st.tabs(tab_labels)
                                for c_idx, (tab, c) in enumerate(zip(tabs, citations), start=1):
                                    with tab:
                                        key_prefix = f"citation_{history_id}_{c_idx}"
                                        render_citation_snapshot(citation_path, c, key_prefix=key_prefix, query=q or "")

    with tab_structured:
        render_structured_workstream_tab(
            paper=paper,
            chunks=chunks,
            chunk_embeddings=chunk_embeddings,
            client=client,
            settings=retrieval_settings,
            selected_model=selected_model,
        )

    with tab_metadata:
        render_openalex_metadata_tab(paper)

    with tab_network:
        render_openalex_citation_network_tab(paper)

    with tab_usage:
        st.subheader("Token Usage")
        st.caption("Aggregates are computed from Postgres (`observability.token_usage`).")
        _reset_query_timings()

        now = datetime.now(timezone.utc)
        last_24h = (now - timedelta(hours=24)).isoformat()

        total = _timed_call("usage_summary:all_time", get_usage_summary, db_path=DEFAULT_USAGE_DB)
        session_total = _timed_call(
            "usage_summary:session",
            get_usage_summary,
            db_path=DEFAULT_USAGE_DB,
            session_id=st.session_state.session_id,
        )
        recent_total = _timed_call(
            "usage_summary:24h",
            get_usage_summary,
            db_path=DEFAULT_USAGE_DB,
            since=last_24h,
        )

        metrics_cols = st.columns(4)
        metrics_cols[0].metric("Total Tokens (All Time)", f"{total.total_tokens}")
        metrics_cols[1].metric("Total Tokens (Session)", f"{session_total.total_tokens}")
        metrics_cols[2].metric("Total Tokens (24h)", f"{recent_total.total_tokens}")
        metrics_cols[3].metric("Calls (All Time)", f"{total.calls}")

        if st.session_state.last_request_id:
            last_query = _timed_call(
                "usage_summary:last_request",
                db_path=DEFAULT_USAGE_DB,
                fn=get_usage_summary,
                request_id=st.session_state.last_request_id,
            )
            st.metric("Last Query Tokens", f"{last_query.total_tokens}")

        st.markdown("---")
        st.subheader("Usage By Model")
        by_model = _timed_call("usage_by_model", get_usage_by_model, db_path=DEFAULT_USAGE_DB)
        if by_model:
            st.dataframe(by_model, width="stretch")
        else:
            st.info("No usage records yet.")

        st.markdown("---")
        st.subheader("Recent Usage Records")
        recent_limit = st.slider("Recent rows", min_value=50, max_value=1000, value=200, step=50)
        recent = _timed_call("usage_recent", get_recent_usage, db_path=DEFAULT_USAGE_DB, limit=int(recent_limit))
        if recent:
            st.dataframe(recent, width="stretch")
        else:
            st.info("No usage records yet.")

        st.markdown("---")
        show_timings = st.checkbox("Show query timings (debug)", value=False)
        if show_timings:
            rows = st.session_state.get(_QUERY_TIMINGS_KEY, [])
            if rows:
                total_ms = round(sum(float(r.get("elapsed_ms") or 0.0) for r in rows), 2)
                st.caption(f"DB query wall time (this render): {total_ms} ms")
                st.dataframe(rows, width="stretch")
            else:
                st.info("No timing rows captured yet.")


if __name__ == "__main__":
    main()
