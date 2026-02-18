"""Structured workstream services shared by web/API surfaces."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from ragonometrics.core.main import top_k_context
from ragonometrics.core.prompts import RESEARCHER_QA_PROMPT
from ragonometrics.db.connection import pooled_connection
from ragonometrics.integrations.citec import format_citec_context
from ragonometrics.integrations.openalex import format_openalex_context
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.pipeline import call_llm
from ragonometrics.pipeline.query_cache import DEFAULT_CACHE_PATH, make_cache_key, profile_hash, set_cached_answer_hybrid
from ragonometrics.services.chat import parse_context_chunks
from ragonometrics.services.papers import PaperRef, load_prepared, normalize_paper_path

_INVALID_STRUCTURED_QUESTION_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)


def structured_report_questions() -> List[Dict[str, str]]:
    """Load canonical structured report questions from workflow module."""
    from ragonometrics.pipeline.workflow import _build_report_questions

    return _build_report_questions()


def normalize_question_key(value: Any) -> str:
    """Normalize question text for stable key lookups."""
    return " ".join(str(value or "").strip().split())


def is_valid_structured_question_text(value: Any) -> bool:
    """Return whether question text is valid for persistence."""
    text = normalize_question_key(value)
    if not text or len(text) > 600:
        return False
    return not any(p.search(text) for p in _INVALID_STRUCTURED_QUESTION_PATTERNS)


def _streamlit_confidence_from_retrieval_stats(stats: Dict[str, Any]) -> Tuple[str, float, str]:
    method = str(stats.get("method") or "unknown")
    raw_score = stats.get("score_mean_norm")
    if not isinstance(raw_score, (int, float)):
        raw_score = stats.get("score_mean")
    try:
        score = float(raw_score)
    except Exception:
        score = 0.0
    score = min(1.0, max(0.0, score))
    if score >= 0.75:
        label = "high"
    elif score >= 0.45:
        label = "medium"
    else:
        label = "low"
    return label, score, method


def structured_fields_from_context(
    *,
    question: str,
    answer: str,
    context: str,
    retrieval_stats: Dict[str, Any],
    top_k: int,
) -> Dict[str, Any]:
    """Build structured fields payload for generated answers."""
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
    for chunk in parsed_chunks:
        txt = str(chunk.get("text") or "").strip()
        if txt:
            quote_snippet = txt[:220]
            break
    return {
        "question_tokens_estimate": len(str(question or "").split()),
        "evidence_type": "retrieved_context",
        "confidence": confidence,
        "confidence_score": confidence_score,
        "retrieval_method": retrieval_method,
        "citation_anchors": anchors,
        "quote_snippet": quote_snippet,
        "answer_length_chars": len(str(answer or "")),
    }


def _structured_run_id(*, paper_path: str, model: str) -> str:
    key = f"{paper_path}||{model}"
    return f"flask-structured-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"


def _input_hash(*, paper_path: str, model: str, question_id: str, question: str) -> str:
    payload = f"{paper_path}||{model}||{question_id}||{normalize_question_key(question)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_idempotent_answer(
    *,
    db_url: str,
    run_id: str,
    question_id: str,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    if not idempotency_key:
        return None
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT payload_json, created_at
            FROM workflow.run_records
            WHERE run_id = %s
              AND record_kind = 'question'
              AND step = 'agentic'
              AND record_key = %s
              AND idempotency_key = %s
            LIMIT 1
            """,
            (run_id, question_id, idempotency_key),
        )
        row = cur.fetchone()
        if not row:
            return None
        payload = row[0] if isinstance(row[0], dict) else {}
        answer = str(payload.get("answer") or "")
        if not answer:
            return None
        return {
            "answer": answer,
            "created_at": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1] or ""),
        }


def upsert_workflow_structured_answer(
    *,
    paper_path: str,
    selected_model: str,
    question_id: str,
    category: str,
    question: str,
    answer: str,
    structured_fields: Optional[Dict[str, Any]] = None,
    idempotency_key: str = "",
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
) -> Dict[str, str]:
    """Persist one structured answer into workflow ledger."""
    if not is_valid_structured_question_text(question):
        return {}
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}

    normalized_path = normalize_paper_path(paper_path)
    run_id = _structured_run_id(paper_path=normalized_path, model=selected_model)
    question_id_clean = str(question_id or "").strip() or hashlib.sha256(normalize_question_key(question).encode("utf-8")).hexdigest()[:10]
    idem = str(idempotency_key or "").strip()
    try:
        existing = _existing_idempotent_answer(
            db_url=db_url,
            run_id=run_id,
            question_id=question_id_clean,
            idempotency_key=idem,
        )
    except Exception:
        existing = None
    if existing:
        return {"run_id": run_id, "question_id": question_id_clean, "created_at": str(existing.get("created_at") or "")}

    created_at = datetime.now(timezone.utc).isoformat()
    question_payload: Dict[str, Any] = {
        "id": question_id_clean,
        "category": str(category or ""),
        "question": str(question or ""),
        "answer": str(answer or ""),
        "source": "workflow.run_records",
        "model": selected_model,
        "cached_at": created_at,
    }
    if isinstance(structured_fields, dict):
        question_payload.update(structured_fields)
    question_meta = {
        "source": "flask_structured_workstream",
        "category": str(category or ""),
        "model": selected_model,
        "idempotency_key": idem,
    }
    inp_hash = _input_hash(
        paper_path=normalized_path,
        model=selected_model,
        question_id=question_id_clean,
        question=question,
    )
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO workflow.run_records
                (
                    run_id, record_kind, step, record_key, status,
                    papers_dir, arm, trigger_source,
                    project_id, persona_id,
                    config_effective_json, report_question_set,
                    started_at, finished_at, created_at, updated_at,
                    payload_json, metadata_json
                )
                VALUES (
                    %s, 'run', '', 'main', 'completed',
                    %s, %s, %s, %s, %s,
                    %s::jsonb, %s,
                    %s, %s, NOW(), NOW(),
                    %s::jsonb, %s::jsonb
                )
                ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    project_id = COALESCE(EXCLUDED.project_id, workflow.run_records.project_id),
                    persona_id = COALESCE(EXCLUDED.persona_id, workflow.run_records.persona_id),
                    payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
                    metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    run_id,
                    normalized_path,
                    selected_model,
                    "flask_structured_workstream",
                    str(project_id or "").strip() or None,
                    str(persona_id or "").strip() or None,
                    json.dumps({"chat_model": selected_model}, ensure_ascii=False),
                    "structured",
                    created_at,
                    created_at,
                    json.dumps({"source": "flask_structured_workstream"}, ensure_ascii=False),
                    json.dumps({"source": "flask_structured_workstream", "model": selected_model}, ensure_ascii=False),
                ),
            )
            cur.execute(
                """
                INSERT INTO workflow.run_records
                (
                    run_id, record_kind, step, record_key, status,
                    question_id, report_question_set, idempotency_key, input_hash,
                    project_id, persona_id,
                    created_at, updated_at, payload_json, metadata_json
                )
                VALUES (
                    %s, 'question', 'agentic', %s, %s,
                    %s, %s, %s, %s, %s, %s, NOW(), NOW(), %s::jsonb, %s::jsonb
                )
                ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    question_id = EXCLUDED.question_id,
                    report_question_set = EXCLUDED.report_question_set,
                    idempotency_key = COALESCE(EXCLUDED.idempotency_key, workflow.run_records.idempotency_key),
                    input_hash = COALESCE(EXCLUDED.input_hash, workflow.run_records.input_hash),
                    project_id = COALESCE(EXCLUDED.project_id, workflow.run_records.project_id),
                    persona_id = COALESCE(EXCLUDED.persona_id, workflow.run_records.persona_id),
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
                    idem or None,
                    inp_hash,
                    str(project_id or "").strip() or None,
                    str(persona_id or "").strip() or None,
                    json.dumps(question_payload, ensure_ascii=False),
                    json.dumps(question_meta, ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception:
        return {}
    return {"run_id": run_id, "question_id": question_id_clean, "created_at": created_at}


def db_workflow_structured_answers_for_paper(
    paper_path: str,
    model: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Load latest structured answers for one paper."""
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}
    normalized_path = normalize_paper_path(paper_path)
    basename_suffix = f"%/{Path(normalized_path).name.lower()}"
    scoped_project = str(project_id or "").strip()
    out: Dict[str, Dict[str, str]] = {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            sql = """
                SELECT
                    q.run_id,
                    COALESCE(q.question_id, q.payload_json->>'id', '') AS question_id,
                    COALESCE(q.payload_json->>'question', q.output_json->>'question', '') AS question_text,
                    COALESCE(q.payload_json->>'answer', q.output_json->>'answer', '') AS answer_text,
                    q.created_at,
                    COALESCE(r.config_effective_json->>'chat_model', r.arm, '') AS model_label
                FROM workflow.run_records q
                JOIN workflow.run_records r
                  ON r.run_id = q.run_id
                 AND r.record_kind = 'run'
                 AND r.step = ''
                 AND r.record_key = 'main'
                WHERE q.record_kind = 'question'
                  AND COALESCE(q.payload_json->>'question', q.output_json->>'question', '') <> ''
                  AND (%s = '' OR COALESCE(r.project_id, '') = %s)
                  AND (
                        lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) = lower(%s)
                     OR lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) LIKE %s
                  )
            """
            params: List[Any] = [scoped_project, scoped_project, normalized_path, basename_suffix]
            if model:
                sql += " AND (COALESCE(r.config_effective_json->>'chat_model', '') = %s OR COALESCE(r.arm, '') = %s)"
                params.extend([model, model])
            sql += " ORDER BY q.created_at DESC"
            cur.execute(sql, tuple(params))
            for run_id, question_id, question_text, answer_text, created_at, model_label in cur.fetchall():
                key = normalize_question_key(question_text)
                if not key or key in out:
                    continue
                out[key] = {
                    "answer": str(answer_text or ""),
                    "model": str(model_label or ""),
                    "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                    "source": "workflow.run_records",
                    "run_id": str(run_id or ""),
                    "question_id": str(question_id or ""),
                }
    except Exception:
        return {}
    return out


def db_workflow_question_records_for_paper(
    paper_path: str,
    model: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load detailed question records indexed by run/question and global question keys."""
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return {}
    normalized_path = normalize_paper_path(paper_path)
    basename_suffix = f"%/{Path(normalized_path).name.lower()}"
    scoped_project = str(project_id or "").strip()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            sql = """
                SELECT
                    q.run_id,
                    COALESCE(q.question_id, q.payload_json->>'id', '') AS question_id,
                    COALESCE(q.payload_json->>'question', q.output_json->>'question', '') AS question_text,
                    q.status, q.step, q.record_key, q.created_at, q.updated_at,
                    q.payload_json, q.output_json, q.metadata_json
                FROM workflow.run_records q
                JOIN workflow.run_records r
                  ON r.run_id = q.run_id
                 AND r.record_kind = 'run'
                 AND r.step = ''
                 AND r.record_key = 'main'
                WHERE q.record_kind = 'question'
                  AND COALESCE(q.payload_json->>'question', q.output_json->>'question', '') <> ''
                  AND (%s = '' OR COALESCE(r.project_id, '') = %s)
                  AND (
                        lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) = lower(%s)
                     OR lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) LIKE %s
                  )
            """
            params: List[Any] = [scoped_project, scoped_project, normalized_path, basename_suffix]
            if model:
                sql += " AND (COALESCE(r.config_effective_json->>'chat_model', '') = %s OR COALESCE(r.arm, '') = %s)"
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
            ) in cur.fetchall():
                run_id_text = str(run_id or "")
                qid = str(question_id or "")
                qkey = normalize_question_key(question_text)
                record = {
                    "run_id": run_id_text,
                    "question_id": qid,
                    "question_text": str(question_text or ""),
                    "status": str(status or ""),
                    "step": str(step or ""),
                    "record_key": str(record_key or ""),
                    "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or ""),
                    "payload_json": payload_json if isinstance(payload_json, dict) else {},
                    "output_json": output_json if isinstance(output_json, dict) else {},
                    "metadata_json": metadata_json if isinstance(metadata_json, dict) else {},
                }
                if run_id_text and qid:
                    out.setdefault(f"{run_id_text}::{qid}", record)
                if run_id_text and qkey:
                    out.setdefault(f"{run_id_text}::q::{qkey}", record)
                if qkey:
                    out.setdefault(f"q::{qkey}", record)
    except Exception:
        return {}
    return out


def _payload_has_full_structured_fields(payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        isinstance(payload.get("citation_anchors"), list)
        and payload.get("confidence_score") is not None
        and bool(str(payload.get("retrieval_method") or "").strip())
    )


def _structured_payload_from_question_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    output_obj = record.get("output_json") if isinstance(record.get("output_json"), dict) else {}
    return output_obj or (record.get("payload_json") if isinstance(record.get("payload_json"), dict) else {})


def generate_and_cache_structured_answer(
    *,
    paper_ref: PaperRef,
    question_id: str,
    category: str,
    question: str,
    selected_model: Optional[str] = None,
    session_id: Optional[str] = None,
    top_k: Optional[int] = None,
    idempotency_key: str = "",
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate one structured answer and persist to cache + run records."""
    if not is_valid_structured_question_text(question):
        return {"answer": "", "error": "invalid_question"}
    paper, chunks, chunk_embeddings, settings = load_prepared(paper_ref)
    if not chunks:
        return {"answer": "", "error": "no_chunks"}
    runtime = build_llm_runtime(settings)
    model = str(selected_model or settings.chat_model)
    retrieval_settings = settings
    if top_k is not None and int(top_k) != int(settings.top_k):
        retrieval_settings = settings.__class__(**{**settings.__dict__, "top_k": int(top_k)})
    request_id = uuid4().hex
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=question,
        client=runtime,
        settings=retrieval_settings,
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
    answer = call_llm(
        runtime,
        model=model,
        instructions=RESEARCHER_QA_PROMPT,
        user_input=user_input,
        max_output_tokens=None,
        temperature=None,
        usage_context="structured_workstream_answer",
        meta={"project_id": project_id, "persona_id": persona_id},
        session_id=session_id,
        request_id=request_id,
        step="structured_workstream_answer",
        question_id=str(question_id or ""),
    ).strip()
    cache_key = make_cache_key(question, paper_ref.path, model, context)
    set_cached_answer_hybrid(
        DEFAULT_CACHE_PATH,
        cache_key=cache_key,
        query=question,
        paper_path=paper_ref.path,
        model=model,
        context=context,
        answer=answer,
        project_id=project_id,
        user_id=user_id,
        source_project_id=project_id,
        prompt_profile_hash=profile_hash(RESEARCHER_QA_PROMPT),
        retrieval_profile_hash=profile_hash(f"top_k={int(retrieval_settings.top_k)}"),
        persona_profile_hash=profile_hash(str(persona_id or "default")),
        allow_custom_question_sharing=bool(allow_custom_question_sharing),
    )
    persisted = upsert_workflow_structured_answer(
        paper_path=paper_ref.path,
        selected_model=model,
        question_id=question_id,
        category=category,
        question=question,
        answer=answer,
        structured_fields=structured_fields_from_context(
            question=question,
            answer=answer,
            context=context,
            retrieval_stats=retrieval_stats if isinstance(retrieval_stats, dict) else {},
            top_k=int(retrieval_settings.top_k),
        ),
        idempotency_key=idempotency_key,
        project_id=project_id,
        persona_id=persona_id,
    )
    return {
        "answer": answer,
        "request_id": request_id,
        "run_id": str(persisted.get("run_id") or ""),
        "question_id": str(persisted.get("question_id") or question_id),
        "created_at": str(persisted.get("created_at") or ""),
        "retrieval_stats": retrieval_stats if isinstance(retrieval_stats, dict) else {},
    }


def generate_missing_structured_answers(
    *,
    paper_ref: PaperRef,
    selected_model: Optional[str] = None,
    session_id: Optional[str] = None,
    top_k: Optional[int] = None,
    question_ids: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate structured answers for missing questions."""
    model = str(selected_model or "")
    questions = structured_report_questions()
    if question_ids:
        wanted = {str(qid or "").strip() for qid in question_ids if str(qid or "").strip()}
        questions = [q for q in questions if str(q.get("id") or "").strip() in wanted]
    cached_map = db_workflow_structured_answers_for_paper(
        paper_ref.path,
        model=model or None,
        project_id=project_id,
    )
    generated: List[Dict[str, Any]] = []
    skipped = 0
    for item in questions:
        q = str(item.get("question") or "")
        qkey = normalize_question_key(q)
        if qkey in cached_map:
            skipped += 1
            continue
        out = generate_and_cache_structured_answer(
            paper_ref=paper_ref,
            question_id=str(item.get("id") or ""),
            category=str(item.get("category") or ""),
            question=q,
            selected_model=model or None,
            session_id=session_id,
            top_k=top_k,
            idempotency_key=f"generate-missing::{paper_ref.paper_id}::{item.get('id')}",
            project_id=project_id,
            persona_id=persona_id,
            allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
            allow_custom_question_sharing=allow_custom_question_sharing,
            user_id=user_id,
        )
        generated.append({"id": item.get("id"), "answer_length": len(str(out.get("answer") or "")), "run_id": out.get("run_id")})
    return {"generated_count": len(generated), "skipped_cached_count": skipped, "generated": generated}


def structured_workstream_export_bundle(
    *,
    paper_ref: PaperRef,
    questions: List[Dict[str, str]],
    cached_map: Dict[str, Dict[str, str]],
    selected_model: str,
    cache_scope: str,
    export_format: str = "compact",
    question_records: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build structured export payload (compact or full)."""
    paper, _, _, _ = load_prepared(paper_ref)
    fmt = "full" if str(export_format or "").strip().lower() == "full" else "compact"
    records = question_records or {}
    rows: List[Dict[str, Any]] = []
    for item in questions:
        question_text = str(item.get("question") or "")
        question_key = normalize_question_key(question_text)
        hit = cached_map.get(question_key) or {}
        row: Dict[str, Any] = {
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
            "answer": str(hit.get("answer") or ""),
            "cached": bool(hit),
            "source": str(hit.get("source") or ("workflow.run_records" if hit else "")),
            "model": str(hit.get("model") or ""),
            "cached_at": str(hit.get("created_at") or ""),
            "run_id": str(hit.get("run_id") or ""),
            "question_id": str(hit.get("question_id") or ""),
        }
        if fmt == "full":
            run_id_text = str(hit.get("run_id") or "").strip()
            qid = str(hit.get("question_id") or "").strip()
            record = {}
            if run_id_text and qid:
                record = records.get(f"{run_id_text}::{qid}") or {}
            if run_id_text and not record:
                record = records.get(f"{run_id_text}::q::{question_key}") or {}
            if not record:
                record = records.get(f"q::{question_key}") or {}
            payload = _structured_payload_from_question_record(record)
            row["workflow_record"] = record
            row["structured_fields"] = {
                "evidence_type": payload.get("evidence_type"),
                "confidence": payload.get("confidence"),
                "confidence_score": payload.get("confidence_score"),
                "retrieval_method": payload.get("retrieval_method"),
                "citation_anchors": payload.get("citation_anchors") if isinstance(payload.get("citation_anchors"), list) else [],
                "quote_snippet": payload.get("quote_snippet"),
            }
            row["has_answer"] = bool(str(row["answer"]).strip())
        rows.append(row)
    bundle: Dict[str, Any] = {
        "export_type": "structured_workstream",
        "export_format": fmt,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "paper": {"path": paper_ref.path, "title": paper.title, "author": paper.author},
        "model_scope": {"selected_model": selected_model, "cache_scope": cache_scope},
        "summary": {
            "total_questions": len(rows),
            "cached_questions": sum(1 for r in rows if r["cached"]),
            "uncached_questions": sum(1 for r in rows if not r["cached"]),
        },
        "questions": rows,
    }
    if fmt == "full":
        bundle["full_summary"] = {
            "rows_with_answer": sum(1 for r in rows if r.get("has_answer")),
            "rows_with_workflow_record": sum(1 for r in rows if bool(r.get("workflow_record"))),
            "rows_with_citation_anchors": sum(
                1
                for r in rows
                if isinstance((r.get("structured_fields") or {}).get("citation_anchors"), list)
                and bool((r.get("structured_fields") or {}).get("citation_anchors"))
            ),
        }
    return bundle


def structured_workstream_pdf_bytes(bundle: Dict[str, Any]) -> Optional[bytes]:
    """Render structured export bundle into PDF bytes."""
    try:
        from fpdf import FPDF
    except Exception:
        return None

    def _pdf_safe_text(value: Any) -> str:
        return str(value or "").encode("latin-1", "replace").decode("latin-1")

    def _pdf_force_wrap_text(pdf: Any, text: Any, width: float) -> str:
        safe_text = _pdf_safe_text(text).replace("\r\n", "\n").replace("\r", "\n")
        if not safe_text:
            return ""
        # Conservative width estimate to force-break unbroken long tokens.
        try:
            sample_width = float(pdf.get_string_width("W"))
        except Exception:
            sample_width = 1.0
        if sample_width <= 0:
            sample_width = 1.0
        max_chars = max(8, int(width / sample_width))
        out_lines: List[str] = []
        for raw_line in safe_text.split("\n"):
            if not raw_line:
                out_lines.append("")
                continue
            fragments: List[str] = []
            for token in re.split(r"(\s+)", raw_line):
                if not token:
                    continue
                if token.isspace():
                    fragments.append(token)
                    continue
                if len(token) <= max_chars and float(pdf.get_string_width(token)) <= width:
                    fragments.append(token)
                    continue
                chunk = ""
                pieces: List[str] = []
                for ch in token:
                    candidate = chunk + ch
                    if len(candidate) <= max_chars and float(pdf.get_string_width(candidate)) <= width:
                        chunk = candidate
                    else:
                        if chunk:
                            pieces.append(chunk)
                        chunk = ch
                if chunk:
                    pieces.append(chunk)
                fragments.append("\n".join(pieces))
            out_lines.append("".join(fragments))
        return "\n".join(out_lines)

    def _pdf_write_wrapped(pdf: Any, *, line_height: float, text: Any) -> None:
        printable_width = float(getattr(pdf, "w", 0.0)) - float(getattr(pdf, "l_margin", 0.0)) - float(
            getattr(pdf, "r_margin", 0.0)
        )
        if printable_width <= 10.0:
            printable_width = 10.0
        pdf.set_x(pdf.l_margin)
        wrapped = _pdf_force_wrap_text(pdf, text, printable_width)
        pdf.multi_cell(printable_width, line_height, wrapped or " ")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    paper = bundle.get("paper") or {}
    summary = bundle.get("summary") or {}
    questions = bundle.get("questions") or []
    model_scope = bundle.get("model_scope") or {}
    export_format = str(bundle.get("export_format") or "compact").strip().lower()
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
        pdf.set_font("Helvetica", "B", 10)
        status = "cached" if item.get("cached") else "uncached"
        _pdf_write_wrapped(pdf, line_height=6, text=f"{item.get('id')}: {item.get('question')} [{status}]")

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
        answer = str(item.get("answer") or "").strip() or "[No cached answer]"
        _pdf_write_wrapped(pdf, line_height=5, text=answer)
        pdf.ln(2)
    payload = pdf.output(dest="S")
    return bytes(payload) if isinstance(payload, (bytes, bytearray)) else str(payload).encode("latin-1", "replace")


def export_bundle_for_paper(
    *,
    paper_ref: PaperRef,
    selected_model: str,
    cache_scope: str,
    export_format: str,
    question_ids: Optional[List[str]] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build export bundle for one paper and optional question-id subset."""
    questions = structured_report_questions()
    if question_ids:
        wanted = {str(q or "").strip() for q in question_ids}
        questions = [q for q in questions if str(q.get("id") or "").strip() in wanted]
    model_filter = selected_model if cache_scope == "Selected model only" else None
    cached_map = db_workflow_structured_answers_for_paper(
        paper_ref.path,
        model=model_filter,
        project_id=project_id,
    )
    records = (
        db_workflow_question_records_for_paper(
            paper_ref.path,
            model=model_filter,
            project_id=project_id,
        )
        if export_format.lower() == "full"
        else {}
    )
    return structured_workstream_export_bundle(
        paper_ref=paper_ref,
        questions=questions,
        cached_map=cached_map,
        selected_model=selected_model,
        cache_scope=cache_scope,
        export_format=export_format,
        question_records=records,
    )
