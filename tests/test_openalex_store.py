"""Unit tests for OpenAlex title+author metadata storage helpers."""

from pathlib import Path

from ragonometrics.integrations.openalex_store import _openalex_author_names, _year_from_path


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
