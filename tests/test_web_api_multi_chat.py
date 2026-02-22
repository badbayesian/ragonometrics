"""Flask web API tests for multi-paper chat endpoints."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.db.connection import pooled_connection
from ragonometrics.services import auth as auth_service
from ragonometrics.web.app import create_app


def _set_test_papers_dir(monkeypatch, *, paper_count: int = 3) -> list[Path]:
    papers_dir = Path(tempfile.mkdtemp(prefix="ragonometrics-webapi-multi-papers-"))
    created: list[Path] = []
    for idx in range(max(1, int(paper_count))):
        path = papers_dir / f"paper_{idx + 1}.pdf"
        path.write_bytes(b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
        created.append(path)
    monkeypatch.setenv("PAPERS_DIR", str(papers_dir))
    return created


def _insert_user(username: str, password: str) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM auth.streamlit_users WHERE lower(username) = lower(%s)", (username,))
        cur.execute(
            """
            INSERT INTO auth.streamlit_users (username, email, password_hash, is_active, updated_at)
            VALUES (%s, %s, %s, TRUE, CURRENT_TIMESTAMP)
            """,
            (username, f"{username}@example.com", auth_service.password_hash(password)),
        )
        conn.commit()


def _login(client, username: str = "admin", password: str = "pass123") -> str:
    out = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert out.status_code == 200
    payload = out.get_json()
    assert payload["ok"] is True
    return str(payload["data"]["csrf_token"])


def test_multi_chat_standard_questions_and_network_routes(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    _set_test_papers_dir(monkeypatch, paper_count=3)
    _insert_user("admin", "pass123")
    monkeypatch.setattr(
        "ragonometrics.web.api.multi_paper_chat_service.suggested_multi_paper_questions",
        lambda: ["Q1", "Q2"],
    )
    monkeypatch.setattr(
        "ragonometrics.web.api.multi_paper_chat_service.selected_paper_interaction_graph",
        lambda **kwargs: {
            "nodes": [{"id": "p1", "paper_id": "p1", "label": "Paper 1"}, {"id": "p2", "paper_id": "p2", "label": "Paper 2"}],
            "edges": [{"from": "p1", "to": "p2", "type": "topic_overlap", "weight": 0.8}],
            "summary": {"edge_count": 1},
            "legend": {},
            "warnings": [],
        },
    )

    app = create_app()
    with app.test_client() as client:
        csrf = _login(client)
        out = client.get("/api/v1/chat/multi/standard-questions")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        assert payload["data"]["count"] == 2

        papers = client.get("/api/v1/papers").get_json()["data"]["papers"]
        ids = [papers[0]["paper_id"], papers[1]["paper_id"]]
        network = client.post(
            "/api/v1/chat/multi/network",
            headers={"X-CSRF-Token": csrf},
            json={"paper_ids": ids},
        )
        assert network.status_code == 200
        n_payload = network.get_json()
        assert n_payload["ok"] is True
        assert len(n_payload["data"]["nodes"]) == 2
        assert len(n_payload["data"]["edges"]) == 1


def test_multi_chat_turn_and_history_routes(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    _set_test_papers_dir(monkeypatch, paper_count=3)
    _insert_user("admin", "pass123")
    monkeypatch.setattr(
        "ragonometrics.web.api.multi_paper_chat_service.multi_chat_turn",
        lambda **kwargs: {
            "conversation_id": "conv-1",
            "answer": "## Short Answer\n- Synthesized answer",
            "model": "gpt-5-nano",
            "request_id": str(kwargs.get("request_id") or "req-1"),
            "scope": {"mode": "multi", "paper_ids": list(kwargs.get("paper_ids") or []), "seed_paper_id": "seed", "paper_count": len(list(kwargs.get("paper_ids") or []))},
            "paper_answers": [
                {"paper_id": "p1", "paper_path": "/app/papers/p1.pdf", "answer": "A1"},
                {"paper_id": "p2", "paper_path": "/app/papers/p2.pdf", "answer": "A2"},
            ],
            "comparison_summary": {"consensus_points": ["c1"], "disagreement_points": [], "methods_contrasts": [], "evidence_gaps": []},
            "aggregate_provenance": {"score": 0.7, "status": "medium", "per_paper": [], "coverage": {}},
            "suggested_followups": ["Next?"],
            "suggested_papers": {"project": [], "external": []},
        },
    )
    monkeypatch.setattr("ragonometrics.web.api.multi_paper_chat_service.ensure_conversation", lambda *args, **kwargs: "conv-1")
    monkeypatch.setattr("ragonometrics.web.api.multi_paper_chat_service.append_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "ragonometrics.web.api.multi_paper_chat_service.list_turns",
        lambda *args, **kwargs: {
            "conversation_id": "conv-1",
            "rows": [{"query": "Q", "answer": "A", "paper_ids": ["p1", "p2"], "created_at": "2026-02-22T00:00:00+00:00"}],
            "count": 1,
        },
    )
    monkeypatch.setattr(
        "ragonometrics.web.api.multi_paper_chat_service.clear_turns",
        lambda *args, **kwargs: {"conversation_id": "conv-1", "deleted_count": 1},
    )

    app = create_app()
    with app.test_client() as client:
        csrf = _login(client)
        papers = client.get("/api/v1/papers").get_json()["data"]["papers"]
        ids = [papers[0]["paper_id"], papers[1]["paper_id"]]

        turn = client.post(
            "/api/v1/chat/multi/turn",
            headers={"X-CSRF-Token": csrf},
            json={"paper_ids": ids, "question": "What do these papers disagree on?"},
        )
        assert turn.status_code == 200
        payload = turn.get_json()
        assert payload["ok"] is True
        assert payload["data"]["conversation_id"] == "conv-1"
        assert payload["data"]["scope"]["paper_count"] == 2

        history = client.get(
            "/api/v1/chat/multi/history?" + "&".join([f"paper_ids={pid}" for pid in ids])
        )
        assert history.status_code == 200
        h_payload = history.get_json()
        assert h_payload["ok"] is True
        assert h_payload["data"]["count"] == 1

        cleared = client.delete("/api/v1/chat/multi/history?conversation_id=conv-1", headers={"X-CSRF-Token": csrf})
        assert cleared.status_code == 200
        c_payload = cleared.get_json()
        assert c_payload["ok"] is True
        assert c_payload["data"]["deleted_count"] == 1
