"""Flask web API tests for auth, scoping, and structured exports."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.db.connection import pooled_connection
from ragonometrics.services import auth as auth_service
from ragonometrics.web.app import create_app


def _insert_user(username: str, password: str, email: str | None = None) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM auth.streamlit_users WHERE lower(username) = lower(%s)", (username,))
        cur.execute(
            """
            INSERT INTO auth.streamlit_users (username, email, password_hash, is_active, updated_at)
            VALUES (%s, %s, %s, TRUE, CURRENT_TIMESTAMP)
            """,
            (username, email, auth_service.password_hash(password)),
        )
        conn.commit()


def _login(client, username: str = "admin", password: str = "pass123"):
    out = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    payload = out.get_json()
    assert out.status_code == 200
    assert payload["ok"] is True
    return payload["data"]["csrf_token"]


def _insert_workflow_run(
    *,
    run_id: str,
    papers_dir: str,
    status: str = "completed",
    started_at: str = "2026-02-17T00:00:00+00:00",
    finished_at: str = "2026-02-17T00:10:00+00:00",
) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM workflow.run_records WHERE run_id = %s", (run_id,))
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status, papers_dir,
                started_at, finished_at, created_at, updated_at, payload_json, metadata_json
            )
            VALUES
            (
                %s, 'run', '', 'main', %s, %s,
                %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s::jsonb, %s::jsonb
            )
            """,
            (
                run_id,
                status,
                papers_dir,
                started_at,
                finished_at,
                json.dumps({"source": "test"}, ensure_ascii=False),
                json.dumps({"source": "test"}, ensure_ascii=False),
            ),
        )
        conn.commit()


def _insert_workflow_step(
    *,
    run_id: str,
    step: str,
    status: str = "completed",
    output: dict | None = None,
    metadata: dict | None = None,
    reuse_source_run_id: str | None = None,
    reuse_source_record_key: str | None = None,
) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status,
                started_at, finished_at, created_at, updated_at,
                output_json, metadata_json, reuse_source_run_id, reuse_source_record_key
            )
            VALUES
            (
                %s, 'step', %s, 'main', %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                %s::jsonb, %s::jsonb, %s, %s
            )
            ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                status = EXCLUDED.status,
                output_json = EXCLUDED.output_json,
                metadata_json = EXCLUDED.metadata_json,
                reuse_source_run_id = COALESCE(EXCLUDED.reuse_source_run_id, workflow.run_records.reuse_source_run_id),
                reuse_source_record_key = COALESCE(EXCLUDED.reuse_source_record_key, workflow.run_records.reuse_source_record_key),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                run_id,
                step,
                status,
                json.dumps(output or {}, ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
                reuse_source_run_id,
                reuse_source_record_key,
            ),
        )
        conn.commit()


def _insert_workflow_question(
    *,
    run_id: str,
    question_id: str,
    payload: dict,
    status: str = "high",
) -> None:
    db_url = os.environ.get("DATABASE_URL", "dummy")
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO workflow.run_records
            (
                run_id, record_kind, step, record_key, status, question_id,
                created_at, updated_at, payload_json, output_json, metadata_json
            )
            VALUES
            (
                %s, 'question', 'agentic', %s, %s, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s::jsonb, '{}'::jsonb, '{}'::jsonb
            )
            ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                status = EXCLUDED.status,
                payload_json = EXCLUDED.payload_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                run_id,
                question_id,
                status,
                question_id,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.commit()


def test_auth_login_me_logout_flow(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    _insert_user("admin", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client)
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.get_json()["ok"] is True
        assert str((me.get_json().get("data") or {}).get("csrf_token") or "").strip()
        out = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert out.status_code == 200
        me2 = client.get("/api/v1/auth/me")
        assert me2.status_code == 401


def test_auth_login_by_email_and_forgot_reset_flow(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("WEB_PASSWORD_RESET_DEBUG", "1")
    _insert_user("email_user", "pass123", email="email_user@example.com")
    app = create_app()
    with app.test_client() as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"identifier": "email_user@example.com", "password": "pass123"},
        )
        assert login.status_code == 200
        payload = login.get_json()
        assert payload["ok"] is True

        forgot = client.post("/api/v1/auth/forgot-password", json={"identifier": "email_user@example.com"})
        assert forgot.status_code == 200
        forgot_payload = forgot.get_json()
        assert forgot_payload["ok"] is True
        assert forgot_payload["data"]["accepted"] is True
        token = str(forgot_payload["data"].get("debug_reset_token") or "").strip()
        assert token

        reset = client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpass456"})
        assert reset.status_code == 200
        reset_payload = reset.get_json()
        assert reset_payload["ok"] is True
        assert reset_payload["data"]["reset"] is True

        relogin = client.post(
            "/api/v1/auth/login",
            json={"identifier": "email_user", "password": "newpass456"},
        )
        assert relogin.status_code == 200
        relogin_payload = relogin.get_json()
        assert relogin_payload["ok"] is True


def test_login_rate_limit(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("WEB_LOGIN_RATE_LIMIT", "1")
    monkeypatch.setenv("WEB_LOGIN_RATE_WINDOW_SECONDS", "3600")
    _insert_user("limited", "pass123")
    app = create_app()
    with app.test_client() as client:
        first = client.post("/api/v1/auth/login", json={"username": "limited", "password": "pass123"})
        assert first.status_code == 200
        second = client.post("/api/v1/auth/login", json={"username": "limited", "password": "pass123"})
        assert second.status_code == 429


def test_paper_scope_and_structured_export(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("user1", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="user1", password="pass123")
        papers = client.get("/api/v1/papers")
        assert papers.status_code == 200
        payload = papers.get_json()
        assert payload["ok"] is True
        assert payload["data"]["count"] > 0
        paper_id = payload["data"]["papers"][0]["paper_id"]

        bad_chat = client.post(
            "/api/v1/chat/turn",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": "invalid", "question": "test question"},
        )
        assert bad_chat.status_code == 404

        def _fake_export_bundle_for_paper(**kwargs):
            return {
                "export_type": "structured_workstream",
                "export_format": "full",
                "summary": {"total_questions": 1, "cached_questions": 1, "uncached_questions": 0},
                "questions": [{"id": "A01", "question": "Q", "answer": "A", "cached": True}],
            }

        monkeypatch.setattr("ragonometrics.web.api.structured_service.export_bundle_for_paper", _fake_export_bundle_for_paper)
        export_json = client.post(
            "/api/v1/structured/export",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "export_format": "full", "output": "json"},
        )
        assert export_json.status_code == 200
        export_payload = export_json.get_json()
        assert export_payload["ok"] is True
        assert export_payload["data"]["export_format"] == "full"

        monkeypatch.setattr("ragonometrics.web.api.structured_service.structured_workstream_pdf_bytes", lambda bundle: b"pdf")
        export_pdf = client.post(
            "/api/v1/structured/export",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "export_format": "compact", "output": "pdf"},
        )
        assert export_pdf.status_code == 200
        assert export_pdf.content_type.startswith("application/pdf")


def test_chat_stream_emits_ndjson_and_error_event(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("streamer", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="streamer", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        def _fake_stream_chat_turn(**kwargs):
            yield json.dumps({"event": "delta", "text": "hello"}, ensure_ascii=False) + "\n"
            raise RuntimeError("upstream exploded")

        monkeypatch.setattr("ragonometrics.web.api.chat_service.stream_chat_turn", _fake_stream_chat_turn)
        out = client.post(
            "/api/v1/chat/turn-stream",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "question": "test stream"},
        )

        assert out.status_code == 200
        assert out.content_type.startswith("application/x-ndjson")
        lines = [ln for ln in out.get_data(as_text=True).splitlines() if ln.strip()]
        assert len(lines) >= 2
        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert first["event"] == "delta"
        assert last["event"] == "error"
        assert last["code"] == "chat_failed"
        assert "upstream exploded" in str(last["message"])


def test_structured_export_pdf_render_failure_returns_typed_error(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("pdferr", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="pdferr", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        def _fake_export_bundle_for_paper(**kwargs):
            return {
                "export_type": "structured_workstream",
                "export_format": "compact",
                "summary": {"total_questions": 1, "cached_questions": 1, "uncached_questions": 0},
                "questions": [{"id": "A01", "question": "Q", "answer": "A", "cached": True}],
            }

        monkeypatch.setattr("ragonometrics.web.api.structured_service.export_bundle_for_paper", _fake_export_bundle_for_paper)

        def _raise_pdf(_bundle):
            raise RuntimeError("Not enough horizontal space to render a single character")

        monkeypatch.setattr("ragonometrics.web.api.structured_service.structured_workstream_pdf_bytes", _raise_pdf)
        export_pdf = client.post(
            "/api/v1/structured/export",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "export_format": "compact", "output": "pdf"},
        )
        assert export_pdf.status_code == 500
        payload = export_pdf.get_json()
        assert payload["ok"] is False
        assert payload["error"]["code"] == "pdf_render_failed"


def test_paper_overview_and_notes_crud(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("notesuser", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="notesuser", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        overview = client.get(f"/api/v1/papers/{paper_id}/overview")
        assert overview.status_code == 200
        overview_payload = overview.get_json()
        assert overview_payload["ok"] is True
        assert str(overview_payload["data"]["paper_id"]) == paper_id

        notes_empty = client.get(f"/api/v1/papers/{paper_id}/notes")
        assert notes_empty.status_code == 200
        notes_payload = notes_empty.get_json()
        assert notes_payload["ok"] is True
        assert notes_payload["data"]["count"] == 0

        created = client.post(
            f"/api/v1/papers/{paper_id}/notes",
            headers={"X-CSRF-Token": csrf},
            json={
                "paper_id": paper_id,
                "note_text": "Important section on identification",
                "page_number": 3,
                "highlight_text": "identification",
            },
        )
        assert created.status_code == 201
        created_payload = created.get_json()
        assert created_payload["ok"] is True
        note_id = int(created_payload["data"]["id"])

        patched = client.patch(
            f"/api/v1/papers/{paper_id}/notes/{note_id}",
            headers={"X-CSRF-Token": csrf},
            json={"note_text": "Updated note"},
        )
        assert patched.status_code == 200
        patched_payload = patched.get_json()
        assert patched_payload["ok"] is True
        assert patched_payload["data"]["note_text"] == "Updated note"

        deleted = client.delete(
            f"/api/v1/papers/{paper_id}/notes/{note_id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 200
        deleted_payload = deleted.get_json()
        assert deleted_payload["ok"] is True
        assert deleted_payload["data"]["deleted"] is True


def test_openalex_metadata_and_network_routes(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("oauser", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="oauser", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        monkeypatch.setattr(
            "ragonometrics.web.api.openalex_metadata_service.metadata_for_paper",
            lambda _ref: {"available": True, "work": {"id": "https://openalex.org/W1", "title": "T"}},
        )
        meta_out = client.get(f"/api/v1/openalex/metadata?paper_id={paper_id}")
        assert meta_out.status_code == 200
        meta_payload = meta_out.get_json()
        assert meta_payload["ok"] is True
        assert meta_payload["data"]["available"] is True

        monkeypatch.setattr(
            "ragonometrics.web.api.citation_network_service.citation_network_for_paper",
            lambda _ref, max_references=10, max_citing=10: {
                "available": True,
                "center": {"id": "W1"},
                "references": [{"id": "W2"}],
                "citing": [{"id": "W3"}],
                "summary": {"references_shown": 1, "citing_shown": 1},
            },
        )
        net_out = client.get(f"/api/v1/openalex/citation-network?paper_id={paper_id}&max_references=5&max_citing=7")
        assert net_out.status_code == 200
        net_payload = net_out.get_json()
        assert net_payload["ok"] is True
        assert net_payload["data"]["available"] is True
        assert len(net_payload["data"]["references"]) == 1


def test_chat_suggestions_and_history_routes(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("histuser", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="histuser", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        suggestions = client.get(f"/api/v1/chat/suggestions?paper_id={paper_id}")
        assert suggestions.status_code == 200
        suggestions_payload = suggestions.get_json()
        assert suggestions_payload["ok"] is True
        assert len(suggestions_payload["data"]["questions"]) >= 3

        history_empty = client.get(f"/api/v1/chat/history?paper_id={paper_id}")
        assert history_empty.status_code == 200
        assert history_empty.get_json()["data"]["count"] == 0

        monkeypatch.setattr(
            "ragonometrics.web.api.chat_service.chat_turn",
            lambda **kwargs: {
                "answer": "Cached summary",
                "citations": [{"page": 1, "start_word": 0, "end_word": 10}],
                "retrieval_stats": {"method": "local"},
                "cache_hit": True,
                "request_id": kwargs.get("request_id"),
                "model": kwargs.get("model") or "gpt-5-nano",
            },
        )
        chat_out = client.post(
            "/api/v1/chat/turn",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "question": "What is this paper about?"},
        )
        assert chat_out.status_code == 200
        chat_payload = chat_out.get_json()
        assert chat_payload["ok"] is True

        history_after = client.get(f"/api/v1/chat/history?paper_id={paper_id}")
        assert history_after.status_code == 200
        history_payload = history_after.get_json()
        assert history_payload["ok"] is True
        assert history_payload["data"]["count"] == 1
        row = history_payload["data"]["rows"][0]
        assert row["query"] == "What is this paper about?"
        assert row["answer"] == "Cached summary"

        cleared = client.delete(f"/api/v1/chat/history?paper_id={paper_id}", headers={"X-CSRF-Token": csrf})
        assert cleared.status_code == 200
        clear_payload = cleared.get_json()
        assert clear_payload["ok"] is True
        assert int(clear_payload["data"]["deleted_count"]) >= 1

        history_final = client.get(f"/api/v1/chat/history?paper_id={paper_id}")
        assert history_final.status_code == 200
        assert history_final.get_json()["data"]["count"] == 0


def test_chat_stream_persists_done_turn(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("streamhist", "pass123")
    app = create_app()
    with app.test_client() as client:
        csrf = _login(client, username="streamhist", password="pass123")
        papers = client.get("/api/v1/papers")
        payload = papers.get_json()
        paper_id = payload["data"]["papers"][0]["paper_id"]

        def _fake_stream_chat_turn(**kwargs):
            yield json.dumps({"event": "delta", "text": "partial"}, ensure_ascii=False) + "\n"
            yield json.dumps(
                {
                    "event": "done",
                    "answer": "final stream answer",
                    "citations": [{"page": 2, "start_word": 11, "end_word": 31}],
                    "retrieval_stats": {"method": "bm25"},
                    "cache_hit": False,
                    "model": "gpt-5-nano",
                },
                ensure_ascii=False,
            ) + "\n"

        monkeypatch.setattr("ragonometrics.web.api.chat_service.stream_chat_turn", _fake_stream_chat_turn)
        out = client.post(
            "/api/v1/chat/turn-stream",
            headers={"X-CSRF-Token": csrf},
            json={"paper_id": paper_id, "question": "Stream question"},
        )
        assert out.status_code == 200
        _ = out.get_data(as_text=True)

        history_after = client.get(f"/api/v1/chat/history?paper_id={paper_id}")
        assert history_after.status_code == 200
        history_payload = history_after.get_json()
        assert history_payload["ok"] is True
        assert history_payload["data"]["count"] == 1
        row = history_payload["data"]["rows"][0]
        assert row["query"] == "Stream question"
        assert row["answer"] == "final stream answer"


def test_workflow_runs_lists_only_paper_scoped_runs(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfuser", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfuser", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        paper_path = str(first["path"])
        paper_dir = str(Path(paper_path).parent).replace("\\", "/")

        _insert_workflow_run(run_id="run-paper-path", papers_dir=paper_path)
        _insert_workflow_run(run_id="run-paper-dir", papers_dir=paper_dir)
        _insert_workflow_run(run_id="run-other", papers_dir="/tmp/other-paper.pdf")

        out = client.get(f"/api/v1/workflow/runs?paper_id={paper_id}&limit=100")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        run_ids = {str(item.get("run_id") or "") for item in payload["data"]["runs"]}
        assert "run-paper-path" in run_ids
        assert "run-paper-dir" in run_ids
        assert "run-other" not in run_ids


def test_workflow_runs_auto_selects_latest_run(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfauto", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfauto", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        paper_id = str(papers["data"]["papers"][0]["paper_id"])
        paper_path = str(papers["data"]["papers"][0]["path"])
        _insert_workflow_run(
            run_id="run-older",
            papers_dir=paper_path,
            started_at="2026-02-16T00:00:00+00:00",
            finished_at="2026-02-16T00:10:00+00:00",
        )
        _insert_workflow_run(
            run_id="run-newer",
            papers_dir=paper_path,
            started_at="2026-02-18T00:00:00+00:00",
            finished_at="2026-02-18T00:10:00+00:00",
        )

        out = client.get(f"/api/v1/workflow/runs?paper_id={paper_id}&limit=10")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        assert payload["data"]["selected_run_id"] == "run-newer"


def test_workflow_steps_returns_top_level_steps_for_run(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfsteps", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfsteps", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        paper_path = str(first["path"])
        run_id = "run-steps-main"
        _insert_workflow_run(run_id=run_id, papers_dir=paper_path)
        _insert_workflow_step(run_id=run_id, step="prep", output={"status": "completed"})
        _insert_workflow_step(run_id=run_id, step="agentic", output={"status": "completed"})
        _insert_workflow_step(run_id=run_id, step="report", output={"status": "completed"})

        out = client.get(f"/api/v1/workflow/runs/{run_id}/steps?paper_id={paper_id}&include_internals=1")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        steps = payload["data"]["steps"]
        step_names = [str(item.get("step") or "") for item in steps]
        assert "prep" in step_names
        assert "agentic" in step_names
        assert "report" in step_names


def test_workflow_steps_rejects_scope_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfscope", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfscope", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        _insert_workflow_run(run_id="run-mismatch", papers_dir="/tmp/not-this-paper.pdf")

        out = client.get(f"/api/v1/workflow/runs/run-mismatch/steps?paper_id={paper_id}")
        assert out.status_code == 404
        payload = out.get_json()
        assert payload["ok"] is False
        assert payload["error"]["code"] == "workflow_run_scope_mismatch"


def test_workflow_steps_derives_agentic_internals_from_cached_payload(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfinternal", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfinternal", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        paper_path = str(first["path"])
        run_id = "run-internals"
        _insert_workflow_run(run_id=run_id, papers_dir=paper_path)
        _insert_workflow_step(
            run_id=run_id,
            step="agentic",
            output={
                "status": "completed",
                "subquestions": ["q1", "q2"],
                "sub_answers": [{"question": "q1", "answer": "a1"}],
                "final_answer": "final",
                "citations_preview": [{"id": "c1"}],
                "citations_enabled": True,
            },
        )
        _insert_workflow_question(
            run_id=run_id,
            question_id="A01",
            payload={"id": "A01", "question": "Q", "answer": "A", "confidence": "high"},
            status="high",
        )

        out = client.get(f"/api/v1/workflow/runs/{run_id}/steps?paper_id={paper_id}&include_internals=1")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        internals = payload["data"]["internals"]
        internal_ids = {str(item.get("internal_step") or "") for item in internals}
        assert "agentic_plan" in internal_ids
        assert "agentic_subquestion_answer" in internal_ids
        assert "agentic_report_question_answer" in internal_ids
        assert "agentic_synthesis" in internal_ids
        assert "agentic_citations" in internal_ids


def test_workflow_steps_handles_missing_agentic_details_gracefully(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfmissing", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfmissing", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        paper_path = str(first["path"])
        run_id = "run-missing-agentic"
        _insert_workflow_run(run_id=run_id, papers_dir=paper_path)
        _insert_workflow_step(run_id=run_id, step="agentic", output={})

        out = client.get(f"/api/v1/workflow/runs/{run_id}/steps?paper_id={paper_id}&include_internals=1")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        internals = payload["data"]["internals"]
        assert len(internals) >= 1
        plan = next((item for item in internals if item.get("internal_step") == "agentic_plan"), {})
        assert str(plan.get("status") or "") in {"unknown", "completed", "failed", "skipped"}


def test_workflow_steps_include_reuse_metadata_when_present(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "dummy")
    monkeypatch.setenv("PAPERS_DIR", "papers")
    _insert_user("wfreuse", "pass123")
    app = create_app()
    with app.test_client() as client:
        _ = _login(client, username="wfreuse", password="pass123")
        papers = client.get("/api/v1/papers").get_json()
        first = papers["data"]["papers"][0]
        paper_id = str(first["paper_id"])
        paper_path = str(first["path"])
        run_id = "run-reuse-meta"
        _insert_workflow_run(run_id=run_id, papers_dir=paper_path)
        _insert_workflow_step(
            run_id=run_id,
            step="prep",
            output={"status": "completed"},
            reuse_source_run_id="run-source",
            reuse_source_record_key="main",
        )

        out = client.get(f"/api/v1/workflow/runs/{run_id}/steps?paper_id={paper_id}&include_internals=0")
        assert out.status_code == 200
        payload = out.get_json()
        assert payload["ok"] is True
        prep = next((item for item in payload["data"]["steps"] if item.get("step") == "prep"), {})
        assert prep.get("reuse_source_run_id") == "run-source"
        assert prep.get("reuse_source_record_key") == "main"
