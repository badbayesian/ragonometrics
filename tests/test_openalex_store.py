"""Unit tests for OpenAlex title+author metadata storage helpers."""

from pathlib import Path

from ragonometrics.integrations.openalex_store import (
    _openalex_author_names,
    _resolve_openalex_metadata_for_paper,
    _year_from_path,
)


def test_year_from_path_extracts_parenthesized_year() -> None:
    path = Path("papers/Calorie Posting in Chain Restaurants - Bollinger et al. (2011).pdf")
    assert _year_from_path(path) == 2011


def test_year_from_path_returns_none_without_year() -> None:
    path = Path("papers/NetworksElectoralCompetition.pdf")
    assert _year_from_path(path) is None


def test_openalex_author_names_dedupes_in_order() -> None:
    meta = {
        "authorships": [
            {"author": {"display_name": "Phillip Leslie"}},
            {"author": {"display_name": "Alan Sorensen"}},
            {"author": {"display_name": "Phillip Leslie"}},
            {"author": {"display_name": ""}},
            {"author": {}},
            {},
        ]
    }
    assert _openalex_author_names(meta) == ["Phillip Leslie", "Alan Sorensen"]


def test_resolve_openalex_metadata_for_paper_uses_ai_title_fallback(monkeypatch) -> None:
    calls = []

    def _fake_fetch_openalex_metadata(*, title, author, year=None, doi=None, cache_path=None, timeout=10):
        calls.append(title)
        if title == "Bad Title":
            return {"id": "https://openalex.org/W_bad", "display_name": "Wrong Paper", "publication_year": year}
        if title == "Good Economics Title":
            return {"id": "https://openalex.org/W_good", "display_name": title, "publication_year": year}
        return None

    def _fake_is_economics_work(meta):
        return str(meta.get("id") or "").endswith("W_good")

    def _fake_extract_title_from_first_page_with_ai(*, paper_path, fallback_title, first_page_text=None):
        assert fallback_title == "Bad Title"
        return "Good Economics Title"

    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.fetch_openalex_metadata",
        _fake_fetch_openalex_metadata,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.is_economics_work",
        _fake_is_economics_work,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._extract_title_from_first_page_with_ai",
        _fake_extract_title_from_first_page_with_ai,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._find_economics_match_via_author_catalog",
        lambda **kwargs: None,
    )

    meta, effective_title, note = _resolve_openalex_metadata_for_paper(
        paper_path=Path("papers/sample.pdf"),
        query_title="Bad Title",
        query_authors="Author A",
        query_year=2011,
        first_page_text="sample text",
    )

    assert meta is not None
    assert meta["id"] == "https://openalex.org/W_good"
    assert effective_title == "Good Economics Title"
    assert calls == ["Bad Title", "Good Economics Title"]
    assert note is not None


def test_resolve_openalex_metadata_for_paper_returns_none_when_not_econ(monkeypatch) -> None:
    def _fake_fetch_openalex_metadata(*, title, author, year=None, doi=None, cache_path=None, timeout=10):
        return {"id": "https://openalex.org/W_non_econ", "display_name": title, "publication_year": year}

    def _fake_is_economics_work(meta):
        return False

    def _fake_extract_title_from_first_page_with_ai(*, paper_path, fallback_title, first_page_text=None):
        return None

    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.fetch_openalex_metadata",
        _fake_fetch_openalex_metadata,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.is_economics_work",
        _fake_is_economics_work,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._extract_title_from_first_page_with_ai",
        _fake_extract_title_from_first_page_with_ai,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._find_economics_match_via_author_catalog",
        lambda **kwargs: None,
    )

    meta, effective_title, note = _resolve_openalex_metadata_for_paper(
        paper_path=Path("papers/sample.pdf"),
        query_title="Some Title",
        query_authors="Author A",
        query_year=2011,
        first_page_text="sample text",
    )

    assert meta is None
    assert effective_title == "Some Title"
    assert note is not None
    assert "No economics OpenAlex match" in note


def test_resolve_openalex_metadata_for_paper_uses_author_catalog_fallback(monkeypatch) -> None:
    def _fake_fetch_openalex_metadata(*, title, author, year=None, doi=None, cache_path=None, timeout=10):
        return None

    def _fake_author_catalog(**kwargs):
        assert kwargs["query_title"] == "Some Title"
        return {"id": "https://openalex.org/W_author"}

    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.fetch_openalex_metadata",
        _fake_fetch_openalex_metadata,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._find_economics_match_via_author_catalog",
        _fake_author_catalog,
    )

    meta, effective_title, note = _resolve_openalex_metadata_for_paper(
        paper_path=Path("papers/sample.pdf"),
        query_title="Some Title",
        query_authors="Author A",
        query_year=2011,
        first_page_text="sample text",
    )

    assert meta == {"id": "https://openalex.org/W_author"}
    assert effective_title == "Some Title"
    assert note == "Resolved via author-catalog fallback."


def test_resolve_openalex_metadata_for_paper_accepts_forced_work_id_without_econ_label(monkeypatch) -> None:
    def _fake_fetch_openalex_metadata(*, title, author, year=None, doi=None, cache_path=None, timeout=10):
        return {
            "id": "https://api.openalex.org/w2075304461",
            "display_name": "Use of Cumulative Sums of Squares for Retrospective Detection of Changes of Variance",
            "publication_year": 1994,
        }

    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.fetch_openalex_metadata",
        _fake_fetch_openalex_metadata,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.get_title_override_work_id",
        lambda title: "W2075304461",
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store.is_economics_work",
        lambda meta: False,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._find_economics_match_via_author_catalog",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "ragonometrics.integrations.openalex_store._extract_title_from_first_page_with_ai",
        lambda **kwargs: None,
    )

    meta, effective_title, note = _resolve_openalex_metadata_for_paper(
        paper_path=Path("papers/Use_of_Cumulative_Sums_of_Squares_for_Re.pdf"),
        query_title="Use_of_Cumulative_Sums_of_Squares_for_Re",
        query_authors="Carla Inclan, George C. Tiao",
        query_year=1994,
        first_page_text="sample text",
    )

    assert meta is not None
    assert meta["id"] == "https://api.openalex.org/w2075304461"
    assert effective_title == "Use_of_Cumulative_Sums_of_Squares_for_Re"
    assert note is None
