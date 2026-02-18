"""Service-level tests for new web migration helpers."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.services import auth as auth_service
from ragonometrics.services import cache_inspector as cache_inspector_service
from ragonometrics.services import chat as chat_service
from ragonometrics.services import chat_history as chat_history_service
from ragonometrics.services import papers as papers_service
from ragonometrics.services import provenance as provenance_service
from ragonometrics.services import structured as structured_service


def test_password_hash_verify_and_legacy_fallback() -> None:
    hashed = auth_service.password_hash("secret123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert auth_service.password_verify("secret123", hashed) is True
    assert auth_service.password_verify("bad", hashed) is False
    # Legacy plaintext fallback compatibility.
    assert auth_service.password_verify("legacy", "legacy") is True
    assert auth_service.password_verify("legacy", "nope") is False


def test_paper_id_is_deterministic_and_path_normalized() -> None:
    p1 = "C:\\repo\\papers\\A.pdf"
    p2 = "C:/repo/papers/A.pdf"
    assert papers_service.normalize_paper_path(p1) == papers_service.normalize_paper_path(p2)
    assert papers_service.paper_id_for_path(p1) == papers_service.paper_id_for_path(p2)


def test_stream_chat_turn_emits_delta_before_completion(monkeypatch) -> None:
    ref = papers_service.PaperRef(paper_id="paper1", path="papers/A.pdf", name="A.pdf")

    def _fake_load_prepared(_):
        paper = SimpleNamespace(path=ref.path, openalex=None, citec=None)
        settings = SimpleNamespace(chat_model="gpt-5-nano", top_k=10)
        return paper, [{"text": "ctx"}], [[0.1, 0.2, 0.3]], settings

    def _fake_top_k_context(*args, **kwargs):
        return "(page 1 words 0-10)\nctx", {"method": "local", "top_k": 1}

    def _fake_stream_llm_answer(**kwargs):
        on_delta = kwargs["on_delta"]
        on_delta("partial")
        time.sleep(0.35)
        on_delta("partial complete")
        return "partial complete"

    monkeypatch.setattr(chat_service, "load_prepared", _fake_load_prepared)
    monkeypatch.setattr(chat_service, "build_llm_runtime", lambda settings: object())
    monkeypatch.setattr(chat_service, "top_k_context", _fake_top_k_context)
    monkeypatch.setattr(chat_service, "stream_llm_answer", _fake_stream_llm_answer)
    monkeypatch.setattr(chat_service, "get_cached_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_service,
        "get_cached_answer_by_normalized_query",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(chat_service, "set_cached_answer", lambda *args, **kwargs: None)

    stream = chat_service.stream_chat_turn(paper_ref=ref, query="test", model="gpt-5-nano")
    t0 = time.perf_counter()
    first_row = next(stream)
    first_elapsed = time.perf_counter() - t0

    first_payload = json.loads(first_row)
    assert first_payload["event"] == "delta"
    assert first_elapsed < 0.2

    rest_payloads = [json.loads(row) for row in stream]
    assert any(row.get("event") == "done" for row in rest_payloads)


def test_structured_pdf_handles_long_unbroken_tokens() -> None:
    long_token = "X" * 2500
    bundle = {
        "paper": {
            "title": f"LongToken-{long_token}",
            "author": "Author",
            "path": f"/app/papers/{long_token}.pdf",
        },
        "summary": {"total_questions": 1, "cached_questions": 1, "uncached_questions": 0},
        "model_scope": {"selected_model": "gpt-5-nano", "cache_scope": "Selected model only"},
        "export_format": "full",
        "questions": [
            {
                "id": "A01",
                "category": "A",
                "question": f"Q-{long_token}",
                "answer": long_token,
                "cached": True,
                "source": "workflow.run_records",
                "model": "gpt-5-nano",
                "cached_at": "2026-01-01T00:00:00Z",
                "structured_fields": {
                    "confidence": "high",
                    "confidence_score": 0.9,
                    "retrieval_method": "local",
                    "evidence_type": "retrieved_context",
                    "citation_anchors": [],
                    "quote_snippet": long_token,
                },
            }
        ],
    }
    out = structured_service.structured_workstream_pdf_bytes(bundle)
    assert isinstance(out, (bytes, bytearray))
    assert len(out) > 100


def test_suggested_paper_questions_title_injection() -> None:
    questions = chat_service.suggested_paper_questions(paper_title="Example Paper")
    assert len(questions) == 6
    assert "Example Paper" in questions[0]


def test_chat_history_service_append_list_clear() -> None:
    db_url = "dummy"
    chat_history_service.append_turn(
        db_url,
        user_id=1,
        username="alice",
        session_id="sess-1",
        paper_id="paper-1",
        paper_path="/app/papers/paper.pdf",
        model="gpt-5-nano",
        variation_mode=False,
        query="What is the contribution?",
        answer="The contribution is...",
        citations=[{"page": 1}],
        retrieval_stats={"method": "local"},
        cache_hit=True,
        request_id="req-1",
    )
    rows = chat_history_service.list_turns(db_url, user_id=1, username="alice", paper_id="paper-1", limit=20)
    assert len(rows) >= 1
    assert rows[-1]["query"] == "What is the contribution?"
    deleted = chat_history_service.clear_turns(db_url, user_id=1, username="alice", paper_id="paper-1")
    assert deleted >= 1


def test_chat_turn_ignores_invalid_cached_answer_and_regenerates(monkeypatch) -> None:
    ref = papers_service.PaperRef(paper_id="paper1", path="papers/A.pdf", name="A.pdf")

    def _fake_load_prepared(_):
        paper = SimpleNamespace(path=ref.path, openalex=None, citec=None)
        settings = SimpleNamespace(chat_model="gpt-5-nano", top_k=10)
        return paper, [{"text": "ctx"}], [[0.1, 0.2, 0.3]], settings

    def _fake_top_k_context(*args, **kwargs):
        return "(page 1 words 0-10)\nctx", {"method": "local", "top_k": 1}

    invalid_cached = "ResponseTextConfig(format=ResponseFormatText(type='text'), verbosity='medium')"
    saved: dict[str, str] = {}

    def _fake_set_cached_answer(*args, **kwargs):
        saved["answer"] = str(kwargs.get("answer") or "")

    monkeypatch.setattr(chat_service, "load_prepared", _fake_load_prepared)
    monkeypatch.setattr(chat_service, "build_llm_runtime", lambda settings: object())
    monkeypatch.setattr(chat_service, "top_k_context", _fake_top_k_context)
    monkeypatch.setattr(chat_service, "get_cached_answer", lambda *args, **kwargs: invalid_cached)
    monkeypatch.setattr(
        chat_service,
        "get_cached_answer_by_normalized_query",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(chat_service, "call_llm", lambda *args, **kwargs: "Main caveat is small-sample sensitivity.")
    monkeypatch.setattr(chat_service, "set_cached_answer", _fake_set_cached_answer)

    out = chat_service.chat_turn(paper_ref=ref, query="what are limitations", model="gpt-5-nano")
    assert out["cache_hit"] is False
    assert out["cache_miss_reason"] == "invalid_strict_cached_answer"
    assert str(out["answer"]).startswith("Main caveat is")
    assert str(saved.get("answer") or "").startswith("Main caveat is")


def test_provenance_score_flags_missing_citations(monkeypatch) -> None:
    ref = papers_service.PaperRef(paper_id="paper1", path="papers/A.pdf", name="A.pdf")

    def _fake_load_prepared(_):
        paper = SimpleNamespace(path=ref.path, title="A", openalex=None, citec=None)
        chunks = [{"page": 1, "start_word": 0, "end_word": 20, "text": "alpha beta gamma delta"}]
        return paper, chunks, [[0.1, 0.2, 0.3]], SimpleNamespace(chat_model="gpt-5-nano", top_k=10)

    monkeypatch.setattr(provenance_service, "load_prepared", _fake_load_prepared)
    out = provenance_service.score_answer_provenance(
        paper_ref=ref,
        question="What is the question?",
        answer="This answer has no evidence.",
        citations=[],
    )
    assert out["status"] in {"low", "medium", "high"}
    warnings = out.get("warnings") or []
    codes = {str(item.get("code") or "") for item in warnings if isinstance(item, dict)}
    assert "no_citations" in codes


def test_cache_inspector_structured_ratio(monkeypatch) -> None:
    ref = papers_service.PaperRef(paper_id="paper1", path="papers/A.pdf", name="A.pdf")
    monkeypatch.setattr(
        cache_inspector_service,
        "structured_report_questions",
        lambda: [
            {"id": "A01", "category": "A", "question": "Q1"},
            {"id": "A02", "category": "A", "question": "Q2"},
        ],
    )
    monkeypatch.setattr(
        cache_inspector_service,
        "db_workflow_structured_answers_for_paper",
        lambda *args, **kwargs: {cache_inspector_service.normalize_question_key("Q1"): {"answer": "A1", "run_id": "r1"}},
    )
    monkeypatch.setattr(cache_inspector_service, "db_workflow_question_records_for_paper", lambda *args, **kwargs: {})
    out = cache_inspector_service.inspect_structured_cache(paper_ref=ref, model="gpt-5-nano")
    assert out["total_questions"] == 2
    assert out["cached_questions"] == 1
    assert float(out["coverage_ratio"]) == 0.5
