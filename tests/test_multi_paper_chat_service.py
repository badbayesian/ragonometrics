"""Unit tests for multi-paper chat service helpers."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.services import multi_paper_chat as svc
from ragonometrics.services import papers as papers_service


def test_validate_paper_ids_dedupes_and_enforces_bounds(monkeypatch) -> None:
    refs = {
        "p1": papers_service.PaperRef(paper_id="p1", path="/app/papers/p1.pdf", name="p1.pdf"),
        "p2": papers_service.PaperRef(paper_id="p2", path="/app/papers/p2.pdf", name="p2.pdf"),
        "p3": papers_service.PaperRef(paper_id="p3", path="/app/papers/p3.pdf", name="p3.pdf"),
    }
    monkeypatch.setattr(svc, "_paper_ref_map", lambda **kwargs: refs)

    out = svc._validate_paper_ids(["p1", "p2", "p2", "p3"], project_id="default-shared")
    assert [row.paper_id for row in out] == ["p1", "p2", "p3"]

    try:
        svc._validate_paper_ids(["p1"], project_id="default-shared")
        raise AssertionError("Expected ValueError for too few papers")
    except ValueError as exc:
        assert "At least 2 papers" in str(exc)

    try:
        svc._validate_paper_ids(["p1", "missing"], project_id="default-shared")
        raise AssertionError("Expected ValueError for unknown paper id")
    except ValueError as exc:
        assert "Unknown paper ids" in str(exc)


def test_stream_multi_chat_turn_emits_start_delta_done(monkeypatch) -> None:
    refs = [
        papers_service.PaperRef(paper_id="p1", path="/app/papers/p1.pdf", name="p1.pdf"),
        papers_service.PaperRef(paper_id="p2", path="/app/papers/p2.pdf", name="p2.pdf"),
    ]
    monkeypatch.setattr(svc, "_validate_paper_ids", lambda paper_ids, project_id=None: refs)

    def _fake_synthesis_payload(**kwargs):
        on_paper_answer = kwargs.get("on_paper_answer")
        on_delta = kwargs.get("on_delta")
        if callable(on_paper_answer):
            on_paper_answer(
                {
                    "paper_id": "p1",
                    "paper_title": "Paper 1",
                    "answer": "Answer 1",
                    "cache_hit": True,
                    "cache_hit_layer": "strict",
                    "provenance": {"score": 0.8, "status": "high"},
                },
                idx=1,
                total=2,
            )
        if callable(on_delta):
            on_delta("Hello")
            on_delta("Hello world")
        return {
            "conversation_id": "conv-1",
            "answer": "Hello world",
            "model": "gpt-5-nano",
            "request_id": str(kwargs.get("request_id") or "req-1"),
            "scope": {"mode": "multi", "paper_ids": ["p1", "p2"], "seed_paper_id": "p1", "paper_count": 2},
            "paper_answers": [],
            "comparison_summary": {},
            "aggregate_provenance": {"score": 0.8, "status": "high", "per_paper": [], "coverage": {}},
            "suggested_followups": [],
            "suggested_papers": {"project": [], "external": []},
        }

    monkeypatch.setattr(svc, "_synthesis_payload", _fake_synthesis_payload)

    rows = list(
        svc.stream_multi_chat_turn(
            paper_ids=["p1", "p2"],
            question="What do these papers agree on?",
            conversation_id="conv-1",
            request_id="req-1",
        )
    )
    assert len(rows) >= 4
    assert '"event": "start"' in rows[0]
    assert any('"event": "paper_answer"' in row for row in rows)
    assert any('"event": "delta"' in row for row in rows)
    assert '"event": "done"' in rows[-1]


def test_selected_paper_interaction_graph_builds_overlap_and_citation_edges(monkeypatch) -> None:
    refs = [
        papers_service.PaperRef(paper_id="p1", path="/app/papers/p1.pdf", name="p1.pdf"),
        papers_service.PaperRef(paper_id="p2", path="/app/papers/p2.pdf", name="p2.pdf"),
    ]
    monkeypatch.setattr(svc, "_validate_paper_ids", lambda paper_ids, project_id=None: refs)
    monkeypatch.setattr(
        svc.papers_service,
        "paper_overview",
        lambda ref: {
            "paper_id": ref.paper_id,
            "display_title": f"Title {ref.paper_id}",
            "display_authors": "Alice, Bob" if ref.paper_id == "p1" else "Alice, Carol",
            "openalex_url": f"https://openalex.org/W{1 if ref.paper_id == 'p1' else 2}",
            "publication_year": 2020 if ref.paper_id == "p1" else 2021,
        },
    )

    def _fake_meta(ref):
        if ref.paper_id == "p1":
            return {
                "id": "https://openalex.org/W1",
                "authorships": [{"author": {"display_name": "Alice"}}, {"author": {"display_name": "Bob"}}],
                "topics": [{"display_name": "econometrics", "score": 0.9}],
                "concepts": [{"display_name": "causal inference", "score": 0.8}],
                "referenced_works": ["https://openalex.org/W2"],
            }
        return {
            "id": "https://openalex.org/W2",
            "authorships": [{"author": {"display_name": "Alice"}}, {"author": {"display_name": "Carol"}}],
            "topics": [{"display_name": "econometrics", "score": 0.8}],
            "concepts": [{"display_name": "causal inference", "score": 0.7}],
            "referenced_works": [],
        }

    monkeypatch.setattr(svc, "_extract_openalex_meta", _fake_meta)

    out = svc.selected_paper_interaction_graph(paper_ids=["p1", "p2"], min_similarity=0.0)
    assert len(out["nodes"]) == 2
    edge_types = {str(item.get("type") or "") for item in out["edges"]}
    assert "cites" in edge_types
    assert "cited_by_selected" in edge_types
    assert "topic_overlap" in edge_types
    assert "concept_overlap" in edge_types
    assert "author_overlap" in edge_types
