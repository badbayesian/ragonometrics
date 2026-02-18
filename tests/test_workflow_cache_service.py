"""Unit tests for workflow cache service helpers."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.db.connection import pooled_connection
from ragonometrics.services import workflow_cache as workflow_cache_service


def test_paper_match_predicates_and_run_membership() -> None:
    paper_path = "/app/papers/Example_Paper.pdf"
    predicates = workflow_cache_service.paper_match_predicates(paper_path)
    assert predicates["paper_path"] == "/app/papers/example_paper.pdf"
    assert predicates["paper_dir"] == "/app/papers"
    assert predicates["paper_filename"] == "example_paper.pdf"
    assert predicates["basename_like"] == "%/example_paper.pdf"

    assert workflow_cache_service.run_belongs_to_paper({"papers_dir": "/app/papers/Example_Paper.pdf"}, paper_path) is True
    assert workflow_cache_service.run_belongs_to_paper({"papers_dir": "/app/papers"}, paper_path) is True
    assert workflow_cache_service.run_belongs_to_paper({"papers_dir": "/tmp/other.pdf"}, paper_path) is False


def test_list_steps_for_run_uses_canonical_order(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    run_id = "svc-order-run"
    with pooled_connection(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM workflow.run_records WHERE run_id = %s", (run_id,))
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (run_id, record_kind, step, record_key, status, papers_dir, created_at, updated_at, payload_json, metadata_json)
            VALUES (%s, 'run', '', 'main', 'completed', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}'::jsonb, '{}'::jsonb)
            """,
            (run_id, "/app/papers/A.pdf"),
        )
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (run_id, record_kind, step, record_key, status, created_at, updated_at, output_json, metadata_json)
            VALUES
                (%s, 'step', 'report', 'main', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}'::jsonb, '{}'::jsonb),
                (%s, 'step', 'prep', 'main', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}'::jsonb, '{}'::jsonb),
                (%s, 'step', 'agentic', 'main', 'completed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}'::jsonb, '{}'::jsonb)
            """,
            (run_id, run_id, run_id),
        )
        conn.commit()

    steps = workflow_cache_service.list_steps_for_run(run_id)
    names = [str(item.get("step") or "") for item in steps]
    assert names.index("prep") < names.index("agentic")
    assert names.index("agentic") < names.index("report")


def test_derive_agentic_internals_handles_sparse_payload() -> None:
    agentic_step = {"step": "agentic", "status": "completed", "output": {}}
    internals = workflow_cache_service.derive_agentic_internals(agentic_step, [], [])
    assert len(internals) == 5
    ids = {str(item.get("internal_step") or "") for item in internals}
    assert "agentic_plan" in ids
    assert "agentic_subquestion_answer" in ids
    assert "agentic_report_question_answer" in ids
    assert "agentic_synthesis" in ids
    assert "agentic_citations" in ids
