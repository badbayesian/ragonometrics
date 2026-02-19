"""Workflow step-cache read services for the Flask web UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import pooled_connection

_TOP_LEVEL_STEP_ORDER = [
    "prep",
    "ingest",
    "enrich",
    "econ_data",
    "agentic",
    "index",
    "evaluate",
    "report",
]
_STEP_ORDER_INDEX = {name: idx for idx, name in enumerate(_TOP_LEVEL_STEP_ORDER)}


def _db_url() -> str:
    """Internal helper for db url."""
    return (os.environ.get("DATABASE_URL") or "").strip()


def _project_scope_params(project_id: str) -> tuple[str, str, str]:
    """Return repeated project-scope SQL parameters."""
    scoped = str(project_id or "").strip()
    return (scoped, scoped, scoped)


def _to_iso(value: Any) -> str:
    """Internal helper for to iso."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except Exception:
            return str(value)
    return str(value)


def _to_int(value: Any) -> int:
    """Internal helper for to int."""
    try:
        return int(value or 0)
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    """Internal helper for to float."""
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _json_obj(value: Any) -> Dict[str, Any]:
    """Internal helper for json obj."""
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
    """Internal helper for json list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_path_for_match(path: str) -> str:
    """Normalize paper path text for deterministic case-insensitive comparisons."""
    return str(path or "").replace("\\", "/").strip().lower()


def paper_match_predicates(paper_path: str) -> Dict[str, str]:
    """Build deterministic path predicates used for paper-scoped run matching."""
    path_text = str(paper_path or "").strip()
    paper_name = Path(path_text).name.lower()
    paper_dir = str(Path(path_text).parent)
    return {
        "paper_path": normalize_path_for_match(path_text),
        "paper_dir": normalize_path_for_match(paper_dir),
        "paper_filename": paper_name,
        "basename_like": f"%/{paper_name}" if paper_name else "",
    }


def _match_kind_for_row(*, papers_dir: str, paper_path: str) -> Optional[str]:
    """Internal helper for match kind for row."""
    predicates = paper_match_predicates(paper_path)
    run_path = normalize_path_for_match(papers_dir)
    if not run_path:
        return None
    if run_path == predicates["paper_path"]:
        return "paper_path"
    if predicates["paper_dir"] and run_path == predicates["paper_dir"]:
        return "paper_dir"
    if predicates["paper_filename"] and run_path.endswith(f"/{predicates['paper_filename']}"):
        return "basename"
    return None


def run_belongs_to_paper(run_row: Dict[str, Any], paper_path: str) -> bool:
    """Return whether one run row is scoped to the selected paper."""
    papers_dir = str((run_row or {}).get("papers_dir") or "")
    return _match_kind_for_row(papers_dir=papers_dir, paper_path=paper_path) is not None


def _run_row_payload(row: Any, *, paper_path: str) -> Dict[str, Any]:
    """Internal helper for run row payload."""
    (
        run_id,
        status,
        papers_dir,
        workstream_id,
        arm,
        trigger_source,
        question,
        report_question_set,
        report_path,
        started_at,
        finished_at,
        created_at,
        updated_at,
    ) = row
    return {
        "run_id": str(run_id or ""),
        "status": str(status or ""),
        "papers_dir": str(papers_dir or ""),
        "workstream_id": str(workstream_id or ""),
        "arm": str(arm or ""),
        "trigger_source": str(trigger_source or ""),
        "question": str(question or ""),
        "report_question_set": str(report_question_set or ""),
        "report_path": str(report_path or ""),
        "started_at": _to_iso(started_at),
        "finished_at": _to_iso(finished_at),
        "created_at": _to_iso(created_at),
        "updated_at": _to_iso(updated_at),
        "matched_by": _match_kind_for_row(papers_dir=str(papers_dir or ""), paper_path=paper_path) or "",
    }


def _usage_row_payload(row: Any) -> Dict[str, Any]:
    """Normalize one token-usage row into API payload shape."""
    return {
        "step": str(row[0] or ""),
        "model": str(row[1] or ""),
        "question_id": str(row[2] or ""),
        "call_count": _to_int(row[3]),
        "input_tokens": _to_int(row[4]),
        "output_tokens": _to_int(row[5]),
        "total_tokens": _to_int(row[6]),
        "cost_usd_total": _to_float(row[7]),
    }


def _usage_rows_payload(rows: List[Any]) -> List[Dict[str, Any]]:
    """Normalize token-usage result rows into response payloads."""
    return [_usage_row_payload(row) for row in rows]


def _relation_has_column(cur: Any, *, schema: str, table: str, column: str) -> bool:
    """Return whether one relation has a given column."""
    try:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
            LIMIT 1
            """,
            (str(schema or ""), str(table or ""), str(column or "")),
        )
        return bool(cur.fetchone())
    except Exception:
        return False


def list_runs_for_paper(paper_path: str, limit: int = 50, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return paper-scoped workflow runs ordered by recency."""
    db_url = _db_url()
    if not db_url:
        return []
    predicates = paper_match_predicates(paper_path)
    bounded_limit = max(1, min(200, int(limit or 50)))
    scoped_project = str(project_id or "").strip()
    scope_params = _project_scope_params(scoped_project)
    out: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    r.run_id,
                    r.status,
                    r.papers_dir,
                    r.workstream_id,
                    r.arm,
                    r.trigger_source,
                    r.question,
                    r.report_question_set,
                    r.report_path,
                    r.started_at,
                    r.finished_at,
                    r.created_at,
                    r.updated_at
                FROM workflow.run_records r
                WHERE r.record_kind = 'run'
                  AND r.step = ''
                  AND r.record_key = 'main'
                  AND (
                        %s = ''
                     OR COALESCE(r.project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(r.project_id, '') = '')
                  )
                  AND (
                        lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) = %s
                     OR lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) = %s
                     OR lower(replace(COALESCE(r.papers_dir, ''), '\\', '/')) LIKE %s
                  )
                ORDER BY COALESCE(r.finished_at, r.updated_at, r.created_at) DESC
                LIMIT %s
                """,
                (
                    *scope_params,
                    predicates["paper_path"],
                    predicates["paper_dir"],
                    predicates["basename_like"],
                    bounded_limit,
                ),
            )
            for row in cur.fetchall():
                payload = _run_row_payload(row, paper_path=paper_path)
                if payload.get("matched_by"):
                    out.append(payload)
    except Exception:
        return []
    return out


def get_run_record(run_id: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load one workflow run record by id."""
    db_url = _db_url()
    wanted = str(run_id or "").strip()
    scoped_project = str(project_id or "").strip()
    scope_params = _project_scope_params(scoped_project)
    if not db_url or not wanted:
        return None
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    r.run_id,
                    r.status,
                    r.papers_dir,
                    r.workstream_id,
                    r.arm,
                    r.trigger_source,
                    r.question,
                    r.report_question_set,
                    r.report_path,
                    r.started_at,
                    r.finished_at,
                    r.created_at,
                    r.updated_at
                FROM workflow.run_records r
                WHERE r.record_kind = 'run'
                  AND r.step = ''
                  AND r.record_key = 'main'
                  AND (
                        %s = ''
                     OR COALESCE(r.project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(r.project_id, '') = '')
                  )
                  AND r.run_id = %s
                LIMIT 1
                """,
                (*scope_params, wanted),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _run_row_payload(row, paper_path=str(row[2] or ""))
    except Exception:
        return None


def list_steps_for_run(run_id: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return top-level workflow step rows for one run."""
    db_url = _db_url()
    wanted = str(run_id or "").strip()
    scoped_project = str(project_id or "").strip()
    scope_params = _project_scope_params(scoped_project)
    if not db_url or not wanted:
        return []
    out: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    step,
                    status,
                    started_at,
                    finished_at,
                    created_at,
                    updated_at,
                    output_json,
                    metadata_json,
                    idempotency_key,
                    input_hash,
                    reuse_source_run_id,
                    reuse_source_record_key
                FROM workflow.run_records
                WHERE run_id = %s
                  AND (
                        %s = ''
                     OR COALESCE(project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                  )
                  AND record_kind = 'step'
                ORDER BY COALESCE(started_at, created_at) ASC, step ASC
                """,
                (wanted, *scope_params),
            )
            for row in cur.fetchall():
                step_name = str(row[0] or "")
                payload = {
                    "step": step_name,
                    "status": str(row[1] or ""),
                    "started_at": _to_iso(row[2]),
                    "finished_at": _to_iso(row[3]),
                    "created_at": _to_iso(row[4]),
                    "updated_at": _to_iso(row[5]),
                    "output": _json_obj(row[6]),
                    "metadata": _json_obj(row[7]),
                    "idempotency_key": str(row[8] or ""),
                    "input_hash": str(row[9] or ""),
                    "reuse_source_run_id": str(row[10] or ""),
                    "reuse_source_record_key": str(row[11] or ""),
                }
                out.append(payload)
    except Exception:
        return []

    def _sort_key(item: Dict[str, Any]) -> Any:
        """Internal helper for sort key."""
        return (
            _STEP_ORDER_INDEX.get(str(item.get("step") or ""), 999),
            str(item.get("started_at") or item.get("created_at") or ""),
            str(item.get("step") or ""),
        )

    return sorted(out, key=_sort_key)


def list_question_rows_for_run(run_id: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return cached question rows for one run."""
    db_url = _db_url()
    wanted = str(run_id or "").strip()
    scoped_project = str(project_id or "").strip()
    scope_params = _project_scope_params(scoped_project)
    if not db_url or not wanted:
        return []
    out: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    run_id,
                    question_id,
                    status,
                    step,
                    record_key,
                    created_at,
                    updated_at,
                    payload_json,
                    output_json,
                    metadata_json
                FROM workflow.run_records
                WHERE run_id = %s
                  AND (
                        %s = ''
                     OR COALESCE(project_id, '') = %s
                     OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                  )
                  AND record_kind = 'question'
                ORDER BY COALESCE(created_at, updated_at) DESC, question_id ASC
                """,
                (wanted, *scope_params),
            )
            for row in cur.fetchall():
                out.append(
                    {
                        "run_id": str(row[0] or ""),
                        "question_id": str(row[1] or ""),
                        "status": str(row[2] or ""),
                        "step": str(row[3] or ""),
                        "record_key": str(row[4] or ""),
                        "created_at": _to_iso(row[5]),
                        "updated_at": _to_iso(row[6]),
                        "payload_json": _json_obj(row[7]),
                        "output_json": _json_obj(row[8]),
                        "metadata_json": _json_obj(row[9]),
                    }
                )
    except Exception:
        return []
    return out


def usage_rollup_for_run(run_id: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return usage rollup rows keyed to one run."""
    db_url = _db_url()
    wanted = str(run_id or "").strip()
    scoped_project = str(project_id or "").strip()
    scope_params = _project_scope_params(scoped_project)
    if not db_url or not wanted:
        return []
    out: List[Dict[str, Any]] = []
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            rollup_has_project_id = _relation_has_column(
                cur,
                schema="observability",
                table="token_usage_rollup",
                column="project_id",
            )
            usage_has_project_id = _relation_has_column(
                cur,
                schema="observability",
                table="token_usage",
                column="project_id",
            )
            try:
                if rollup_has_project_id:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(step, '') AS step,
                            COALESCE(model, '') AS model,
                            COALESCE(question_id, '') AS question_id,
                            call_count,
                            input_tokens,
                            output_tokens,
                            total_tokens,
                            cost_usd_total
                        FROM observability.token_usage_rollup
                        WHERE run_id = %s
                          AND (
                                %s = ''
                             OR COALESCE(project_id, '') = %s
                             OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                          )
                        ORDER BY total_tokens DESC, call_count DESC, step ASC, model ASC
                        """,
                        (wanted, *scope_params),
                    )
                elif not scoped_project:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(step, '') AS step,
                            COALESCE(model, '') AS model,
                            COALESCE(question_id, '') AS question_id,
                            call_count,
                            input_tokens,
                            output_tokens,
                            total_tokens,
                            cost_usd_total
                        FROM observability.token_usage_rollup
                        WHERE run_id = %s
                        ORDER BY total_tokens DESC, call_count DESC, step ASC, model ASC
                        """,
                        (wanted,),
                    )
                else:
                    # Rollup table does not support project scoping on this schema version.
                    raise RuntimeError("token_usage_rollup lacks project_id; falling back to token_usage")
                rows = cur.fetchall()
                out = _usage_rows_payload(rows)
                return out
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                if usage_has_project_id:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(step, '') AS step,
                            COALESCE(model, '') AS model,
                            COALESCE(question_id, '') AS question_id,
                            COUNT(*) AS call_count,
                            COALESCE(SUM(input_tokens), 0) AS input_tokens,
                            COALESCE(SUM(output_tokens), 0) AS output_tokens,
                            COALESCE(SUM(total_tokens), 0) AS total_tokens,
                            COALESCE(SUM(cost_usd_total), 0) AS cost_usd_total
                        FROM observability.token_usage
                        WHERE run_id = %s
                          AND (
                                %s = ''
                             OR COALESCE(project_id, '') = %s
                             OR (%s = 'default-shared' AND COALESCE(project_id, '') = '')
                          )
                        GROUP BY COALESCE(step, ''), COALESCE(model, ''), COALESCE(question_id, '')
                        ORDER BY total_tokens DESC, call_count DESC, step ASC, model ASC
                        """,
                        (wanted, *scope_params),
                    )
                elif not scoped_project:
                    cur.execute(
                        """
                        SELECT
                            COALESCE(step, '') AS step,
                            COALESCE(model, '') AS model,
                            COALESCE(question_id, '') AS question_id,
                            COUNT(*) AS call_count,
                            COALESCE(SUM(input_tokens), 0) AS input_tokens,
                            COALESCE(SUM(output_tokens), 0) AS output_tokens,
                            COALESCE(SUM(total_tokens), 0) AS total_tokens,
                            COALESCE(SUM(cost_usd_total), 0) AS cost_usd_total
                        FROM observability.token_usage
                        WHERE run_id = %s
                        GROUP BY COALESCE(step, ''), COALESCE(model, ''), COALESCE(question_id, '')
                        ORDER BY total_tokens DESC, call_count DESC, step ASC, model ASC
                        """,
                        (wanted,),
                    )
                else:
                    return []
                rows = cur.fetchall()
                out = _usage_rows_payload(rows)
                return out
    except Exception:
        return []


def summarize_usage_by_step(usage_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Group usage rollup rows by step with aggregate totals."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in usage_rows:
        step = str(row.get("step") or "")
        bucket = buckets.setdefault(
            step,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd_total": 0.0,
                "models": [],
            },
        )
        bucket["calls"] += _to_int(row.get("call_count"))
        bucket["input_tokens"] += _to_int(row.get("input_tokens"))
        bucket["output_tokens"] += _to_int(row.get("output_tokens"))
        bucket["total_tokens"] += _to_int(row.get("total_tokens"))
        bucket["cost_usd_total"] += _to_float(row.get("cost_usd_total"))
        model = str(row.get("model") or "").strip()
        if model and model not in bucket["models"]:
            bucket["models"].append(model)
    for payload in buckets.values():
        payload["models"] = sorted(payload.get("models") or [])
    return buckets


def _aggregate_usage_for_steps(usage_rows: List[Dict[str, Any]], step_names: List[str]) -> Dict[str, Any]:
    """Internal helper for aggregate usage for steps."""
    names = {str(name or "").strip() for name in step_names if str(name or "").strip()}
    filtered = [row for row in usage_rows if str(row.get("step") or "") in names]
    models = sorted({str(row.get("model") or "").strip() for row in filtered if str(row.get("model") or "").strip()})
    return {
        "calls": sum(_to_int(row.get("call_count")) for row in filtered),
        "input_tokens": sum(_to_int(row.get("input_tokens")) for row in filtered),
        "output_tokens": sum(_to_int(row.get("output_tokens")) for row in filtered),
        "total_tokens": sum(_to_int(row.get("total_tokens")) for row in filtered),
        "cost_usd_total": round(sum(_to_float(row.get("cost_usd_total")) for row in filtered), 8),
        "models": models,
        "row_count": len(filtered),
    }


def derive_agentic_internals(
    agentic_step: Optional[Dict[str, Any]],
    question_rows: List[Dict[str, Any]],
    usage_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Derive agentic-internal stage rows from cached agentic payload + question rows."""
    if not isinstance(agentic_step, dict):
        return []
    output = agentic_step.get("output") if isinstance(agentic_step.get("output"), dict) else {}
    agentic_status = str(agentic_step.get("status") or "").strip().lower()
    subquestions = [item for item in _json_list(output.get("subquestions")) if isinstance(item, str)]
    sub_answers = [item for item in _json_list(output.get("sub_answers")) if isinstance(item, dict)]
    answer_count = sum(1 for item in sub_answers if str(item.get("answer") or "").strip())
    final_answer = str(output.get("final_answer") or "").strip()
    citations_preview = [item for item in _json_list(output.get("citations_preview")) if isinstance(item, dict)]
    citations_error = str(output.get("citations_error") or "").strip()
    citations_enabled = output.get("citations_enabled")
    report_rows = [row for row in question_rows if str(row.get("step") or "") == "agentic"]
    confidence_labels: Dict[str, int] = {}
    for row in report_rows:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        confidence = str(payload.get("confidence") or row.get("status") or "").strip().lower()
        if confidence:
            confidence_labels[confidence] = confidence_labels.get(confidence, 0) + 1

    def _status_from_presence(count: int) -> str:
        """Internal helper for status from presence."""
        if count > 0:
            return "completed"
        if agentic_status in {"failed", "skipped"}:
            return agentic_status
        return "unknown"

    out: List[Dict[str, Any]] = []
    out.append(
        {
            "internal_step": "agentic_plan",
            "label": "Agentic Plan",
            "status": _status_from_presence(len(subquestions)),
            "detail": f"subquestions={len(subquestions)}",
            "summary": {"subquestion_count": len(subquestions)},
            "usage": _aggregate_usage_for_steps(usage_rows, ["agentic_plan"]),
            "sample": subquestions[:5],
        }
    )
    out.append(
        {
            "internal_step": "agentic_subquestion_answer",
            "label": "Agentic Subquestion Answers",
            "status": _status_from_presence(answer_count),
            "detail": f"answered={answer_count}/{len(sub_answers)}",
            "summary": {"sub_answers_count": len(sub_answers), "answered_count": answer_count},
            "usage": _aggregate_usage_for_steps(
                usage_rows,
                ["agentic_subquestion_retrieval", "agentic_subquestion_answer"],
            ),
        }
    )
    out.append(
        {
            "internal_step": "agentic_report_question_answer",
            "label": "Agentic Report Questions",
            "status": _status_from_presence(len(report_rows)),
            "detail": f"cached_questions={len(report_rows)}",
            "summary": {"question_row_count": len(report_rows), "confidence_label_counts": confidence_labels},
            "usage": _aggregate_usage_for_steps(
                usage_rows,
                ["agentic_report_question_retrieval", "agentic_report_question_answer"],
            ),
        }
    )
    out.append(
        {
            "internal_step": "agentic_synthesis",
            "label": "Agentic Synthesis",
            "status": _status_from_presence(1 if final_answer else 0),
            "detail": f"final_answer_chars={len(final_answer)}",
            "summary": {
                "final_answer_chars": len(final_answer),
                "final_answer_words": len([tok for tok in final_answer.split() if tok.strip()]),
            },
            "usage": _aggregate_usage_for_steps(usage_rows, ["agentic_synthesis"]),
        }
    )
    if citations_error:
        citations_status = "failed" if not citations_preview else "completed"
    elif citations_enabled is False:
        citations_status = "skipped"
    elif citations_preview:
        citations_status = "completed"
    elif agentic_status in {"failed", "skipped"}:
        citations_status = agentic_status
    else:
        citations_status = "unknown"
    out.append(
        {
            "internal_step": "agentic_citations",
            "label": "Agentic Citations",
            "status": citations_status,
            "detail": f"preview_items={len(citations_preview)}",
            "summary": {
                "citations_preview_count": len(citations_preview),
                "citations_error": citations_error,
                "citations_enabled": citations_enabled,
            },
            "usage": _aggregate_usage_for_steps(usage_rows, []),
        }
    )
    return out
