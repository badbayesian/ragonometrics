"""Tests for async queue OpenAlex citation graph refresh job."""

from __future__ import annotations

import pytest

from ragonometrics.integrations import rq_queue


def test_execute_job_openalex_network_refresh_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "ragonometrics.services.citation_network.refresh_cached_citation_graph",
        lambda payload, db_url=None: {
            "cache_key": str(payload.get("cache_key") or ""),
            "available": True,
            "db_url": db_url,
        },
    )
    job = {
        "job_type": "openalex_network_refresh",
        "payload_json": {
            "cache_key": "k1",
            "center_work_id": "W1",
            "n_hops": 2,
            "max_references": 10,
            "max_citing": 10,
            "max_nodes": 250,
            "algo_version": "v1",
        },
    }
    out = rq_queue._execute_job(job, default_meta_db_url="dummy")
    assert out["cache_key"] == "k1"
    assert out["available"] is True
    assert out["db_url"] == "dummy"


def test_execute_job_openalex_network_refresh_failure_marks_cache(monkeypatch) -> None:
    marked = {"cache_key": ""}

    def _raise(payload, db_url=None):  # noqa: ARG001
        raise RuntimeError("refresh failed")

    def _mark(cache_key, db_url=None):  # noqa: ARG001
        marked["cache_key"] = str(cache_key or "")

    monkeypatch.setattr("ragonometrics.services.citation_network.refresh_cached_citation_graph", _raise)
    monkeypatch.setattr("ragonometrics.services.citation_network.mark_cached_citation_refresh_failure", _mark)
    job = {
        "job_type": "openalex_network_refresh",
        "payload_json": {"cache_key": "k-fail"},
    }
    with pytest.raises(RuntimeError):
        rq_queue._execute_job(job, default_meta_db_url="dummy")
    assert marked["cache_key"] == "k-fail"
