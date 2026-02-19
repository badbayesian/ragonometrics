"""Service tests for multi-paper comparison workflows."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.db.connection import pooled_connection
from ragonometrics.services import paper_compare as compare_service
from ragonometrics.services import papers as papers_service


def _insert_query_cache(*, paper_path: str, model: str, question_norm: str, answer: str, cache_key: str = "k1") -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO retrieval.query_cache
            (cache_key, query, query_normalized, paper_path, model, context_hash, answer, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (cache_key) DO UPDATE SET answer = EXCLUDED.answer, created_at = CURRENT_TIMESTAMP
            """,
            (cache_key, question_norm, question_norm, paper_path, model, "ctx", answer),
        )
        conn.commit()


def _insert_openalex_meta(*, paper_path: str, topics: list[dict], concepts: list[dict]) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    payload = {"topics": topics, "concepts": concepts}
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO enrichment.paper_openalex_metadata
            (
                paper_path, title, authors, query_title, query_authors, query_year,
                openalex_id, openalex_json, match_status, updated_at
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s::jsonb, 'matched', CURRENT_TIMESTAMP
            )
            ON CONFLICT (paper_path) DO UPDATE SET
                openalex_json = EXCLUDED.openalex_json,
                match_status = 'matched',
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                paper_path,
                "title",
                "authors",
                "query title",
                "query authors",
                2025,
                "https://openalex.org/W1",
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()


def test_suggest_similar_papers_ranks_by_topic_overlap(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    r1 = papers_service.PaperRef(paper_id="p1", path="/app/papers/p1.pdf", name="p1.pdf")
    r2 = papers_service.PaperRef(paper_id="p2", path="/app/papers/p2.pdf", name="p2.pdf")
    r3 = papers_service.PaperRef(paper_id="p3", path="/app/papers/p3.pdf", name="p3.pdf")
    monkeypatch.setattr(compare_service, "_paper_ref_map", lambda: {r.paper_id: r for r in [r1, r2, r3]})
    monkeypatch.setattr(
        compare_service,
        "_overview_map",
        lambda refs: {
            "p1": {"paper_id": "p1", "display_title": "Seed macro paper", "display_abstract": "Local projections and VAR"},
            "p2": {"paper_id": "p2", "display_title": "Macro followup", "display_abstract": "VAR and projections"},
            "p3": {"paper_id": "p3", "display_title": "Unrelated topic", "display_abstract": "restaurants calories"},
        },
    )
    _insert_openalex_meta(
        paper_path=r1.path,
        topics=[{"display_name": "Macroeconomics", "score": 0.9}],
        concepts=[{"display_name": "Vector autoregression", "score": 0.8}],
    )
    _insert_openalex_meta(
        paper_path=r2.path,
        topics=[{"display_name": "Macroeconomics", "score": 0.8}],
        concepts=[{"display_name": "Vector autoregression", "score": 0.7}],
    )
    _insert_openalex_meta(
        paper_path=r3.path,
        topics=[{"display_name": "Nutrition", "score": 0.9}],
        concepts=[{"display_name": "Calories", "score": 0.8}],
    )

    out = compare_service.suggest_similar_papers("p1", limit=5)
    assert int(out["count"]) == 2
    assert out["rows"][0]["paper_id"] == "p2"
    assert float(out["rows"][0]["score"]) >= float(out["rows"][1]["score"])


def test_create_comparison_run_cache_first_and_fill_missing(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setattr(compare_service, "load_settings", lambda: SimpleNamespace(chat_model="gpt-5-nano"))
    monkeypatch.setattr(compare_service.structured_service, "structured_report_questions", lambda: [])
    r1 = papers_service.PaperRef(paper_id="p1", path="/app/papers/p1.pdf", name="p1.pdf")
    r2 = papers_service.PaperRef(paper_id="p2", path="/app/papers/p2.pdf", name="p2.pdf")
    monkeypatch.setattr(compare_service, "_paper_ref_map", lambda: {"p1": r1, "p2": r2})
    monkeypatch.setattr(
        compare_service,
        "_overviews_for_ids",
        lambda paper_ids: [
            {"paper_id": pid, "display_title": f"title-{pid}", "name": f"{pid}.pdf"} for pid in paper_ids
        ],
    )

    qnorm = "what is identification strategy"
    _insert_query_cache(
        paper_path=r1.path,
        model="gpt-5-nano",
        question_norm=qnorm,
        answer="cached answer",
        cache_key="cache-p1-q1",
    )

    created = compare_service.create_comparison_run(
        seed_paper_id="p1",
        paper_ids=["p1", "p2"],
        questions=["What is identification strategy?"],
        model="gpt-5-nano",
        name="cmp",
        created_by_user_id=1,
        created_by_username="tester",
    )
    assert str(created.get("comparison_id") or "")
    summary = created.get("summary") or {}
    assert int(summary.get("cached_cells") or 0) == 1
    assert int(summary.get("missing_cells") or 0) == 1

    monkeypatch.setattr(
        compare_service.chat_service,
        "chat_turn",
        lambda **kwargs: {"answer": "generated answer", "cache_hit_layer": "none"},
    )
    updated = compare_service.fill_missing_cells(comparison_id=str(created["comparison_id"]))
    updated_summary = updated.get("summary") or {}
    assert int(updated_summary.get("generated_cells") or 0) >= 1
    assert int(updated_summary.get("missing_cells") or 0) == 0


def test_create_comparison_validates_limits(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setattr(compare_service, "load_settings", lambda: SimpleNamespace(chat_model="gpt-5-nano"))
    refs = {
        f"p{i}": papers_service.PaperRef(paper_id=f"p{i}", path=f"/app/papers/p{i}.pdf", name=f"p{i}.pdf")
        for i in range(1, 13)
    }
    monkeypatch.setattr(compare_service, "_paper_ref_map", lambda: refs)
    try:
        compare_service.create_comparison_run(
            seed_paper_id="p1",
            paper_ids=[f"p{i}" for i in range(1, 12)],
            questions=["Q1"],
            model="gpt-5-nano",
            name="too-many",
            created_by_user_id=1,
            created_by_username="tester",
        )
        assert False, "expected validation error"
    except ValueError as exc:
        assert "At most" in str(exc)
