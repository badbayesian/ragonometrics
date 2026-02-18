"""Cache inspection helpers for chat and structured web surfaces."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from ragonometrics.core.main import top_k_context
from ragonometrics.db.connection import pooled_connection
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.pipeline.query_cache import (
    DEFAULT_CACHE_PATH,
    get_cached_answer,
    get_cached_answer_by_normalized_query,
    make_cache_key,
    normalize_query_for_cache,
)
from ragonometrics.services.chat import build_chat_history_context
from ragonometrics.services.papers import PaperRef, normalize_paper_path, load_prepared
from ragonometrics.services.structured import (
    db_workflow_question_records_for_paper,
    db_workflow_structured_answers_for_paper,
    normalize_question_key,
    structured_report_questions,
)

_INVALID_CHAT_ANSWER_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)


def _is_valid_answer(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return not any(pattern.search(text) for pattern in _INVALID_CHAT_ANSWER_PATTERNS)


def _preview(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 3)].rstrip() + "..."


def _cache_row_by_key(cache_key: str) -> Dict[str, Any]:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url or not cache_key:
        return {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT cache_key, query, query_normalized, paper_path, model, context_hash, answer, created_at
                FROM retrieval.query_cache
                WHERE cache_key = %s
                LIMIT 1
                """,
                (cache_key,),
            )
            row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    return {
        "cache_key": str(row[0] or ""),
        "query": str(row[1] or ""),
        "query_normalized": str(row[2] or ""),
        "paper_path": str(row[3] or ""),
        "model": str(row[4] or ""),
        "context_hash": str(row[5] or ""),
        "answer_preview": _preview(row[6]),
        "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7] or ""),
        "valid_answer": _is_valid_answer(row[6]),
    }


def _cache_row_by_normalized_query(*, query_normalized: str, paper_path: str, model: str) -> Dict[str, Any]:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url or not query_normalized:
        return {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT cache_key, query, query_normalized, paper_path, model, context_hash, answer, created_at
                FROM retrieval.query_cache
                WHERE query_normalized = %s
                  AND paper_path = %s
                  AND model = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (query_normalized, normalize_paper_path(paper_path), str(model or "")),
            )
            row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    return {
        "cache_key": str(row[0] or ""),
        "query": str(row[1] or ""),
        "query_normalized": str(row[2] or ""),
        "paper_path": str(row[3] or ""),
        "model": str(row[4] or ""),
        "context_hash": str(row[5] or ""),
        "answer_preview": _preview(row[6]),
        "created_at": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7] or ""),
        "valid_answer": _is_valid_answer(row[6]),
    }


def inspect_chat_cache(
    *,
    paper_ref: PaperRef,
    question: str,
    model: Optional[str] = None,
    top_k: Optional[int] = None,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Inspect strict and normalized chat cache paths for one query."""
    paper, chunks, chunk_embeddings, settings = load_prepared(paper_ref)
    runtime = build_llm_runtime(settings)
    selected_model = str(model or settings.chat_model)
    retrieved_top_k = int(top_k or settings.top_k)
    retrieval_settings = (
        settings if retrieved_top_k == settings.top_k else settings.__class__(**{**settings.__dict__, "top_k": retrieved_top_k})
    )
    history_context = build_chat_history_context(
        list(history or []),
        paper_path=paper_ref.path,
        max_turns=max(1, int(os.getenv("CHAT_HISTORY_TURNS", "6"))),
    )
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=str(question or ""),
        client=runtime,
        settings=retrieval_settings,
        paper_path=paper.path,
        return_stats=True,
    )
    cache_context = context if not history_context else f"Conversation History:\n{history_context}\n\n{context}"
    cache_key = make_cache_key(str(question or ""), paper_ref.path, selected_model, cache_context)
    query_normalized = normalize_query_for_cache(str(question or ""))
    strict_raw = get_cached_answer(DEFAULT_CACHE_PATH, cache_key)
    fallback_raw = (
        get_cached_answer_by_normalized_query(
            DEFAULT_CACHE_PATH,
            query=str(question or ""),
            paper_path=paper_ref.path,
            model=selected_model,
        )
        if query_normalized
        else None
    )
    strict_hit = strict_raw is not None and _is_valid_answer(strict_raw)
    fallback_hit = fallback_raw is not None and _is_valid_answer(fallback_raw)
    if strict_hit:
        selected_layer = "strict"
        miss_reason = ""
    elif fallback_hit:
        selected_layer = "fallback"
        miss_reason = ""
    elif strict_raw is not None and not _is_valid_answer(strict_raw):
        selected_layer = "none"
        miss_reason = "invalid_strict_cached_answer"
    elif fallback_raw is not None and not _is_valid_answer(fallback_raw):
        selected_layer = "none"
        miss_reason = "invalid_normalized_cached_answer"
    else:
        selected_layer = "none"
        miss_reason = "strict_and_normalized_miss"

    strict_row = _cache_row_by_key(cache_key)
    fallback_row = _cache_row_by_normalized_query(
        query_normalized=query_normalized,
        paper_path=paper_ref.path,
        model=selected_model,
    )
    return {
        "paper_id": paper_ref.paper_id,
        "paper_path": paper_ref.path,
        "question": str(question or ""),
        "query_normalized": query_normalized,
        "model": selected_model,
        "top_k": int(retrieved_top_k),
        "cache_key": cache_key,
        "cache_context_hash": hashlib.sha256(cache_context.encode("utf-8")).hexdigest(),
        "selected_layer": selected_layer,
        "cache_miss_reason": miss_reason,
        "strict_hit": bool(strict_hit),
        "fallback_hit": bool(fallback_hit),
        "strict_row": strict_row,
        "fallback_row": fallback_row,
        "retrieval_stats": retrieval_stats if isinstance(retrieval_stats, dict) else {},
        "context_preview": _preview(context, max_chars=500),
    }


def inspect_structured_cache(*, paper_ref: PaperRef, model: Optional[str] = None) -> Dict[str, Any]:
    """Inspect structured-question cache coverage for one paper."""
    selected_model = str(model or "").strip() or None
    questions = structured_report_questions()
    cached_map = db_workflow_structured_answers_for_paper(paper_ref.path, model=selected_model)
    records = db_workflow_question_records_for_paper(paper_ref.path, model=selected_model)
    rows: List[Dict[str, Any]] = []
    cached_count = 0
    missing_ids: List[str] = []
    for item in questions:
        question_text = str(item.get("question") or "")
        qkey = normalize_question_key(question_text)
        hit = cached_map.get(qkey) or {}
        run_id_text = str(hit.get("run_id") or "")
        qid_text = str(hit.get("question_id") or "")
        record = {}
        if run_id_text and qid_text:
            record = records.get(f"{run_id_text}::{qid_text}") or {}
        if run_id_text and not record:
            record = records.get(f"{run_id_text}::q::{qkey}") or {}
        if not record:
            record = records.get(f"q::{qkey}") or {}
        has_cache = bool(hit) and bool(str(hit.get("answer") or "").strip())
        if has_cache:
            cached_count += 1
        else:
            missing_ids.append(str(item.get("id") or ""))
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "category": str(item.get("category") or ""),
                "question": question_text,
                "cached": has_cache,
                "run_id": run_id_text,
                "question_id": qid_text,
                "source": str(hit.get("source") or ""),
                "model": str(hit.get("model") or ""),
                "cached_at": str(hit.get("created_at") or ""),
                "answer_preview": _preview(hit.get("answer") or ""),
                "record_status": str(record.get("status") or ""),
                "record_step": str(record.get("step") or ""),
                "record_key": str(record.get("record_key") or ""),
            }
        )
    total = len(questions)
    ratio = (float(cached_count) / float(total)) if total else 0.0
    return {
        "paper_id": paper_ref.paper_id,
        "paper_path": paper_ref.path,
        "model": str(selected_model or ""),
        "total_questions": total,
        "cached_questions": cached_count,
        "missing_questions": max(0, total - cached_count),
        "coverage_ratio": ratio,
        "missing_question_ids": missing_ids,
        "rows": rows,
    }

