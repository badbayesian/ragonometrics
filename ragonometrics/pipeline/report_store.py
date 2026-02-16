"""Postgres-backed storage for workflow reports using a unified run records table."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2

from ragonometrics.pipeline.run_records import ensure_run_records_table


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_workflow_status(payload: Dict[str, Any]) -> str:
    """Infer a top-level workflow status from report payload data."""
    status = str(payload.get("status") or "").strip().lower()
    if status in {"running", "completed", "failed"}:
        return status
    prep = payload.get("prep")
    if isinstance(prep, dict) and str(prep.get("status") or "").strip().lower() == "failed":
        return "failed"
    if payload.get("finished_at"):
        return "completed"
    return "running"


def _agentic_status(payload: Dict[str, Any]) -> str | None:
    agentic = payload.get("agentic")
    if not isinstance(agentic, dict):
        return None
    value = str(agentic.get("status") or "").strip()
    return value or None


def _report_questions_set(payload: Dict[str, Any]) -> str | None:
    agentic = payload.get("agentic")
    if not isinstance(agentic, dict):
        return None
    value = str(agentic.get("report_questions_set") or "").strip()
    return value or None


def _report_questions(payload: Dict[str, Any]) -> list[dict[str, Any]]:
    agentic = payload.get("agentic")
    if not isinstance(agentic, dict):
        return []
    questions = agentic.get("report_questions")
    if not isinstance(questions, list):
        return []
    return [q for q in questions if isinstance(q, dict)]


def _report_question_confidence(payload: Dict[str, Any]) -> dict[str, Any]:
    agentic = payload.get("agentic")
    if not isinstance(agentic, dict):
        return {}
    conf = agentic.get("report_question_confidence")
    if isinstance(conf, dict):
        return conf
    return {}


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def ensure_workflow_report_store(conn) -> None:
    """Create unified workflow report persistence table/indexes if needed."""
    ensure_run_records_table(conn)


def _upsert_run_record(cur, *, run_id: str, status: str, papers_dir: str, report_path: str, payload: Dict[str, Any]) -> None:
    workstream_id = str(payload.get("workstream_id") or "").strip() or None
    arm = str(payload.get("arm") or "").strip() or None
    parent_run_id = str(payload.get("parent_run_id") or "").strip() or None
    trigger_source = str(payload.get("trigger_source") or "").strip() or None
    git_sha = str(payload.get("git_sha") or "").strip() or None
    git_branch = str(payload.get("git_branch") or "").strip() or None
    paper_set_hash = str(payload.get("paper_set_hash") or "").strip() or None
    question = str(((payload.get("agentic") or {}).get("question") or "")).strip() or None
    report_question_set = _report_questions_set(payload)
    config_effective = (payload.get("config") or {}).get("config_effective")
    if not isinstance(config_effective, dict):
        config_effective = {}
    now = _utc_now()
    cur.execute(
        """
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key,
            status, papers_dir, config_hash,
            workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
            config_effective_json, paper_set_hash, question, report_question_set,
            started_at, finished_at, created_at, updated_at,
            report_path, payload_json, metadata_json
        )
        VALUES (
            %s, 'run', '', 'main',
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = COALESCE(EXCLUDED.status, workflow.run_records.status),
            papers_dir = COALESCE(EXCLUDED.papers_dir, workflow.run_records.papers_dir),
            config_hash = COALESCE(EXCLUDED.config_hash, workflow.run_records.config_hash),
            workstream_id = COALESCE(EXCLUDED.workstream_id, workflow.run_records.workstream_id),
            arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
            parent_run_id = COALESCE(EXCLUDED.parent_run_id, workflow.run_records.parent_run_id),
            trigger_source = COALESCE(EXCLUDED.trigger_source, workflow.run_records.trigger_source),
            git_sha = COALESCE(EXCLUDED.git_sha, workflow.run_records.git_sha),
            git_branch = COALESCE(EXCLUDED.git_branch, workflow.run_records.git_branch),
            config_effective_json = CASE
                WHEN workflow.run_records.config_effective_json IS NULL OR workflow.run_records.config_effective_json = '{}'::jsonb
                    THEN EXCLUDED.config_effective_json
                ELSE workflow.run_records.config_effective_json
            END,
            paper_set_hash = COALESCE(EXCLUDED.paper_set_hash, workflow.run_records.paper_set_hash),
            question = COALESCE(EXCLUDED.question, workflow.run_records.question),
            report_question_set = COALESCE(EXCLUDED.report_question_set, workflow.run_records.report_question_set),
            started_at = COALESCE(workflow.run_records.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
            report_path = COALESCE(EXCLUDED.report_path, workflow.run_records.report_path),
            payload_json = CASE
                WHEN workflow.run_records.payload_json IS NULL OR workflow.run_records.payload_json = '{}'::jsonb
                    THEN EXCLUDED.payload_json
                ELSE workflow.run_records.payload_json
            END,
            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
            updated_at = EXCLUDED.updated_at
        """,
        (
            run_id,
            status,
            papers_dir,
            str((payload.get("config") or {}).get("config_hash") or ""),
            workstream_id,
            arm,
            parent_run_id,
            trigger_source,
            git_sha,
            git_branch,
            json.dumps(config_effective, ensure_ascii=False),
            paper_set_hash,
            question,
            report_question_set,
            payload.get("started_at"),
            payload.get("finished_at"),
            now,
            now,
            report_path,
            json.dumps({"source": "report_store"}, ensure_ascii=False),
            json.dumps({"source": "report_store"}, ensure_ascii=False),
        ),
    )
    if workstream_id:
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status,
                workstream_id, arm, parent_run_id, created_at, updated_at,
                payload_json, metadata_json
            )
            VALUES (
                %s, 'workstream_link', '', %s, %s,
                %s, %s, %s, NOW(), NOW(),
                %s::jsonb, %s::jsonb
            )
            ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                status = COALESCE(EXCLUDED.status, workflow.run_records.status),
                workstream_id = COALESCE(EXCLUDED.workstream_id, workflow.run_records.workstream_id),
                arm = COALESCE(EXCLUDED.arm, workflow.run_records.arm),
                parent_run_id = COALESCE(EXCLUDED.parent_run_id, workflow.run_records.parent_run_id),
                payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
                metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            """,
            (
                run_id,
                workstream_id,
                status,
                workstream_id,
                arm,
                parent_run_id,
                json.dumps(
                    {
                        "source_bucket": "archive" if "archived" in str(report_path).lower() else "current",
                        "is_baseline": bool(str(arm or "").strip().lower() in {"baseline", "control", "gpt-5"}),
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"source": "report_store"}, ensure_ascii=False),
            ),
        )


def _upsert_report_questions(cur, *, run_id: str, payload: Dict[str, Any]) -> None:
    questions = _report_questions(payload)
    if not questions:
        return
    for item in questions:
        question_id = str(item.get("id") or "").strip()
        if not question_id:
            continue
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status, question_id,
                report_question_set, created_at, updated_at, payload_json, metadata_json
            )
            VALUES (
                %s, 'question', 'agentic', %s, %s, %s,
                %s, NOW(), NOW(), %s::jsonb, %s::jsonb
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
                question_id,
                item.get("confidence"),
                question_id,
                _report_questions_set(payload),
                json.dumps(item, ensure_ascii=False, default=str),
                json.dumps(
                    {
                        "category": item.get("category"),
                        "retrieval_method": item.get("retrieval_method"),
                        "evidence_type": item.get("evidence_type"),
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def _upsert_artifacts(cur, *, run_id: str, report_path: str, payload: Dict[str, Any]) -> None:
    artifact_candidates: list[tuple[str, str | None, dict[str, Any]]] = [
        ("workflow_report_json", report_path, {"source": "workflow.report_path"}),
    ]
    audit = payload.get("audit_artifacts")
    if isinstance(audit, dict):
        md = audit.get("markdown") if isinstance(audit.get("markdown"), dict) else {}
        pdf = audit.get("pdf") if isinstance(audit.get("pdf"), dict) else {}
        artifact_candidates.append(("audit_markdown", md.get("path"), {"status": md.get("status")}))
        artifact_candidates.append(("audit_pdf", pdf.get("path"), {"status": pdf.get("status")}))
        artifact_candidates.append(("audit_tex", pdf.get("tex_path"), {"status": pdf.get("status")}))

    for artifact_type, path_text, meta in artifact_candidates:
        if not path_text:
            continue
        path_obj = Path(path_text)
        sha256 = _sha256_file(path_obj)
        key = f"{artifact_type}:{path_text}"
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status,
                artifact_type, artifact_path, artifact_sha256,
                created_at, updated_at, payload_json, metadata_json
            )
            VALUES (
                %s, 'artifact', 'report', %s, %s,
                %s, %s, %s,
                NOW(), NOW(), %s::jsonb, %s::jsonb
            )
            ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                status = COALESCE(EXCLUDED.status, workflow.run_records.status),
                artifact_type = COALESCE(EXCLUDED.artifact_type, workflow.run_records.artifact_type),
                artifact_path = COALESCE(EXCLUDED.artifact_path, workflow.run_records.artifact_path),
                artifact_sha256 = COALESCE(EXCLUDED.artifact_sha256, workflow.run_records.artifact_sha256),
                payload_json = workflow.run_records.payload_json || EXCLUDED.payload_json,
                metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            """,
            (
                run_id,
                key,
                meta.get("status"),
                artifact_type,
                str(path_text),
                sha256,
                json.dumps(meta or {}, ensure_ascii=False),
                json.dumps({"source": "report_store"}, ensure_ascii=False),
            ),
        )


def upsert_workflow_report(
    conn,
    *,
    run_id: str,
    report_path: str,
    payload: Dict[str, Any],
    status: str | None = None,
) -> None:
    """Upsert one workflow report row into the unified workflow ledger."""
    cur = conn.cursor()
    now = _utc_now()
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    workflow_status = (status or infer_workflow_status(payload)).strip().lower() or "completed"
    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")
    papers_dir = str(payload.get("papers_dir") or "")
    agentic_status = _agentic_status(payload)
    question_set = _report_questions_set(payload)
    report_questions = _report_questions(payload)
    confidence = _report_question_confidence(payload)
    confidence_labels = confidence.get("label_counts") if isinstance(confidence.get("label_counts"), dict) else {}
    confidence_mean = confidence.get("mean")
    try:
        confidence_mean = float(confidence_mean) if confidence_mean is not None else None
    except Exception:
        confidence_mean = None
    final_answer = ""
    agentic_payload = payload.get("agentic")
    if isinstance(agentic_payload, dict):
        final_answer = str(agentic_payload.get("final_answer") or "")
    report_hash = _sha256_text(payload_json)
    final_answer_hash = _sha256_text(final_answer) if final_answer else None
    report_question_count = len(report_questions)

    _upsert_run_record(
        cur,
        run_id=run_id,
        status=workflow_status,
        papers_dir=papers_dir,
        report_path=report_path,
        payload=payload,
    )
    cur.execute(
        """
        INSERT INTO workflow.run_records
        (
            run_id, record_kind, step, record_key,
            status, started_at, finished_at, papers_dir, report_path,
            agentic_status, report_question_set, report_hash, report_question_count,
            confidence_mean, confidence_label_counts_json, final_answer_hash,
            created_at, updated_at, payload_json, metadata_json
        )
        VALUES (
            %s, 'report', 'report', 'main',
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s::jsonb, %s,
            %s, %s, %s::jsonb, %s::jsonb
        )
        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
            status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            papers_dir = EXCLUDED.papers_dir,
            report_path = EXCLUDED.report_path,
            agentic_status = EXCLUDED.agentic_status,
            report_question_set = EXCLUDED.report_question_set,
            report_hash = EXCLUDED.report_hash,
            report_question_count = EXCLUDED.report_question_count,
            confidence_mean = EXCLUDED.confidence_mean,
            confidence_label_counts_json = EXCLUDED.confidence_label_counts_json,
            final_answer_hash = EXCLUDED.final_answer_hash,
            payload_json = EXCLUDED.payload_json,
            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
            updated_at = EXCLUDED.updated_at
        """,
        (
            run_id,
            workflow_status,
            started_at,
            finished_at,
            papers_dir,
            report_path,
            agentic_status,
            question_set,
            report_hash,
            report_question_count,
            confidence_mean,
            json.dumps(confidence_labels, ensure_ascii=False),
            final_answer_hash,
            now,
            now,
            payload_json,
            json.dumps({"source": "report_store"}, ensure_ascii=False),
        ),
    )
    _upsert_report_questions(cur, run_id=run_id, payload=payload)
    _upsert_artifacts(cur, run_id=run_id, report_path=report_path, payload=payload)
    conn.commit()


def store_workflow_report(
    *,
    db_url: str,
    run_id: str,
    report_path: str,
    payload: Dict[str, Any],
    status: str | None = None,
) -> None:
    """Connect and store a single workflow report row."""
    conn = psycopg2.connect(db_url)
    try:
        ensure_workflow_report_store(conn)
        upsert_workflow_report(conn, run_id=run_id, report_path=report_path, payload=payload, status=status)
    finally:
        conn.close()


def store_workflow_reports_from_dir(
    *,
    reports_dir: Path,
    db_url: str,
    recursive: bool = False,
    limit: int = 0,
) -> Dict[str, int]:
    """Backfill workflow report JSON files from disk into Postgres."""
    if not reports_dir.exists():
        return {"total": 0, "stored": 0, "skipped": 0}

    pattern = "workflow-report-*.json"
    paths = sorted(reports_dir.rglob(pattern) if recursive else reports_dir.glob(pattern))
    if limit and limit > 0:
        paths = paths[:limit]

    conn = psycopg2.connect(db_url)
    stored = 0
    skipped = 0
    try:
        ensure_workflow_report_store(conn)
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            run_id = str(payload.get("run_id") or path.stem.replace("workflow-report-", ""))
            report_path = str(payload.get("report_path") or path)
            try:
                upsert_workflow_report(
                    conn,
                    run_id=run_id,
                    report_path=report_path,
                    payload=payload,
                    status=infer_workflow_status(payload),
                )
                stored += 1
            except Exception:
                skipped += 1
    finally:
        conn.close()

    return {"total": len(paths), "stored": stored, "skipped": skipped}
