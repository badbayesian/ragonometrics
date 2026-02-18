"""Multi-paper topic comparison services for web and CLI."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from ragonometrics.core.main import load_settings
from ragonometrics.db.connection import pooled_connection
from ragonometrics.pipeline.query_cache import normalize_query_for_cache
from ragonometrics.services import chat as chat_service
from ragonometrics.services import papers as papers_service
from ragonometrics.services import structured as structured_service

MAX_COMPARE_PAPERS = 10
MAX_COMPARE_QUESTIONS = 50
DEFAULT_SUGGESTION_LIMIT = 20


def _db_url() -> str:
    return str(os.environ.get("DATABASE_URL") or "").strip()


def _to_iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except Exception:
            return str(value or "")
    return str(value or "")


def _json_obj(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _tokenize(text: str) -> List[str]:
    return [item for item in re.sub(r"[^a-z0-9\s]+", " ", str(text or "").lower()).split() if item]


def _keyword_overlap(seed_text: str, candidate_text: str) -> float:
    seed_tokens = set(_tokenize(seed_text))
    cand_tokens = set(_tokenize(candidate_text))
    if not seed_tokens or not cand_tokens:
        return 0.0
    union = seed_tokens | cand_tokens
    if not union:
        return 0.0
    return float(len(seed_tokens & cand_tokens)) / float(len(union))


def _weighted_jaccard(left: Dict[str, float], right: Dict[str, float]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    num = 0.0
    den = 0.0
    for key in keys:
        lv = float(left.get(key, 0.0))
        rv = float(right.get(key, 0.0))
        num += min(lv, rv)
        den += max(lv, rv)
    if den <= 0.0:
        return 0.0
    return num / den


def _extract_topic_signature(meta: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    topics: Dict[str, float] = {}
    concepts: Dict[str, float] = {}
    for item in _json_list(meta.get("topics")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            score = float(item.get("score") or 1.0)
        except Exception:
            score = 1.0
        topics[name] = max(topics.get(name, 0.0), max(0.01, score))
    for item in _json_list(meta.get("concepts")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            score = float(item.get("score") or 1.0)
        except Exception:
            score = 1.0
        concepts[name] = max(concepts.get(name, 0.0), max(0.01, score))
    merged: Dict[str, float] = {}
    for key, value in topics.items():
        merged[f"t:{key}"] = float(value)
    for key, value in concepts.items():
        merged[f"c:{key}"] = float(value) * 0.8
    return topics, concepts, merged


def _openalex_by_path() -> Dict[str, Dict[str, Any]]:
    db_url = _db_url()
    if not db_url:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT paper_path, openalex_json
                FROM enrichment.paper_openalex_metadata
                WHERE match_status = 'matched'
                """
            )
            for paper_path, payload in cur.fetchall():
                normalized = papers_service.normalize_paper_path(str(paper_path or "")).lower()
                meta = _json_obj(payload)
                if normalized and meta:
                    out[normalized] = meta
    except Exception:
        return {}
    return out


def _match_openalex_meta(
    ref: papers_service.PaperRef,
    *,
    by_path: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    normalized = papers_service.normalize_paper_path(ref.path).lower()
    direct = by_path.get(normalized)
    if isinstance(direct, dict) and direct:
        return direct
    basename = Path(ref.path).name.lower()
    if not basename:
        return {}
    for path_text, payload in by_path.items():
        if str(path_text or "").endswith(f"/{basename}") and isinstance(payload, dict):
            return payload
    return {}


def _paper_ref_map(*, project_id: Optional[str] = None) -> Dict[str, papers_service.PaperRef]:
    settings = load_settings()
    refs = papers_service.list_papers(settings=settings)
    scoped_project = str(project_id or "").strip()
    if scoped_project:
        allowed = set(_project_paper_ids(scoped_project))
        if allowed:
            refs = [ref for ref in refs if ref.paper_id in allowed]
    return {ref.paper_id: ref for ref in refs}


def _paper_ref_map_scoped(project_id: Optional[str]) -> Dict[str, papers_service.PaperRef]:
    """Backward-compatible wrapper for tests monkeypatching _paper_ref_map() without kwargs."""
    try:
        return _paper_ref_map(project_id=project_id)
    except TypeError:
        return _paper_ref_map()


def _project_paper_ids(project_id: str) -> List[str]:
    db_url = _db_url()
    if not db_url:
        return []
    wanted = str(project_id or "").strip()
    if not wanted:
        return []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT paper_id
                FROM auth.project_papers
                WHERE project_id = %s
                """,
                (wanted,),
            )
            return [str((row or [""])[0] or "") for row in cur.fetchall() if str((row or [""])[0] or "").strip()]
    except Exception:
        return []


def _overview_map(refs: Sequence[papers_service.PaperRef]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        out[ref.paper_id] = papers_service.paper_overview(ref)
    return out


def suggest_similar_papers(
    seed_paper_id: str,
    limit: int = DEFAULT_SUGGESTION_LIMIT,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ranked paper suggestions by OpenAlex topic/concept + keyword overlap."""
    refs_by_id = _paper_ref_map_scoped(project_id)
    seed_ref = refs_by_id.get(str(seed_paper_id or "").strip())
    if not seed_ref:
        raise ValueError("Unknown paper_id.")
    refs = list(refs_by_id.values())
    overviews = _overview_map(refs)
    meta_map = _openalex_by_path()

    seed_meta = _match_openalex_meta(seed_ref, by_path=meta_map)
    seed_topics, seed_concepts, seed_signature = _extract_topic_signature(seed_meta)
    seed_overview = overviews.get(seed_ref.paper_id) or {}
    seed_text = f"{seed_overview.get('display_title') or seed_overview.get('title') or ''} {seed_overview.get('display_abstract') or ''}"

    bounded_limit = max(1, min(100, int(limit or DEFAULT_SUGGESTION_LIMIT)))
    ranked: List[Dict[str, Any]] = []
    for ref in refs:
        if ref.paper_id == seed_ref.paper_id:
            continue
        cand_meta = _match_openalex_meta(ref, by_path=meta_map)
        cand_topics, cand_concepts, cand_signature = _extract_topic_signature(cand_meta)
        topic_score = _weighted_jaccard(seed_signature, cand_signature)
        cand_overview = overviews.get(ref.paper_id) or {}
        cand_text = f"{cand_overview.get('display_title') or cand_overview.get('title') or ''} {cand_overview.get('display_abstract') or ''}"
        keyword_score = _keyword_overlap(seed_text, cand_text)
        score = (0.7 * topic_score) + (0.3 * keyword_score)
        overlap_topics = sorted(set(seed_topics) & set(cand_topics))[:8]
        overlap_concepts = sorted(set(seed_concepts) & set(cand_concepts))[:8]
        ranked.append(
            {
                "paper_id": ref.paper_id,
                "name": ref.name,
                "path": ref.path,
                "title": cand_overview.get("display_title") or cand_overview.get("title") or ref.name,
                "authors": cand_overview.get("display_authors") or "",
                "openalex_url": cand_overview.get("openalex_url") or "",
                "score": round(float(score), 6),
                "score_breakdown": {
                    "topic_similarity": round(float(topic_score), 6),
                    "keyword_overlap": round(float(keyword_score), 6),
                },
                "overlap_topics": overlap_topics,
                "overlap_concepts": overlap_concepts,
            }
        )
    ranked = sorted(
        ranked,
        key=lambda row: (
            -float(row.get("score") or 0.0),
            str(row.get("title") or "").lower(),
            str(row.get("paper_id") or ""),
        ),
    )[:bounded_limit]
    return {
        "seed_paper_id": seed_ref.paper_id,
        "seed_paper": seed_overview,
        "count": len(ranked),
        "rows": ranked,
    }


def _normalize_questions(questions: Sequence[str]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    seen = set()
    for raw in questions:
        text = structured_service.normalize_question_key(raw)
        if not text:
            continue
        norm = normalize_query_for_cache(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        rows.append({"id": f"Q{len(rows) + 1:02d}", "text": text, "normalized": norm})
    return rows


def _latest_cache_rows(
    *,
    paper_paths: Sequence[str],
    model: str,
    normalized_questions: Sequence[str],
    project_id: Optional[str] = None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    db_url = _db_url()
    if not db_url or not paper_paths or not normalized_questions:
        return {}
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    path_placeholders = ",".join(["%s"] * len(paper_paths))
    q_placeholders = ",".join(["%s"] * len(normalized_questions))
    params: List[Any] = [str(model or "")]
    normalized_paths = [papers_service.normalize_paper_path(path) for path in paper_paths]
    params.extend(normalized_paths)
    params.extend(list(normalized_questions))
    scoped_project = str(project_id or "").strip()
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            if scoped_project:
                try:
                    cur.execute(
                        f"""
                        SELECT paper_path, query_normalized, cache_key, context_hash, answer, created_at
                        FROM retrieval.project_query_cache
                        WHERE project_id = %s
                          AND model = %s
                          AND paper_path IN ({path_placeholders})
                          AND query_normalized IN ({q_placeholders})
                        ORDER BY created_at DESC
                        """,
                        tuple([scoped_project, str(model or ""), *normalized_paths, *list(normalized_questions)]),
                    )
                    for row in cur.fetchall():
                        path = papers_service.normalize_paper_path(str(row[0] or ""))
                        qnorm = str(row[1] or "")
                        key = (path, qnorm)
                        if key in out:
                            continue
                        out[key] = {
                            "paper_path": path,
                            "query_normalized": qnorm,
                            "cache_key": str(row[2] or ""),
                            "context_hash": str(row[3] or ""),
                            "answer": str(row[4] or "").strip(),
                            "created_at": _to_iso(row[5]),
                            "cache_scope": "project",
                        }
                except Exception:
                    pass
            cur.execute(
                f"""
                SELECT paper_path, query_normalized, cache_key, context_hash, answer, created_at
                FROM retrieval.query_cache
                WHERE model = %s
                  AND paper_path IN ({path_placeholders})
                  AND query_normalized IN ({q_placeholders})
                ORDER BY created_at DESC
                """,
                tuple(params),
            )
            for row in cur.fetchall():
                path = papers_service.normalize_paper_path(str(row[0] or ""))
                qnorm = str(row[1] or "")
                key = (path, qnorm)
                if key in out:
                    continue
                out[key] = {
                    "paper_path": path,
                    "query_normalized": qnorm,
                    "cache_key": str(row[2] or ""),
                    "context_hash": str(row[3] or ""),
                    "answer": str(row[4] or "").strip(),
                    "created_at": _to_iso(row[5]),
                    "cache_scope": "shared",
                }
    except Exception:
        return {}
    return out


def _canonical_structured_map() -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for item in structured_service.structured_report_questions():
        question = str(item.get("question") or "")
        normalized = normalize_query_for_cache(question)
        if not normalized or normalized in out:
            continue
        out[normalized] = {
            "id": str(item.get("id") or ""),
            "category": str(item.get("category") or ""),
            "question": question,
        }
    return out


def _structured_fields_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = record.get("output_json") if isinstance(record.get("output_json"), dict) else {}
    if not payload:
        payload = record.get("payload_json") if isinstance(record.get("payload_json"), dict) else {}
    if not isinstance(payload, dict):
        return {}
    return {
        "confidence_score": payload.get("confidence_score"),
        "retrieval_method": payload.get("retrieval_method"),
        "citation_anchors": payload.get("citation_anchors") if isinstance(payload.get("citation_anchors"), list) else [],
        "quote_snippet": payload.get("quote_snippet"),
    }


def _build_summary(cells: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(cells)
    counts = {"cached": 0, "missing": 0, "generated": 0, "failed": 0}
    for row in cells:
        status = str(row.get("cell_status") or "")
        if status in counts:
            counts[status] += 1
    return {
        "total_cells": total,
        "cached_cells": counts["cached"],
        "missing_cells": counts["missing"],
        "generated_cells": counts["generated"],
        "failed_cells": counts["failed"],
        "ready_cells": counts["cached"] + counts["generated"],
    }


def _persist_comparison(
    *,
    comparison_id: str,
    name: str,
    created_by_user_id: Optional[int],
    created_by_username: str,
    model: str,
    seed_paper_id: Optional[str],
    paper_ids: Sequence[str],
    paper_paths: Sequence[str],
    questions: Sequence[Dict[str, str]],
    cells: Sequence[Dict[str, Any]],
    project_id: Optional[str],
    persona_id: Optional[str],
) -> None:
    db_url = _db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required.")
    summary = _build_summary(cells)
    status = "completed" if int(summary.get("missing_cells") or 0) == 0 and int(summary.get("failed_cells") or 0) == 0 else "ready"
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO retrieval.paper_comparison_runs
            (
                comparison_id, name, created_by_user_id, created_by_username, model,
                project_id, persona_id,
                compute_mode, visibility, status, paper_ids_json, paper_paths_json,
                questions_json, summary_json, seed_paper_id, created_at, updated_at
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s, %s,
                'cache_only', 'shared', %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s, NOW(), NOW()
            )
            """,
            (
                comparison_id,
                str(name or "").strip(),
                created_by_user_id,
                str(created_by_username or "").strip(),
                str(model or "").strip(),
                str(project_id or "").strip() or None,
                str(persona_id or "").strip() or None,
                status,
                json.dumps(list(paper_ids), ensure_ascii=False),
                json.dumps(list(paper_paths), ensure_ascii=False),
                json.dumps(list(questions), ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                str(seed_paper_id or "").strip() or None,
            ),
        )
        for row in cells:
            cur.execute(
                """
                INSERT INTO retrieval.paper_comparison_cells
                (
                    comparison_id, paper_id, paper_path, question_id, question_text,
                    project_id,
                    question_normalized, model, cell_status, answer, answer_source,
                    cache_hit_layer, cache_key, context_hash, structured_fields_json,
                    error_text, created_at, updated_at
                )
                VALUES
                (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb,
                    %s, NOW(), NOW()
                )
                """,
                (
                    comparison_id,
                    row.get("paper_id"),
                    row.get("paper_path"),
                    row.get("question_id"),
                    row.get("question_text"),
                    str(project_id or "").strip() or None,
                    row.get("question_normalized"),
                    row.get("model"),
                    row.get("cell_status"),
                    row.get("answer"),
                    row.get("answer_source"),
                    row.get("cache_hit_layer"),
                    row.get("cache_key"),
                    row.get("context_hash"),
                    json.dumps(row.get("structured_fields") or {}, ensure_ascii=False),
                    row.get("error_text"),
                ),
            )
        conn.commit()


def _validate_paper_ids(
    *,
    seed_paper_id: Optional[str],
    paper_ids: Sequence[str],
    project_id: Optional[str],
) -> Tuple[List[papers_service.PaperRef], Optional[str]]:
    refs_by_id = _paper_ref_map_scoped(project_id)
    ordered_ids: List[str] = []
    seen = set()
    seed = str(seed_paper_id or "").strip()
    if seed:
        ordered_ids.append(seed)
        seen.add(seed)
    for item in paper_ids:
        pid = str(item or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        ordered_ids.append(pid)
    unknown = [pid for pid in ordered_ids if pid not in refs_by_id]
    if unknown:
        raise ValueError(f"Unknown paper ids: {', '.join(unknown)}")
    if len(ordered_ids) < 2:
        raise ValueError("At least 2 papers are required.")
    if len(ordered_ids) > MAX_COMPARE_PAPERS:
        raise ValueError(f"At most {MAX_COMPARE_PAPERS} papers are allowed.")
    return [refs_by_id[pid] for pid in ordered_ids], seed or None


def create_comparison_run(
    *,
    seed_paper_id: Optional[str],
    paper_ids: Sequence[str],
    questions: Sequence[str],
    model: Optional[str],
    name: Optional[str],
    created_by_user_id: Optional[int],
    created_by_username: str,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
) -> Dict[str, Any]:
    """Create one saved comparison run with cache-first cell population."""
    user_name = str(created_by_username or "").strip()
    if not user_name:
        raise ValueError("created_by_username is required.")
    refs, seed = _validate_paper_ids(
        seed_paper_id=seed_paper_id,
        paper_ids=paper_ids,
        project_id=project_id,
    )
    question_rows = _normalize_questions(questions)
    if not question_rows:
        raise ValueError("At least 1 non-empty question is required.")
    if len(question_rows) > MAX_COMPARE_QUESTIONS:
        raise ValueError(f"At most {MAX_COMPARE_QUESTIONS} questions are allowed.")

    settings = load_settings()
    selected_model = str(model or settings.chat_model).strip()
    if not selected_model:
        raise ValueError("model is required.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    display_name = str(name or "").strip() or f"Comparison {now}"

    paper_paths = [papers_service.normalize_paper_path(ref.path) for ref in refs]
    qnorms = [str(item.get("normalized") or "") for item in question_rows]
    cache_map = _latest_cache_rows(
        paper_paths=paper_paths,
        model=selected_model,
        normalized_questions=qnorms,
        project_id=project_id,
    )

    canonical_structured = _canonical_structured_map()
    structured_by_path: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for ref in refs:
        normalized_path = papers_service.normalize_paper_path(ref.path)
        structured_by_path[normalized_path] = {}
        if not canonical_structured:
            continue
        records = structured_service.db_workflow_question_records_for_paper(ref.path, model=selected_model or None)
        for normalized_question, canonical in canonical_structured.items():
            lookup_key = f"q::{structured_service.normalize_question_key(str(canonical.get('question') or ''))}"
            record = records.get(lookup_key) or {}
            if isinstance(record, dict) and record:
                structured_by_path[normalized_path][normalized_question] = _structured_fields_from_record(record)

    cells: List[Dict[str, Any]] = []
    for question in question_rows:
        qid = str(question.get("id") or "")
        text = str(question.get("text") or "")
        qnorm = str(question.get("normalized") or "")
        for ref in refs:
            normalized_path = papers_service.normalize_paper_path(ref.path)
            cache_row = cache_map.get((normalized_path, qnorm)) or {}
            answer = str(cache_row.get("answer") or "").strip()
            status = "cached" if answer else "missing"
            cells.append(
                {
                    "paper_id": ref.paper_id,
                    "paper_path": normalized_path,
                    "question_id": qid,
                    "question_text": text,
                    "question_normalized": qnorm,
                    "model": selected_model,
                    "cell_status": status,
                    "answer": answer,
                    "answer_source": "query_cache" if answer else None,
                    "cache_hit_layer": "normalized" if answer else "none",
                    "cache_key": str(cache_row.get("cache_key") or ""),
                    "context_hash": str(cache_row.get("context_hash") or ""),
                    "structured_fields": structured_by_path.get(normalized_path, {}).get(qnorm, {}),
                    "error_text": None,
                }
            )

    comparison_id = uuid4().hex
    _persist_comparison(
        comparison_id=comparison_id,
        name=display_name,
        created_by_user_id=created_by_user_id,
        created_by_username=user_name,
        model=selected_model,
        seed_paper_id=seed,
        paper_ids=[ref.paper_id for ref in refs],
        paper_paths=paper_paths,
        questions=question_rows,
        cells=cells,
        project_id=project_id,
        persona_id=persona_id,
    )
    out = get_comparison_run(comparison_id, project_id=project_id)
    if not out:
        raise RuntimeError("Comparison run creation failed.")
    return out


def list_comparison_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List saved comparison runs with pagination."""
    db_url = _db_url()
    if not db_url:
        return {"rows": [], "count": 0}
    bounded_limit = max(1, min(200, int(limit or 50)))
    bounded_offset = max(0, int(offset or 0))
    scoped_project = str(project_id or "").strip()
    rows: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    comparison_id, name, created_by_user_id, created_by_username, model,
                    COALESCE(project_id, ''), COALESCE(persona_id, ''),
                    compute_mode, visibility, status, paper_ids_json, questions_json,
                    summary_json, seed_paper_id, created_at, updated_at
                FROM retrieval.paper_comparison_runs
                WHERE (
                        %s = ''
                     OR COALESCE(project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                )
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (scoped_project, scoped_project, scoped_project, bounded_limit, bounded_offset),
            )
            for row in cur.fetchall():
                rows.append(
                    {
                        "comparison_id": str(row[0] or ""),
                        "name": str(row[1] or ""),
                        "created_by_user_id": row[2],
                        "created_by_username": str(row[3] or ""),
                        "model": str(row[4] or ""),
                        "project_id": str(row[5] or ""),
                        "persona_id": str(row[6] or ""),
                        "compute_mode": str(row[7] or ""),
                        "visibility": str(row[8] or ""),
                        "status": str(row[9] or ""),
                        "paper_ids": _json_list(row[10]),
                        "questions": _json_list(row[11]),
                        "summary": _json_obj(row[12]),
                        "seed_paper_id": str(row[13] or ""),
                        "created_at": _to_iso(row[14]),
                        "updated_at": _to_iso(row[15]),
                    }
                )
    except Exception:
        return {"rows": [], "count": 0}
    return {"rows": rows, "count": len(rows), "limit": bounded_limit, "offset": bounded_offset}


def _comparison_run_row(comparison_id: str, *, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    db_url = _db_url()
    if not db_url:
        return None
    wanted = str(comparison_id or "").strip()
    scoped_project = str(project_id or "").strip()
    if not wanted:
        return None
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    comparison_id, name, created_by_user_id, created_by_username, model,
                    COALESCE(project_id, ''), COALESCE(persona_id, ''),
                    compute_mode, visibility, status, paper_ids_json, paper_paths_json,
                    questions_json, summary_json, seed_paper_id, created_at, updated_at
                FROM retrieval.paper_comparison_runs
                WHERE comparison_id = %s
                  AND (
                        %s = ''
                     OR COALESCE(project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                  )
                LIMIT 1
                """,
                (wanted, scoped_project, scoped_project, scoped_project),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "comparison_id": str(row[0] or ""),
                "name": str(row[1] or ""),
                "created_by_user_id": row[2],
                "created_by_username": str(row[3] or ""),
                "model": str(row[4] or ""),
                "project_id": str(row[5] or ""),
                "persona_id": str(row[6] or ""),
                "compute_mode": str(row[7] or ""),
                "visibility": str(row[8] or ""),
                "status": str(row[9] or ""),
                "paper_ids": [str(item) for item in _json_list(row[10]) if str(item or "").strip()],
                "paper_paths": [str(item) for item in _json_list(row[11]) if str(item or "").strip()],
                "questions": [item for item in _json_list(row[12]) if isinstance(item, dict)],
                "summary": _json_obj(row[13]),
                "seed_paper_id": str(row[14] or ""),
                "created_at": _to_iso(row[15]),
                "updated_at": _to_iso(row[16]),
            }
    except Exception:
        return None


def _comparison_cells(comparison_id: str, *, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    db_url = _db_url()
    if not db_url:
        return []
    scoped_project = str(project_id or "").strip()
    out: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    paper_id, paper_path, question_id, question_text, question_normalized,
                    model, cell_status, answer, answer_source, cache_hit_layer, cache_key,
                    context_hash, structured_fields_json, error_text, created_at, updated_at
                FROM retrieval.paper_comparison_cells
                WHERE comparison_id = %s
                  AND (
                        %s = ''
                     OR COALESCE(project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                  )
                ORDER BY question_id ASC, paper_id ASC
                """,
                (comparison_id, scoped_project, scoped_project, scoped_project),
            )
            for row in cur.fetchall():
                out.append(
                    {
                        "paper_id": str(row[0] or ""),
                        "paper_path": str(row[1] or ""),
                        "question_id": str(row[2] or ""),
                        "question_text": str(row[3] or ""),
                        "question_normalized": str(row[4] or ""),
                        "model": str(row[5] or ""),
                        "cell_status": str(row[6] or ""),
                        "answer": str(row[7] or ""),
                        "answer_source": str(row[8] or ""),
                        "cache_hit_layer": str(row[9] or ""),
                        "cache_key": str(row[10] or ""),
                        "context_hash": str(row[11] or ""),
                        "structured_fields": _json_obj(row[12]),
                        "error_text": str(row[13] or ""),
                        "created_at": _to_iso(row[14]),
                        "updated_at": _to_iso(row[15]),
                    }
                )
    except Exception:
        return []
    return out


def _overviews_for_ids(paper_ids: Sequence[str]) -> List[Dict[str, Any]]:
    refs = _paper_ref_map()
    rows: List[Dict[str, Any]] = []
    for pid in paper_ids:
        ref = refs.get(str(pid or "").strip())
        if not ref:
            rows.append(
                {
                    "paper_id": str(pid or ""),
                    "name": "",
                    "path": "",
                    "display_title": str(pid or ""),
                    "display_authors": "",
                    "display_abstract": "",
                    "openalex_url": "",
                }
            )
            continue
        rows.append(papers_service.paper_overview(ref))
    return rows


def get_comparison_run(comparison_id: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return one persisted comparison run with matrix rows."""
    run = _comparison_run_row(comparison_id, project_id=project_id)
    if not run:
        return None
    cells = _comparison_cells(comparison_id, project_id=project_id)
    papers = _overviews_for_ids(run.get("paper_ids") or [])
    question_rows = [item for item in (run.get("questions") or []) if isinstance(item, dict)]
    paper_order = [str(item.get("paper_id") or "") for item in papers]
    cell_by_key = {(str(c.get("question_id") or ""), str(c.get("paper_id") or "")): c for c in cells}
    matrix: List[Dict[str, Any]] = []
    for question in question_rows:
        qid = str(question.get("id") or "")
        row_cells = []
        for pid in paper_order:
            cell = cell_by_key.get((qid, pid)) or {
                "paper_id": pid,
                "question_id": qid,
                "cell_status": "missing",
                "answer": "",
                "structured_fields": {},
            }
            row_cells.append(cell)
        matrix.append(
            {
                "question_id": qid,
                "question_text": str(question.get("text") or ""),
                "question_normalized": str(question.get("normalized") or ""),
                "cells": row_cells,
            }
        )
    run["papers"] = papers
    run["cells"] = cells
    run["matrix"] = matrix
    run["summary"] = _build_summary(cells)
    return run


def _update_cell(
    *,
    comparison_id: str,
    paper_id: str,
    question_id: str,
    status: str,
    answer: str,
    answer_source: Optional[str],
    cache_hit_layer: Optional[str],
    structured_fields: Optional[Dict[str, Any]],
    error_text: Optional[str],
) -> None:
    db_url = _db_url()
    if not db_url:
        return
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE retrieval.paper_comparison_cells
            SET
                cell_status = %s,
                answer = %s,
                answer_source = %s,
                cache_hit_layer = %s,
                structured_fields_json = %s::jsonb,
                error_text = %s,
                updated_at = NOW()
            WHERE comparison_id = %s
              AND paper_id = %s
              AND question_id = %s
            """,
            (
                status,
                answer,
                answer_source,
                cache_hit_layer,
                json.dumps(structured_fields or {}, ensure_ascii=False),
                error_text,
                comparison_id,
                paper_id,
                question_id,
            ),
        )
        conn.commit()


def _refresh_run_summary(comparison_id: str) -> Dict[str, Any]:
    db_url = _db_url()
    if not db_url:
        return {}
    counts = {"cached": 0, "missing": 0, "generated": 0, "failed": 0}
    total = 0
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT cell_status, COUNT(*)
            FROM retrieval.paper_comparison_cells
            WHERE comparison_id = %s
            GROUP BY cell_status
            """,
            (comparison_id,),
        )
        for status, count in cur.fetchall():
            key = str(status or "")
            if key in counts:
                counts[key] = int(count or 0)
                total += int(count or 0)
        summary = {
            "total_cells": total,
            "cached_cells": counts["cached"],
            "missing_cells": counts["missing"],
            "generated_cells": counts["generated"],
            "failed_cells": counts["failed"],
            "ready_cells": counts["cached"] + counts["generated"],
        }
        run_status = "failed" if counts["failed"] > 0 else ("ready" if counts["missing"] > 0 else "completed")
        cur.execute(
            """
            UPDATE retrieval.paper_comparison_runs
            SET summary_json = %s::jsonb, status = %s, updated_at = NOW()
            WHERE comparison_id = %s
            """,
            (json.dumps(summary, ensure_ascii=False), run_status, comparison_id),
        )
        conn.commit()
    return summary


def _structured_fields_for_paper_question(
    *,
    paper_path: str,
    model: str,
    question_normalized: str,
) -> Dict[str, Any]:
    canonical = _canonical_structured_map()
    canonical_item = canonical.get(question_normalized) or {}
    if not canonical_item:
        return {}
    records = structured_service.db_workflow_question_records_for_paper(paper_path, model=model or None)
    lookup_key = f"q::{structured_service.normalize_question_key(str(canonical_item.get('question') or ''))}"
    record = records.get(lookup_key) or {}
    if not isinstance(record, dict):
        return {}
    return _structured_fields_from_record(record)


def fill_missing_cells(
    *,
    comparison_id: str,
    paper_ids: Optional[Sequence[str]] = None,
    question_ids: Optional[Sequence[str]] = None,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
) -> Dict[str, Any]:
    """Fill missing/failed matrix cells by generating fresh answers."""
    run = _comparison_run_row(comparison_id, project_id=project_id)
    if not run:
        raise ValueError("comparison_not_found")
    selected_papers = {str(item or "").strip() for item in (paper_ids or []) if str(item or "").strip()}
    selected_questions = {str(item or "").strip() for item in (question_ids or []) if str(item or "").strip()}
    refs = _paper_ref_map_scoped(project_id)
    db_url = _db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required.")

    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE retrieval.paper_comparison_runs
            SET status = 'filling', updated_at = NOW()
            WHERE comparison_id = %s
            """,
            (comparison_id,),
        )
        conn.commit()

    candidates = [
        cell
        for cell in _comparison_cells(comparison_id, project_id=project_id)
        if str(cell.get("cell_status") or "") in {"missing", "failed"}
        and (not selected_papers or str(cell.get("paper_id") or "") in selected_papers)
        and (not selected_questions or str(cell.get("question_id") or "") in selected_questions)
    ]
    model = str(run.get("model") or "")
    for cell in candidates:
        paper_id = str(cell.get("paper_id") or "")
        question_id = str(cell.get("question_id") or "")
        question_text = str(cell.get("question_text") or "")
        ref = refs.get(paper_id)
        if not ref:
            _update_cell(
                comparison_id=comparison_id,
                paper_id=paper_id,
                question_id=question_id,
                status="failed",
                answer="",
                answer_source=None,
                cache_hit_layer="none",
                structured_fields=cell.get("structured_fields") if isinstance(cell.get("structured_fields"), dict) else {},
                error_text="paper_not_found",
            )
            continue
        try:
            out = chat_service.chat_turn(
                paper_ref=ref,
                query=question_text,
                model=model or None,
                top_k=None,
                session_id=None,
                request_id=uuid4().hex,
                history=[],
                variation_mode=False,
                project_id=project_id,
                persona_id=persona_id,
                allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
                allow_custom_question_sharing=allow_custom_question_sharing,
                user_id=None,
            )
            answer = str(out.get("answer") or "").strip()
            if not answer:
                raise RuntimeError("Empty answer.")
            _update_cell(
                comparison_id=comparison_id,
                paper_id=paper_id,
                question_id=question_id,
                status="generated",
                answer=answer,
                answer_source="generated",
                cache_hit_layer=str(out.get("cache_hit_layer") or "none"),
                structured_fields=_structured_fields_for_paper_question(
                    paper_path=ref.path,
                    model=model,
                    question_normalized=str(cell.get("question_normalized") or ""),
                ),
                error_text=None,
            )
        except Exception as exc:
            _update_cell(
                comparison_id=comparison_id,
                paper_id=paper_id,
                question_id=question_id,
                status="failed",
                answer="",
                answer_source=None,
                cache_hit_layer="none",
                structured_fields=cell.get("structured_fields") if isinstance(cell.get("structured_fields"), dict) else {},
                error_text=str(exc),
            )
    _refresh_run_summary(comparison_id)
    out = get_comparison_run(comparison_id, project_id=project_id)
    if not out:
        raise ValueError("comparison_not_found")
    return out


def export_comparison(comparison_id: str, *, export_format: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Export one comparison as JSON payload or CSV text."""
    run = get_comparison_run(comparison_id, project_id=project_id)
    if not run:
        raise ValueError("comparison_not_found")
    fmt = str(export_format or "").strip().lower()
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(run.get("name") or "comparison")).strip("-") or "comparison"
    if fmt == "json":
        return {
            "format": "json",
            "filename": f"{safe_name}-{comparison_id}.json",
            "payload": run,
        }
    if fmt != "csv":
        raise ValueError("Unsupported export format.")
    paper_by_id = {str(item.get("paper_id") or ""): item for item in run.get("papers") or []}
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(
        [
            "comparison_id",
            "question_id",
            "question_text",
            "paper_id",
            "paper_title",
            "cell_status",
            "answer_source",
            "cache_hit_layer",
            "answer",
            "confidence_score",
            "retrieval_method",
            "error_text",
        ]
    )
    for cell in run.get("cells") or []:
        paper_id = str(cell.get("paper_id") or "")
        fields = cell.get("structured_fields") if isinstance(cell.get("structured_fields"), dict) else {}
        writer.writerow(
            [
                comparison_id,
                str(cell.get("question_id") or ""),
                str(cell.get("question_text") or ""),
                paper_id,
                str((paper_by_id.get(paper_id) or {}).get("display_title") or ""),
                str(cell.get("cell_status") or ""),
                str(cell.get("answer_source") or ""),
                str(cell.get("cache_hit_layer") or ""),
                str(cell.get("answer") or ""),
                str(fields.get("confidence_score") or ""),
                str(fields.get("retrieval_method") or ""),
                str(cell.get("error_text") or ""),
            ]
        )
    return {
        "format": "csv",
        "filename": f"{safe_name}-{comparison_id}.csv",
        "content": sio.getvalue(),
    }
