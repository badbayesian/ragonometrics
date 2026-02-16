"""Postgres-backed storage for workflow reports using JSONB payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_execute(cur, sql: str, params: tuple | None = None) -> bool:
    """Execute SQL while isolating backend-specific incompatibilities."""
    savepoint_name = "ragonometrics_report_store_safe_execute"
    has_savepoint = False
    try:
        cur.execute(f"SAVEPOINT {savepoint_name}")
        has_savepoint = True
    except Exception:
        has_savepoint = False
    try:
        cur.execute(sql, params or ())
        if has_savepoint:
            try:
                cur.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return True
    except Exception:
        if has_savepoint:
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            except Exception:
                pass
            try:
                cur.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return False


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
    """Create workflow report table/indexes if they do not exist."""
    cur = conn.cursor()
    _safe_execute(cur, "CREATE SCHEMA IF NOT EXISTS workflow")
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.workflow_runs (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            status TEXT,
            papers_dir TEXT,
            config_hash TEXT,
            workstream_id TEXT,
            arm TEXT,
            parent_run_id TEXT,
            trigger_source TEXT,
            git_sha TEXT,
            git_branch TEXT,
            config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            paper_set_hash TEXT,
            question TEXT,
            report_question_set TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
    )
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS workstream_id TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS arm TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS parent_run_id TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS trigger_source TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS git_sha TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS git_branch TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS config_effective_json JSONB NOT NULL DEFAULT '{}'::jsonb")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS paper_set_hash TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS question TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_runs ADD COLUMN IF NOT EXISTS report_question_set TEXT")
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.workstreams (
            workstream_id TEXT PRIMARY KEY,
            name TEXT,
            objective TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
    )
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.workstream_runs (
            workstream_id TEXT NOT NULL REFERENCES workflow.workstreams(workstream_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            arm TEXT,
            source_bucket TEXT,
            is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
            parent_run_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (workstream_id, run_id)
        )
        """,
    )
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.workflow_reports (
            run_id TEXT PRIMARY KEY REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            status TEXT,
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            papers_dir TEXT,
            report_path TEXT,
            agentic_status TEXT,
            report_questions_set TEXT,
            report_hash TEXT,
            report_question_count INTEGER,
            confidence_mean DOUBLE PRECISION,
            confidence_label_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            final_answer_hash TEXT,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """,
    )
    _safe_execute(cur, "ALTER TABLE workflow.workflow_reports ADD COLUMN IF NOT EXISTS report_hash TEXT")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_reports ADD COLUMN IF NOT EXISTS report_question_count INTEGER")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_reports ADD COLUMN IF NOT EXISTS confidence_mean DOUBLE PRECISION")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_reports ADD COLUMN IF NOT EXISTS confidence_label_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb")
    _safe_execute(cur, "ALTER TABLE workflow.workflow_reports ADD COLUMN IF NOT EXISTS final_answer_hash TEXT")
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.report_questions (
            run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            question_id TEXT NOT NULL,
            category TEXT,
            question TEXT,
            answer TEXT,
            confidence TEXT,
            confidence_score DOUBLE PRECISION,
            retrieval_method TEXT,
            evidence_type TEXT,
            assumption_flag BOOLEAN,
            assumption_notes TEXT,
            quote_snippet TEXT,
            table_figure TEXT,
            data_source TEXT,
            citation_anchors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            related_questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (run_id, question_id)
        )
        """,
    )
    _safe_execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS workflow.artifacts (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES workflow.workflow_runs(run_id) ON DELETE CASCADE,
            artifact_type TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE (run_id, artifact_type, path)
        )
        """,
    )

    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_status_idx ON workflow.workflow_reports (status)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_started_at_idx ON workflow.workflow_reports (started_at DESC)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_finished_at_idx ON workflow.workflow_reports (finished_at DESC)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_papers_dir_idx ON workflow.workflow_reports (papers_dir)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_agentic_status_idx ON workflow.workflow_reports (agentic_status)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_question_set_idx ON workflow.workflow_reports (report_questions_set)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_report_hash_idx ON workflow.workflow_reports (report_hash)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_runs_workstream_idx ON workflow.workflow_runs (workstream_id)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_workstream_runs_run_idx ON workflow.workstream_runs (run_id)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_report_questions_conf_idx ON workflow.report_questions (confidence)")
    _safe_execute(cur, "CREATE INDEX IF NOT EXISTS workflow_reports_payload_gin_idx ON workflow.workflow_reports USING GIN (payload)")
    _safe_execute(
        cur,
        "CREATE INDEX IF NOT EXISTS workflow_reports_payload_path_gin_idx ON workflow.workflow_reports USING GIN (payload jsonb_path_ops)",
    )
    conn.commit()


def _ensure_workflow_run_row(
    cur,
    *,
    run_id: str,
    status: str,
    papers_dir: str,
    report_path: str | None,
    payload: Dict[str, Any],
) -> None:
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
    if workstream_id:
        cur.execute(
            """
            INSERT INTO workflow.workstreams (workstream_id, name, objective, created_at, metadata_json)
            VALUES (%s, %s, %s, NOW(), %s::jsonb)
            ON CONFLICT (workstream_id) DO UPDATE SET
                metadata_json = workflow.workstreams.metadata_json || EXCLUDED.metadata_json
            """,
            (
                workstream_id,
                workstream_id,
                None,
                json.dumps({"source": "report_store"}, ensure_ascii=False),
            ),
        )
    cur.execute(
        """
        INSERT INTO workflow.workflow_runs
        (
            run_id, created_at, started_at, finished_at, status, papers_dir, config_hash,
            workstream_id, arm, parent_run_id, trigger_source, git_sha, git_branch,
            config_effective_json, paper_set_hash, question, report_question_set, metadata_json
        )
        VALUES (
            %s,
            COALESCE(%s, NOW()),
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s::jsonb,
            %s,
            %s,
            %s,
            %s::jsonb
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            papers_dir = EXCLUDED.papers_dir,
            config_hash = COALESCE(workflow.workflow_runs.config_hash, EXCLUDED.config_hash),
            started_at = COALESCE(workflow.workflow_runs.started_at, EXCLUDED.started_at),
            finished_at = COALESCE(EXCLUDED.finished_at, workflow.workflow_runs.finished_at),
            workstream_id = COALESCE(workflow.workflow_runs.workstream_id, EXCLUDED.workstream_id),
            arm = COALESCE(workflow.workflow_runs.arm, EXCLUDED.arm),
            parent_run_id = COALESCE(workflow.workflow_runs.parent_run_id, EXCLUDED.parent_run_id),
            trigger_source = COALESCE(workflow.workflow_runs.trigger_source, EXCLUDED.trigger_source),
            git_sha = COALESCE(workflow.workflow_runs.git_sha, EXCLUDED.git_sha),
            git_branch = COALESCE(workflow.workflow_runs.git_branch, EXCLUDED.git_branch),
            config_effective_json = CASE
                WHEN workflow.workflow_runs.config_effective_json IS NULL OR workflow.workflow_runs.config_effective_json = '{}'::jsonb
                    THEN EXCLUDED.config_effective_json
                ELSE workflow.workflow_runs.config_effective_json
            END,
            paper_set_hash = COALESCE(workflow.workflow_runs.paper_set_hash, EXCLUDED.paper_set_hash),
            question = COALESCE(workflow.workflow_runs.question, EXCLUDED.question),
            report_question_set = COALESCE(workflow.workflow_runs.report_question_set, EXCLUDED.report_question_set),
            metadata_json = workflow.workflow_runs.metadata_json || EXCLUDED.metadata_json
        """,
        (
            run_id,
            payload.get("started_at"),
            payload.get("started_at"),
            payload.get("finished_at"),
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
            json.dumps({"source": "report_store"}, ensure_ascii=False),
        ),
    )
    if workstream_id:
        cur.execute(
            """
            INSERT INTO workflow.workstream_runs
            (workstream_id, run_id, arm, source_bucket, is_baseline, parent_run_id, created_at, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
            ON CONFLICT (workstream_id, run_id) DO UPDATE SET
                arm = COALESCE(workflow.workstream_runs.arm, EXCLUDED.arm),
                source_bucket = COALESCE(workflow.workstream_runs.source_bucket, EXCLUDED.source_bucket),
                is_baseline = workflow.workstream_runs.is_baseline OR EXCLUDED.is_baseline,
                parent_run_id = COALESCE(workflow.workstream_runs.parent_run_id, EXCLUDED.parent_run_id),
                metadata_json = workflow.workstream_runs.metadata_json || EXCLUDED.metadata_json
            """,
            (
                workstream_id,
                run_id,
                arm,
                "archive" if "archived" in str(report_path or payload.get("report_path") or "").lower() else "current",
                bool(str(arm or "").strip().lower() in {"baseline", "control", "gpt-5"}),
                parent_run_id,
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
            INSERT INTO workflow.report_questions (
                run_id, question_id, category, question, answer, confidence, confidence_score,
                retrieval_method, evidence_type, assumption_flag, assumption_notes,
                quote_snippet, table_figure, data_source, citation_anchors_json,
                related_questions_json, created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb,
                %s::jsonb, NOW(), NOW()
            )
            ON CONFLICT (run_id, question_id) DO UPDATE SET
                category = EXCLUDED.category,
                question = EXCLUDED.question,
                answer = EXCLUDED.answer,
                confidence = EXCLUDED.confidence,
                confidence_score = EXCLUDED.confidence_score,
                retrieval_method = EXCLUDED.retrieval_method,
                evidence_type = EXCLUDED.evidence_type,
                assumption_flag = EXCLUDED.assumption_flag,
                assumption_notes = EXCLUDED.assumption_notes,
                quote_snippet = EXCLUDED.quote_snippet,
                table_figure = EXCLUDED.table_figure,
                data_source = EXCLUDED.data_source,
                citation_anchors_json = EXCLUDED.citation_anchors_json,
                related_questions_json = EXCLUDED.related_questions_json,
                updated_at = NOW()
            """,
            (
                run_id,
                question_id,
                item.get("category"),
                item.get("question"),
                item.get("answer"),
                item.get("confidence"),
                item.get("confidence_score"),
                item.get("retrieval_method"),
                item.get("evidence_type"),
                item.get("assumption_flag"),
                item.get("assumption_notes"),
                item.get("quote_snippet"),
                item.get("table_figure"),
                item.get("data_source"),
                json.dumps(item.get("citation_anchors") if isinstance(item.get("citation_anchors"), list) else [], ensure_ascii=False),
                json.dumps(item.get("related_questions") if isinstance(item.get("related_questions"), list) else [], ensure_ascii=False),
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
        cur.execute(
            """
            INSERT INTO workflow.artifacts (run_id, artifact_type, path, sha256, created_at, meta_json)
            VALUES (%s, %s, %s, %s, NOW(), %s::jsonb)
            ON CONFLICT (run_id, artifact_type, path) DO UPDATE SET
                sha256 = COALESCE(EXCLUDED.sha256, workflow.artifacts.sha256),
                meta_json = workflow.artifacts.meta_json || EXCLUDED.meta_json
            """,
            (
                run_id,
                artifact_type,
                str(path_text),
                sha256,
                json.dumps(meta or {}, ensure_ascii=False),
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
    """Upsert one workflow report row."""
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

    sql_jsonb = """
        INSERT INTO workflow.workflow_reports (
            run_id, status, started_at, finished_at, papers_dir, report_path,
            agentic_status, report_questions_set,
            report_hash, report_question_count, confidence_mean, confidence_label_counts_json,
            final_answer_hash, payload, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s)
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            papers_dir = EXCLUDED.papers_dir,
            report_path = EXCLUDED.report_path,
            agentic_status = EXCLUDED.agentic_status,
            report_questions_set = EXCLUDED.report_questions_set,
            report_hash = EXCLUDED.report_hash,
            report_question_count = EXCLUDED.report_question_count,
            confidence_mean = EXCLUDED.confidence_mean,
            confidence_label_counts_json = EXCLUDED.confidence_label_counts_json,
            final_answer_hash = EXCLUDED.final_answer_hash,
            payload = EXCLUDED.payload,
            updated_at = EXCLUDED.updated_at
    """
    params = (
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
        payload_json,
        now,
        now,
    )

    _ensure_workflow_run_row(
        cur,
        run_id=run_id,
        status=workflow_status,
        papers_dir=papers_dir,
        report_path=report_path,
        payload=payload,
    )

    cur.execute(sql_jsonb, params)
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
