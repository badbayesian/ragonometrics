"""Tests for Streamlit structured export compact/full bundle behavior."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from ragonometrics.core.main import Paper
from ragonometrics.ui import streamlit_app


def _paper() -> Paper:
    return Paper(
        path=Path("/tmp/example-paper.pdf"),
        title="Example Paper",
        author="Example Author",
        text="",
    )


def test_is_valid_structured_question_text_rejects_malformed_response_config() -> None:
    bad = "ResponseTextConfig(format=ResponseFormatText(type='text'), verbosity='medium')"
    assert streamlit_app._is_valid_structured_question_text(bad) is False
    assert streamlit_app._is_valid_structured_question_text("What is the main research question?") is True


def test_structured_workstream_export_bundle_compact_stays_minimal() -> None:
    question = "What is the main research question of the paper?"
    bundle = streamlit_app._structured_workstream_export_bundle(
        paper=_paper(),
        questions=[{"id": "A01", "category": "A) Research question", "question": question}],
        cached_map={
            streamlit_app._normalize_question_key(question): {
                "answer": "The paper studies X.",
                "model": "gpt-5-nano",
                "created_at": "2026-02-17T00:00:00+00:00",
                "source": "workflow.run_records",
                "run_id": "streamlit-structured-abc",
                "question_id": "A01",
            }
        },
        selected_model="gpt-5-nano",
        cache_scope="Selected model only",
        export_format="Compact",
        question_records={},
    )

    assert bundle["export_format"] == "compact"
    row = bundle["questions"][0]
    assert row["cached"] is True
    assert "workflow_record" not in row
    assert "structured_fields" not in row
    assert "full_summary" not in bundle


def test_structured_workstream_export_bundle_full_prefers_richer_global_record() -> None:
    question = "What is the main research question of the paper?"
    question_key = streamlit_app._normalize_question_key(question)
    rich_record = {
        "run_id": "workflow-rich-run",
        "question_id": "A01",
        "payload_json": {
            "id": "A01",
            "question": question,
            "answer": "Rich answer",
            "evidence_type": "retrieved_context",
            "confidence": "high",
            "confidence_score": 0.92,
            "retrieval_method": "hybrid",
            "citation_anchors": [{"page": 2, "start_word": 10, "end_word": 24, "section": "intro", "note": None}],
            "quote_snippet": "A short quote.",
        },
        "output_json": {},
        "metadata_json": {},
    }
    compact_record = {
        "run_id": "streamlit-structured-abc",
        "question_id": "A01",
        "payload_json": {"id": "A01", "question": question, "answer": "Compact answer"},
        "output_json": {},
        "metadata_json": {},
    }

    bundle = streamlit_app._structured_workstream_export_bundle(
        paper=_paper(),
        questions=[{"id": "A01", "category": "A) Research question", "question": question}],
        cached_map={
            question_key: {
                "answer": "The paper studies X.",
                "model": "gpt-5-nano",
                "created_at": "2026-02-17T00:00:00+00:00",
                "source": "workflow.run_records",
                "run_id": "streamlit-structured-abc",
                "question_id": "A01",
            }
        },
        selected_model="gpt-5-nano",
        cache_scope="Selected model only",
        export_format="Full",
        question_records={
            "streamlit-structured-abc::A01": compact_record,
            f"q::{question_key}": rich_record,
        },
    )

    assert bundle["export_format"] == "full"
    row = bundle["questions"][0]
    assert row["workflow_record"]["run_id"] == "workflow-rich-run"
    assert row["structured_fields"]["confidence_score"] == 0.92
    assert isinstance(row["structured_fields"]["citation_anchors"], list)
    assert bundle["full_summary"]["rows_with_workflow_record"] == 1
    assert bundle["full_summary"]["rows_with_citation_anchors"] == 1

