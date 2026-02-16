"""Tests for explicit OpenAlex search request construction."""

from typing import Any, Dict, Optional

from ragonometrics.integrations import openalex


def test_search_work_by_title_author_year_uses_expected_params(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["timeout"] = timeout
        return {"results": [{"id": "https://openalex.org/W123"}]}

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    title = "Calorie Posting in Chain Restaurants"
    author = "Bryan Bollinger Phillip Leslie Alan Sorensen"
    year = 2011
    result = openalex.search_work_by_title_author_year(
        title=title,
        author=author,
        year=year,
        timeout=15,
    )

    assert result == {"id": "https://openalex.org/W123"}
    assert captured["url"] == "https://api.openalex.org/works"
    assert captured["params"]["search"] == f"{title} {author} {year}"
    assert captured["params"]["per-page"] == 1
    assert captured["params"]["select"] == openalex.DEFAULT_SELECT
    assert captured["timeout"] == 15


def test_search_work_by_title_author_year_returns_none_without_title(monkeypatch) -> None:
    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        raise AssertionError("request should not be called when title is missing")

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    assert openalex.search_work_by_title_author_year(title="") is None


def test_openalex_work_by_title_uses_quoted_search_and_api_key(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Dict[str, Any]:
            return {"results": [{"id": "https://openalex.org/W999"}]}

    def _fake_get(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(openalex.requests, "get", _fake_get)

    results = openalex.openalex_work_by_title(
        title="Calorie Posting in Chain Restaurants",
        api_key="test_key",
        timeout=30,
    )

    assert results == [{"id": "https://openalex.org/W999"}]
    assert captured["url"] == 'https://api.openalex.org/works?search=%22Calorie%20Posting%20in%20Chain%20Restaurants%22&per-page=1'
    assert captured["params"] == {"api_key": "test_key"}
    assert captured["timeout"] == 30


def test_openalex_work_by_title_requires_inputs() -> None:
    try:
        openalex.openalex_work_by_title(title="", api_key="x")
    except RuntimeError as exc:
        assert "title is required" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for empty title")

    try:
        openalex.openalex_work_by_title(title="Any", api_key="")
    except RuntimeError as exc:
        assert "api_key is required" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for empty api_key")


def test_sanitize_title_for_lookup_removes_html_footnote_artifacts() -> None:
    raw = "FOOD DESERTS AND THE CAUSES OF NUTRITIONAL INEQUALITY&ast;"
    assert openalex._sanitize_title_for_lookup(raw) == "FOOD DESERTS AND THE CAUSES OF NUTRITIONAL INEQUALITY"


def test_fetch_openalex_metadata_prefers_exact_title_then_falls_back(monkeypatch) -> None:
    calls: Dict[str, int] = {"title": 0, "tay": 0}

    def _fake_cache(*args, **kwargs):
        return None

    def _fake_set_cache(*args, **kwargs):
        return None

    def _fake_by_title(title: str, select: str = openalex.DEFAULT_SELECT, timeout: int = 10):
        calls["title"] += 1
        return None

    def _fake_tay(*, title: str, author: Optional[str] = None, year: Optional[int] = None, select: str = openalex.DEFAULT_SELECT, timeout: int = 10):
        calls["tay"] += 1
        if year == 2011:
            return {"id": "https://openalex.org/W555", "display_name": title, "publication_year": year}
        return None

    monkeypatch.setattr(openalex, "get_cached_metadata", _fake_cache)
    monkeypatch.setattr(openalex, "set_cached_metadata", _fake_set_cache)
    monkeypatch.setattr(openalex, "search_work_by_title", _fake_by_title)
    monkeypatch.setattr(openalex, "search_work_by_title_author_year", _fake_tay)

    result = openalex.fetch_openalex_metadata(
        title="FOOD DESERTS AND THE CAUSES OF NUTRITIONAL INEQUALITY&ast;",
        author="Charles Courtemanche",
        year=2011,
        doi=None,
    )

    assert result is not None
    assert result.get("id") == "https://openalex.org/W555"
    assert calls["title"] == 1
    assert calls["tay"] >= 1


def test_search_work_by_title_falls_back_when_select_fails(monkeypatch) -> None:
    calls = []

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        calls.append(dict(params or {}))
        # Simulate OpenAlex rejecting the `select` payload on search.
        if params and "select" in params:
            return None
        return {"results": [{"id": "https://openalex.org/W777"}]}

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    result = openalex.search_work_by_title("Food Deserts and the Causes of Nutritional Inequality")

    assert result == {"id": "https://openalex.org/W777"}
    assert len(calls) == 2
    assert "select" in calls[0]
    assert "select" not in calls[1]
