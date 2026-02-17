"""Tests for Postgres JSON-style workflow report storage helpers."""

import importlib.util
import json
from pathlib import Path


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


report_store = _load_mod("ragonometrics/pipeline/report_store.py", "ragonometrics.pipeline.report_store")


def test_upsert_workflow_report():
    conn = report_store.connect("dummy", require_migrated=False)
    report_store.ensure_workflow_report_store(conn)

    payload = {
        "run_id": "run-report-store-1",
        "started_at": "2026-02-15T00:00:00+00:00",
        "finished_at": "2026-02-15T00:01:00+00:00",
        "papers_dir": "papers/example.pdf",
        "prep": {"status": "completed"},
        "agentic": {"status": "completed", "report_questions_set": "structured"},
    }
    report_store.upsert_workflow_report(
        conn,
        run_id="run-report-store-1",
        report_path="reports/workflow-report-run-report-store-1.json",
        payload=payload,
        status="completed",
    )

    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_id, status, papers_dir, agentic_status, report_question_set
        FROM run_records
        WHERE run_id = %s AND record_kind = 'report'
        """,
        ("run-report-store-1",),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "run-report-store-1"
    assert row[1] == "completed"
    assert row[2] == "papers/example.pdf"
    assert row[3] == "completed"
    assert row[4] == "structured"


def test_store_workflow_reports_from_dir(tmp_path: Path):
    payload = {
        "run_id": "run-report-store-2",
        "started_at": "2026-02-15T00:00:00+00:00",
        "finished_at": "2026-02-15T00:02:00+00:00",
        "papers_dir": "papers/example-2.pdf",
        "prep": {"status": "completed"},
        "agentic": {"status": "skipped"},
    }
    path = tmp_path / "workflow-report-run-report-store-2.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    stats = report_store.store_workflow_reports_from_dir(
        reports_dir=tmp_path,
        db_url="dummy",
        recursive=False,
        limit=0,
    )
    assert stats["total"] == 1
    assert stats["stored"] == 1
    assert stats["skipped"] == 0
