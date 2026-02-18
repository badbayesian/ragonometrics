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

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["timeout"] = timeout
        return {"results": [{"id": "https://openalex.org/W999"}]}

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    results = openalex.openalex_work_by_title(
        title="Calorie Posting in Chain Restaurants",
        api_key="test_key",
        timeout=30,
    )

    assert results == [{"id": "https://openalex.org/W999"}]
    assert captured["url"] == "https://api.openalex.org/works"
    assert captured["params"]["search"] == '"Calorie Posting in Chain Restaurants"'
    assert captured["params"]["per-page"] == 1
    assert captured["params"]["api_key"] == "test_key"
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


def test_sanitize_title_for_lookup_removes_filename_suffix_author_year() -> None:
    raw = "Bundle-Size Pricing as an Approximation to Mixed Bundling - Chu et al. (2010)"
    assert openalex._sanitize_title_for_lookup(raw) == "Bundle-Size Pricing as an Approximation to Mixed Bundling"


def test_normalize_author_for_search_drops_placeholders() -> None:
    assert openalex._normalize_author_for_search("Unknown") is None
    assert openalex._normalize_author_for_search("N/A") is None
    assert openalex._normalize_author_for_search("none") is None
    assert openalex._normalize_author_for_search("Phillip Leslie") == "Phillip Leslie"


def test_build_title_lookup_variants_generates_projects_projections_and_var_vars() -> None:
    title = "Local Projects or VAR A Primer for Macroeconomists"
    variants = openalex._build_title_lookup_variants(title)

    assert variants
    assert variants[0] == "Local Projects or VAR A Primer for Macroeconomists"
    lowered = [variant.lower() for variant in variants]
    assert any("local projections or vars a primer for macroeconomists" in variant for variant in lowered)
    assert len(lowered) == len(set(lowered))


def test_normalize_openalex_work_id_supports_api_url() -> None:
    assert openalex._normalize_openalex_work_id("https://api.openalex.org/w2075304461") == "W2075304461"


def test_fetch_openalex_metadata_uses_title_override(monkeypatch) -> None:
    seen: Dict[str, int] = {"fetch_by_id": 0}

    def _fake_get_cache(*args, **kwargs):
        return {"id": "https://openalex.org/W111", "display_name": "Wrong"}

    def _fake_fetch_by_id(work_id: str, select: str = openalex.DEFAULT_SELECT, timeout: int = 10):
        seen["fetch_by_id"] += 1
        assert work_id == "W2075304461"
        return {
            "id": "https://openalex.org/W2075304461",
            "display_name": "Use of Cumulative Sums of Squares for Retrospective Detection of Changes of Variance",
            "publication_year": 1994,
            "authorships": [{"author": {"display_name": "George C. Tiao"}}],
        }

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("Search fallback should not run when title override is available.")

    monkeypatch.setattr(openalex, "get_cached_metadata", _fake_get_cache)
    monkeypatch.setattr(openalex, "set_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(
        openalex,
        "_load_title_override_rows",
        lambda *a, **k: [
            {
                "title_pattern": "use of cumulative sums of squares",
                "match_type": "contains",
                "openalex_work_id": "https://api.openalex.org/w2075304461",
            }
        ],
    )
    monkeypatch.setattr(openalex, "fetch_work_by_id", _fake_fetch_by_id)
    monkeypatch.setattr(openalex, "search_work_by_title", _should_not_be_called)
    monkeypatch.setattr(openalex, "search_work_by_title_author_year", _should_not_be_called)

    result = openalex.fetch_openalex_metadata(
        title="Use of Cumulative Sums of Squares for Retrospective Detection of Changes of Variance",
        author="Carla Inclan; George C. Tiao",
        year=1994,
        doi=None,
    )

    assert seen["fetch_by_id"] == 1
    assert result is not None
    assert result.get("id") == "https://openalex.org/W2075304461"


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


def test_fetch_openalex_metadata_ignores_cached_non_matching_title(monkeypatch) -> None:
    def _fake_get_cache(*args, **kwargs):
        return {
            "id": "https://openalex.org/Wwrong",
            "display_name": "A comprehensive survey on support vector machine classification",
            "publication_year": 2020,
            "authorships": [],
        }

    called = {"title": 0}

    def _fake_search_title(title: str, select: str = openalex.DEFAULT_SELECT, timeout: int = 10):
        called["title"] += 1
        return {
            "id": "https://openalex.org/W2099952424",
            "display_name": "Bundle-Size Pricing as an Approximation to Mixed Bundling",
            "publication_year": 2011,
            "authorships": [{"author": {"display_name": "Phillip Leslie"}}],
        }

    monkeypatch.setattr(openalex, "get_cached_metadata", _fake_get_cache)
    monkeypatch.setattr(openalex, "search_work_by_title", _fake_search_title)
    monkeypatch.setattr(openalex, "set_cached_metadata", lambda *a, **k: None)

    result = openalex.fetch_openalex_metadata(
        title="Bundle-Size Pricing as an Approximation to Mixed Bundling - Chu et al. (2010)",
        author="Phillip Leslie",
        year=2010,
        doi=None,
    )

    assert result is not None
    assert result.get("id") == "https://openalex.org/W2099952424"
    assert called["title"] == 1


def test_fetch_openalex_metadata_uses_variant_fallback_when_direct_lookup_fails(monkeypatch) -> None:
    seen_queries = []

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        query = str((params or {}).get("search") or "")
        if query:
            seen_queries.append(query)
        lowered = query.lower()
        if "projections" in lowered and "vars" in lowered:
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W4411016694",
                        "display_name": "Local Projections or VARs? A Primer for Macroeconomists",
                        "publication_year": 2025,
                        "authorships": [],
                        "cited_by_count": 3,
                    }
                ]
            }
        return {"results": []}

    monkeypatch.setattr(openalex, "get_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "set_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title_author_year", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    result = openalex.fetch_openalex_metadata(
        title="Local Projects or VAR A Primer for Macroeconomists",
        author="Unknown",
        year=2025,
        doi=None,
    )

    assert result is not None
    assert result.get("id") == "https://openalex.org/W4411016694"
    assert any("projections" in query.lower() and "vars" in query.lower() for query in seen_queries)


def test_fetch_openalex_metadata_variant_fallback_rejects_non_plausible_candidate(monkeypatch) -> None:
    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        return {
            "results": [
                {
                    "id": "https://openalex.org/W999",
                    "display_name": "Calorie Posting in Chain Restaurants",
                    "publication_year": 2011,
                    "authorships": [],
                    "cited_by_count": 999,
                }
            ]
        }

    monkeypatch.setattr(openalex, "get_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "set_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title_author_year", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    result = openalex.fetch_openalex_metadata(
        title="Local Projects or VAR A Primer for Macroeconomists",
        author="Unknown",
        year=2025,
        doi=None,
    )

    assert result is None


def test_fetch_openalex_metadata_does_not_include_unknown_author_in_queries(monkeypatch) -> None:
    seen_queries = []

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        query = str((params or {}).get("search") or "")
        if query:
            seen_queries.append(query)
        return {"results": []}

    monkeypatch.setattr(openalex, "get_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "set_cached_metadata", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "search_work_by_title_author_year", lambda *a, **k: None)
    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    result = openalex.fetch_openalex_metadata(
        title="Local Projects or VAR A Primer for Macroeconomists",
        author="Unknown",
        year=2025,
        doi=None,
    )

    assert result is None
    assert seen_queries
    assert all("unknown" not in query.lower() for query in seen_queries)


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


def test_is_economics_work_true_from_concepts() -> None:
    meta = {
        "concepts": [
            {"display_name": "Marketing"},
            {"display_name": "Economics"},
        ]
    }
    assert openalex.is_economics_work(meta) is True


def test_is_economics_work_true_from_venue_name() -> None:
    meta = {
        "primary_location": {
            "source": {
                "display_name": "American Economic Journal Economic Policy",
            }
        }
    }
    assert openalex.is_economics_work(meta) is True


def test_is_economics_work_false_when_no_econ_signals() -> None:
    meta = {
        "primary_topic": {
            "field": {"display_name": "Decision Sciences"},
            "subfield": {"display_name": "Statistics"},
        },
        "concepts": [
            {"display_name": "Mathematics"},
            {"display_name": "Variance"},
        ],
    }
    assert openalex.is_economics_work(meta) is False


def test_search_authors_by_name_prefers_display_name_filter(monkeypatch) -> None:
    calls = []

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        calls.append(dict(params or {}))
        if params and params.get("filter", "").startswith("display_name.search:"):
            return {
                "results": [
                    {"id": "https://openalex.org/A1", "display_name": "Phillip Leslie"},
                    {"id": "https://openalex.org/A2", "display_name": "Other"},
                ]
            }
        return None

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    out = openalex.search_authors_by_name("Phillip Leslie", limit=2, timeout=11)
    assert len(out) == 2
    assert out[0]["id"] == "https://openalex.org/A1"
    assert calls[0]["filter"] == "display_name.search:Phillip Leslie"


def test_list_works_for_author_uses_author_filter(monkeypatch) -> None:
    calls = []

    def _fake_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10):
        payload = dict(params or {})
        calls.append(payload)
        page = int(payload.get("page") or 1)
        if page == 1:
            return {"results": [{"id": "https://openalex.org/W1"}, {"id": "https://openalex.org/W2"}]}
        return {"results": []}

    monkeypatch.setattr(openalex, "_request_json", _fake_request_json)

    works = openalex.list_works_for_author("A5048277762", per_page=2, max_pages=3, timeout=9)
    assert works == [{"id": "https://openalex.org/W1"}, {"id": "https://openalex.org/W2"}]
    assert calls[0]["filter"] == "author.id:https://openalex.org/A5048277762"
