"""Tests for concurrent web cached-structured-question benchmark."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.eval.web_cache_benchmark import benchmark_web_cached_structured_questions
from ragonometrics.services.structured import normalize_question_key


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = int(status_code)
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._payload


def test_benchmark_web_cache_shared_session_uses_one_login() -> None:
    class FakeSession(requests.Session):
        login_calls = 0
        paper_calls = 0
        questions_calls = 0
        answers_calls = 0

        def request(self, method, url, json=None, timeout=None, verify=None, **kwargs):  # type: ignore[override]
            path = urlparse(url).path
            if path.endswith("/api/v1/auth/login"):
                FakeSession.login_calls += 1
                self.cookies.set("rag_session", "seed")
                return _FakeResponse(200, {"ok": True, "data": {"session_id": "s1"}})
            if path.endswith("/api/v1/papers"):
                FakeSession.paper_calls += 1
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"papers": [{"paper_id": "paper-1", "display_title": "Paper One"}], "count": 1}},
                )
            if path.endswith("/api/v1/structured/questions"):
                FakeSession.questions_calls += 1
                return _FakeResponse(
                    200,
                    {
                        "ok": True,
                        "data": {
                            "questions": [
                                {"id": "A01", "question": "What is question one?"},
                                {"id": "A02", "question": "What is question two?"},
                            ]
                        },
                    },
                )
            if path.endswith("/api/v1/structured/answers"):
                FakeSession.answers_calls += 1
                return _FakeResponse(
                    200,
                    {
                        "ok": True,
                        "data": {
                            "answers": {
                                normalize_question_key("What is question one?"): {"answer": "A1"},
                                normalize_question_key("What is question two?"): {"answer": "A2"},
                            },
                            "count": 2,
                        },
                    },
                )
            return _FakeResponse(404, {"ok": False, "error": {"message": "unknown"}})

    report = benchmark_web_cached_structured_questions(
        base_url="http://fake-web",
        identifier="admin",
        password="pass",
        users=4,
        iterations=3,
        auth_mode="shared-session",
        session_factory=FakeSession,
    )

    assert int(report["summary"]["target_iterations"]) == 12
    assert int(report["summary"]["successful_iterations"]) == 12
    assert float(report["cache_coverage"]["avg_ratio"]) == 1.0
    assert FakeSession.login_calls == 1
    assert FakeSession.paper_calls == 1
    assert FakeSession.questions_calls == 12
    assert FakeSession.answers_calls == 12


def test_benchmark_web_cache_per_user_login_calls_login_per_user() -> None:
    class FakeSession(requests.Session):
        login_calls = 0
        questions_calls = 0
        answers_calls = 0

        def request(self, method, url, json=None, timeout=None, verify=None, **kwargs):  # type: ignore[override]
            path = urlparse(url).path
            if path.endswith("/api/v1/auth/login"):
                FakeSession.login_calls += 1
                self.cookies.set("rag_session", f"s-{FakeSession.login_calls}")
                return _FakeResponse(200, {"ok": True, "data": {"session_id": "s"}})
            if path.endswith("/api/v1/structured/questions"):
                FakeSession.questions_calls += 1
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"questions": [{"id": "A01", "question": "Question one?"}]}},
                )
            if path.endswith("/api/v1/structured/answers"):
                FakeSession.answers_calls += 1
                return _FakeResponse(
                    200,
                    {"ok": True, "data": {"answers": {normalize_question_key("Question one?"): {"answer": "A1"}}, "count": 1}},
                )
            return _FakeResponse(404, {"ok": False, "error": {"message": "unknown"}})

    report = benchmark_web_cached_structured_questions(
        base_url="http://fake-web",
        identifier="admin",
        password="pass",
        users=3,
        iterations=2,
        paper_id="paper-1",
        auth_mode="per-user-login",
        session_factory=FakeSession,
    )

    assert int(report["summary"]["target_iterations"]) == 6
    assert int(report["summary"]["successful_iterations"]) == 6
    assert FakeSession.login_calls == 3
    assert FakeSession.questions_calls == 6
    assert FakeSession.answers_calls == 6

