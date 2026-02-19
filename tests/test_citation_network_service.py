"""Unit tests for OpenAlex citation network n-hop behavior."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.services.citation_network import (
    _graph_cache_key,
    _now_utc,
    citation_network_for_paper,
)
from ragonometrics.services.papers import PaperRef


def _install_openalex_stubs(monkeypatch) -> None:
    works = {
        "W1": {
            "display_name": "Center",
            "publication_year": 2020,
            "doi": "https://doi.org/10.1/center",
            "cited_by_count": 100,
            "referenced_works": ["https://openalex.org/W2"],
        },
        "W2": {
            "display_name": "Reference 1",
            "publication_year": 2018,
            "doi": "https://doi.org/10.1/ref1",
            "cited_by_count": 40,
            "referenced_works": ["https://openalex.org/W4"],
        },
        "W3": {
            "display_name": "Citing 1",
            "publication_year": 2022,
            "doi": "https://doi.org/10.1/cit1",
            "cited_by_count": 55,
            "referenced_works": [],
        },
        "W4": {
            "display_name": "Second-hop node",
            "publication_year": 2016,
            "doi": "https://doi.org/10.1/hop2",
            "cited_by_count": 12,
            "referenced_works": [],
        },
    }
    citing_map = {
        "W1": ["W3"],
        "W2": [],
        "W3": [],
        "W4": [],
    }

    def _fake_request_json(url, params=None, timeout=20):  # noqa: ARG001
        if str(url).startswith("https://api.openalex.org/works/"):
            key = str(url).rstrip("/").rsplit("/", 1)[-1]
            row = works.get(key)
            if not row:
                return {}
            select = str((params or {}).get("select") or "")
            out = {
                "id": f"https://openalex.org/{key}",
                "display_name": row["display_name"],
                "publication_year": row["publication_year"],
                "doi": row["doi"],
                "cited_by_count": row["cited_by_count"],
            }
            if "referenced_works" in select:
                out["referenced_works"] = list(row["referenced_works"])
            return out
        if str(url).rstrip("/") == "https://api.openalex.org/works":
            filt = str((params or {}).get("filter") or "")
            if filt.startswith("cites:"):
                target = filt.split(":", 1)[1]
                ids = citing_map.get(target, [])
                return {
                    "results": [
                        {
                            "id": f"https://openalex.org/{wid}",
                            "display_name": works[wid]["display_name"],
                            "publication_year": works[wid]["publication_year"],
                            "doi": works[wid]["doi"],
                            "cited_by_count": works[wid]["cited_by_count"],
                        }
                        for wid in ids
                    ]
                }
        return {}

    paper = SimpleNamespace(openalex={"id": "https://openalex.org/W1"})
    monkeypatch.setattr(
        "ragonometrics.services.citation_network.load_prepared",
        lambda _ref: (paper, None, None, None),
    )
    monkeypatch.setattr(
        "ragonometrics.services.citation_network.openalex_request_json",
        _fake_request_json,
    )


def test_citation_network_defaults_to_two_hops(monkeypatch) -> None:
    _install_openalex_stubs(monkeypatch)
    ref = PaperRef(paper_id="p1", path="papers/p1.pdf", name="p1.pdf")
    out = citation_network_for_paper(ref, max_references=5, max_citing=5)
    assert out["available"] is True
    assert int(out["graph"]["n_hops"]) == 2
    ids = {str(node.get("id") or "") for node in out["graph"]["nodes"]}
    assert "https://openalex.org/W4" in ids
    assert int(out["summary"]["n_hops_requested"]) == 2


def test_citation_network_one_hop_excludes_second_hop_nodes(monkeypatch) -> None:
    _install_openalex_stubs(monkeypatch)
    ref = PaperRef(paper_id="p1", path="papers/p1.pdf", name="p1.pdf")
    out = citation_network_for_paper(ref, max_references=5, max_citing=5, n_hops=1)
    ids = {str(node.get("id") or "") for node in out["graph"]["nodes"]}
    assert "https://openalex.org/W4" not in ids
    assert int(out["graph"]["n_hops"]) == 1


def test_citation_network_edge_directions_match_relations(monkeypatch) -> None:
    _install_openalex_stubs(monkeypatch)
    ref = PaperRef(paper_id="p1", path="papers/p1.pdf", name="p1.pdf")
    out = citation_network_for_paper(ref, max_references=5, max_citing=5, n_hops=2)
    edges = list((out.get("graph") or {}).get("edges") or [])
    ref_edges = [edge for edge in edges if str(edge.get("relation") or "") == "references"]
    cites_edges = [edge for edge in edges if str(edge.get("relation") or "") == "cites"]
    assert ref_edges, "Expected at least one references edge."
    assert cites_edges, "Expected at least one cites edge."
    assert any(str(edge.get("from") or "").endswith("/W1") and str(edge.get("to") or "").endswith("/W2") for edge in ref_edges)
    assert any(str(edge.get("from") or "").endswith("/W3") and str(edge.get("to") or "").endswith("/W1") for edge in cites_edges)


def _paper_with_center(monkeypatch) -> PaperRef:
    paper = SimpleNamespace(openalex={"id": "https://openalex.org/W1"})
    monkeypatch.setattr(
        "ragonometrics.services.citation_network.load_prepared",
        lambda _ref: (paper, None, None, None),
    )
    return PaperRef(paper_id="p1", path="papers/p1.pdf", name="p1.pdf")


def test_graph_cache_key_changes_with_hops() -> None:
    k1 = _graph_cache_key(
        center_work_id="W1",
        n_hops=1,
        max_references=10,
        max_citing=10,
        max_nodes=250,
        algo_version="v1",
    )
    k2 = _graph_cache_key(
        center_work_id="W1",
        n_hops=2,
        max_references=10,
        max_citing=10,
        max_nodes=250,
        algo_version="v1",
    )
    assert k1 != k2


def test_fresh_cache_hit_skips_recompute(monkeypatch) -> None:
    ref = _paper_with_center(monkeypatch)
    now = _now_utc()
    monkeypatch.setattr("ragonometrics.services.citation_network._graph_cache_disabled", lambda: False)
    monkeypatch.setattr("ragonometrics.services.citation_network._db_url", lambda: "dummy")
    monkeypatch.setattr("ragonometrics.services.citation_network._compute_and_upsert_graph", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not compute")))
    touched = {"ok": False}
    monkeypatch.setattr("ragonometrics.services.citation_network._touch_graph_cache", lambda *args, **kwargs: touched.__setitem__("ok", True))
    monkeypatch.setattr(
        "ragonometrics.services.citation_network._read_graph_cache",
        lambda *args, **kwargs: {
            "payload_json": {"available": True, "center": {"id": "W1"}, "references": [], "citing": [], "graph": {"nodes": [], "edges": [], "n_hops": 2}, "summary": {}},
            "generated_at": now - timedelta(minutes=2),
            "expires_at": now + timedelta(hours=1),
            "stale_until": now + timedelta(days=2),
            "refresh_job_id": "",
        },
    )
    out = citation_network_for_paper(ref, n_hops=2)
    assert out["cache"]["status"] == "fresh_hit"
    assert touched["ok"] is True


def test_stale_cache_hit_enqueues_refresh(monkeypatch) -> None:
    ref = _paper_with_center(monkeypatch)
    now = _now_utc()
    monkeypatch.setattr("ragonometrics.services.citation_network._graph_cache_disabled", lambda: False)
    monkeypatch.setattr("ragonometrics.services.citation_network._db_url", lambda: "dummy")
    monkeypatch.setattr("ragonometrics.services.citation_network._compute_and_upsert_graph", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not compute")))
    monkeypatch.setattr(
        "ragonometrics.services.citation_network._read_graph_cache",
        lambda *args, **kwargs: {
            "payload_json": {"available": True, "center": {"id": "W1"}, "references": [], "citing": [], "graph": {"nodes": [], "edges": [], "n_hops": 2}, "summary": {}},
            "generated_at": now - timedelta(days=2),
            "expires_at": now - timedelta(minutes=1),
            "stale_until": now + timedelta(hours=2),
            "refresh_job_id": "",
        },
    )
    monkeypatch.setattr("ragonometrics.services.citation_network._enqueue_graph_refresh_job", lambda **kwargs: True)
    out = citation_network_for_paper(ref, n_hops=2)
    assert out["cache"]["status"] == "stale_hit"
    assert out["cache"]["refresh_enqueued"] is True


def test_hard_expired_cache_recomputes(monkeypatch) -> None:
    ref = _paper_with_center(monkeypatch)
    now = _now_utc()
    monkeypatch.setattr("ragonometrics.services.citation_network._graph_cache_disabled", lambda: False)
    monkeypatch.setattr("ragonometrics.services.citation_network._db_url", lambda: "dummy")
    monkeypatch.setattr(
        "ragonometrics.services.citation_network._read_graph_cache",
        lambda *args, **kwargs: {
            "payload_json": {"available": True, "center": {"id": "W1"}, "references": [], "citing": [], "graph": {"nodes": [], "edges": [], "n_hops": 2}, "summary": {}},
            "generated_at": now - timedelta(days=10),
            "expires_at": now - timedelta(days=9),
            "stale_until": now - timedelta(days=8),
            "refresh_job_id": "",
        },
    )
    monkeypatch.setattr("ragonometrics.services.citation_network._try_advisory_lock", lambda *args, **kwargs: True)
    monkeypatch.setattr("ragonometrics.services.citation_network._release_advisory_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "ragonometrics.services.citation_network._compute_and_upsert_graph",
        lambda **kwargs: {
            "available": True,
            "center": {"id": "W1"},
            "references": [],
            "citing": [],
            "graph": {"nodes": [], "edges": [], "n_hops": 2},
            "summary": {},
            "cache": {"status": "miss_or_hard_expired", "cache_key": "k", "refresh_enqueued": False},
        },
    )
    out = citation_network_for_paper(ref, n_hops=2)
    assert out["cache"]["status"] == "miss_or_hard_expired"


def test_advisory_lock_loser_returns_stale(monkeypatch) -> None:
    ref = _paper_with_center(monkeypatch)
    now = _now_utc()
    monkeypatch.setattr("ragonometrics.services.citation_network._graph_cache_disabled", lambda: False)
    monkeypatch.setattr("ragonometrics.services.citation_network._db_url", lambda: "dummy")
    stale = {
        "payload_json": {"available": True, "center": {"id": "W1"}, "references": [], "citing": [], "graph": {"nodes": [], "edges": [], "n_hops": 2}, "summary": {}},
        "generated_at": now - timedelta(days=2),
        "expires_at": now - timedelta(days=1),
        "stale_until": now + timedelta(hours=1),
        "refresh_job_id": "job-1",
    }
    monkeypatch.setattr("ragonometrics.services.citation_network._read_graph_cache", lambda *args, **kwargs: stale)
    monkeypatch.setattr("ragonometrics.services.citation_network._try_advisory_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr("ragonometrics.services.citation_network._compute_and_upsert_graph", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not compute")))
    out = citation_network_for_paper(ref, n_hops=2)
    assert out["cache"]["status"] == "stale_hit"
