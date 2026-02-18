"""Tests for additional web API benchmark scenarios (tabs + chat)."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.eval.web_cache_benchmark import benchmark_web_chat_turns, benchmark_web_tabs
from ragonometrics.services.structured import normalize_question_key


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = int(status_code)
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._payload


def test_benchmark_web_tabs_success() -> None:
    class FakeSession(requests.Session):
        chat_calls = 0
        structured_calls = 0

        def request(self, method, url, json=None, headers=None, timeout=None, verify=None, **kwargs):  # type: ignore[override]
            path = urlparse(url).path
            if path.endswith("/api/v1/auth/login"):
                self.cookies.set("rag_session", "seed")
                self.cookies.set("rag_csrf", "csrf-1")
                return _FakeResponse(200, {"ok": True, "data": {"session_id": "s1", "csrf_token": "csrf-1"}})
            if path.endswith("/api/v1/papers"):
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"papers": [{"paper_id": "paper-1", "display_title": "Paper One"}], "count": 1}},
                )
            if path.endswith("/api/v1/chat/suggestions"):
                FakeSession.chat_calls += 1
                return _FakeResponse(200, {"ok": True, "data": {"questions": ["Q1", "Q2"]}})
            if path.endswith("/api/v1/chat/history"):
                return _FakeResponse(200, {"ok": True, "data": {"rows": [], "count": 0}})
            if path.endswith("/api/v1/structured/questions"):
                FakeSession.structured_calls += 1
                return _FakeResponse(200, {"ok": True, "data": {"questions": [{"id": "A01", "question": "Question one?"}]}})
            if path.endswith("/api/v1/structured/answers"):
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"answers": {normalize_question_key("Question one?"): {"answer": "A"}}, "count": 1}},
                )
            return _FakeResponse(404, {"ok": False, "error": {"message": "unknown"}})

    report = benchmark_web_tabs(
        base_url="http://fake-web",
        identifier="admin",
        password="pass",
        users=1,
        iterations=2,
        auth_mode="shared-session",
        include_chat=True,
        include_structured=True,
        include_openalex=False,
        include_network=False,
        include_usage=False,
        session_factory=FakeSession,
    )

    assert int(report["summary"]["target_iterations"]) == 2
    assert int(report["summary"]["successful_iterations"]) == 2
    endpoints = report["endpoints"]
    assert int(endpoints["chat.suggestions"]["count"]) == 2
    assert int(endpoints["structured.questions"]["count"]) == 2
    assert FakeSession.chat_calls == 2
    assert FakeSession.structured_calls == 2


def test_benchmark_web_chat_turns_collects_cache_ratio() -> None:
    class FakeSession(requests.Session):
        chat_calls = 0

        def request(self, method, url, json=None, headers=None, timeout=None, verify=None, **kwargs):  # type: ignore[override]
            path = urlparse(url).path
            if path.endswith("/api/v1/auth/login"):
                self.cookies.set("rag_session", "seed")
                self.cookies.set("rag_csrf", "csrf-1")
                return _FakeResponse(200, {"ok": True, "data": {"session_id": "s1", "csrf_token": "csrf-1"}})
            if path.endswith("/api/v1/papers"):
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"papers": [{"paper_id": "paper-1", "display_title": "Paper One"}], "count": 1}},
                )
            if path.endswith("/api/v1/chat/turn"):
                FakeSession.chat_calls += 1
                hit = FakeSession.chat_calls in {1, 3}
                layer = "strict" if FakeSession.chat_calls == 1 else ("fallback" if FakeSession.chat_calls == 3 else "none")
                return _FakeResponse(
                    200,
                    {
                        "ok": True,
                        "data": {
                            "answer": "A",
                            "cache_hit": hit,
                            "cache_hit_layer": layer,
                            "model": "gpt-5-nano",
                        },
                    },
                )
            return _FakeResponse(404, {"ok": False, "error": {"message": "unknown"}})

    report = benchmark_web_chat_turns(
        base_url="http://fake-web",
        identifier="admin",
        password="pass",
        users=1,
        iterations=3,
        auth_mode="shared-session",
        question="What is this paper about?",
        session_factory=FakeSession,
    )

    assert int(report["summary"]["target_iterations"]) == 3
    assert int(report["summary"]["successful_iterations"]) == 3
    cache = report["chat_cache"]
    assert float(cache["cache_hit_ratio"]) == (2.0 / 3.0)
    assert int(cache["hit_count"]) == 2
    layers = cache["layer_counts"]
    assert int(layers["strict"]) == 1
    assert int(layers["fallback"]) == 1
