"""Flask API blueprint for the multi-user web surface."""

from __future__ import annotations

import json
import os
import smtplib
from pathlib import Path
from functools import wraps
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4
from email.message import EmailMessage

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file, stream_with_context
from pydantic import ValidationError

from ragonometrics.core.main import load_settings
from ragonometrics.services import auth as auth_service
from ragonometrics.services import chat as chat_service
from ragonometrics.services import chat_history as chat_history_service
from ragonometrics.services import citation_network as citation_network_service
from ragonometrics.services import openalex_metadata as openalex_metadata_service
from ragonometrics.services import notes as notes_service
from ragonometrics.services import papers as papers_service
from ragonometrics.services import rate_limit as rate_limit_service
from ragonometrics.services import structured as structured_service
from ragonometrics.services import usage as usage_service
from ragonometrics.services import workflow_cache as workflow_cache_service
from ragonometrics.web.schemas import (
    ChatTurnRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PaperNoteCreateRequest,
    PaperNoteUpdateRequest,
    ResetPasswordRequest,
    StructuredExportRequest,
    StructuredGenerateMissingRequest,
    StructuredGenerateRequest,
    parse_model,
)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _request_id() -> str:
    return str(getattr(g, "request_id", "") or uuid4().hex)


def _ok(data: Any, *, status: int = 200):
    return jsonify({"ok": True, "data": data, "request_id": _request_id()}), status


def _err(code: str, message: str, *, status: int = 400):
    return jsonify({"ok": False, "error": {"code": code, "message": message}, "request_id": _request_id()}), status


def _db_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def _send_password_reset_email(*, email: str, token: str, username: str) -> bool:
    """Send password reset email using configured SMTP settings."""
    smtp_host = str(os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(str(os.getenv("SMTP_PORT") or "587").strip() or "587")
    smtp_user = str(os.getenv("SMTP_USERNAME") or "").strip()
    smtp_password = str(os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = str(os.getenv("SMTP_FROM") or smtp_user or "noreply@localhost").strip()
    use_tls = str(os.getenv("SMTP_USE_TLS") or "1").strip().lower() not in {"0", "false", "no"}
    reset_base_url = str(os.getenv("WEB_RESET_BASE_URL") or "").strip()
    if not smtp_host or not email:
        return False

    if reset_base_url:
        reset_link = f"{reset_base_url.rstrip('/')}/?reset_token={token}"
    else:
        reset_link = f"Use this reset token in the app: {token}"

    msg = EmailMessage()
    msg["Subject"] = "Ragonometrics password reset"
    msg["From"] = smtp_from
    msg["To"] = email
    msg.set_content(
        "\n".join(
            [
                f"Hello {username or 'user'},",
                "",
                "A password reset was requested for your Ragonometrics account.",
                f"Reset link/token: {reset_link}",
                "",
                "If you did not request this, you can ignore this email.",
            ]
        )
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        return False


def _session_cookie_name() -> str:
    return str(current_app.config.get("WEB_SESSION_COOKIE_NAME", "rag_session"))


def _csrf_cookie_name() -> str:
    return str(current_app.config.get("WEB_CSRF_COOKIE_NAME", "rag_csrf"))


def _cookie_secure() -> bool:
    raw = str(current_app.config.get("WEB_COOKIE_SECURE", "1")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cookie_samesite() -> str:
    value = str(current_app.config.get("WEB_COOKIE_SAMESITE", "Lax")).strip() or "Lax"
    if value.lower() not in {"lax", "strict", "none"}:
        return "Lax"
    return value


def _set_auth_cookies(response: Response, *, session_id: str, csrf_token: str) -> None:
    max_age = int(current_app.config.get("WEB_SESSION_MAX_AGE_SECONDS", 60 * 60 * 12))
    cookie_kwargs = {
        "max_age": max_age,
        "httponly": True,
        "secure": _cookie_secure(),
        "samesite": _cookie_samesite(),
        "path": "/",
    }
    response.set_cookie(_session_cookie_name(), session_id, **cookie_kwargs)
    response.set_cookie(
        _csrf_cookie_name(),
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(_session_cookie_name(), path="/")
    response.delete_cookie(_csrf_cookie_name(), path="/")


def _rate_limit(*, subject_key: str, route: str, limit_env: str, window_env: str) -> Optional[tuple]:
    db_url = _db_url()
    limit_count = int(os.getenv(limit_env, "10"))
    window_seconds = int(os.getenv(window_env, "60"))
    result = rate_limit_service.check_rate_limit(
        db_url,
        subject_key=subject_key,
        route=route,
        limit_count=limit_count,
        window_seconds=window_seconds,
    )
    if not bool(result.get("allowed")):
        message = (
            f"Rate limit exceeded: {result.get('count')}/{result.get('limit')} in "
            f"{result.get('window_seconds')}s window."
        )
        return _err("rate_limited", message, status=429)
    return None


def _csrf_valid() -> bool:
    expected = str(request.cookies.get(_csrf_cookie_name()) or "")
    provided = str(request.headers.get("X-CSRF-Token") or "")
    return bool(expected and provided and expected == provided)


def require_auth(fn):
    """Require one authenticated DB-backed session."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        sid = str(request.cookies.get(_session_cookie_name()) or "").strip()
        user = auth_service.get_session_user(_db_url(), session_id=sid)
        if not user:
            return _err("unauthorized", "Authentication required.", status=401)
        g.current_user = user
        g.session_id = sid
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "api_v1.login":
            if not _csrf_valid():
                return _err("csrf_invalid", "Missing or invalid CSRF token.", status=403)
        return fn(*args, **kwargs)

    return wrapper


@api_bp.route("/auth/login", methods=["POST"])
def login():
    try:
        payload = parse_model(LoginRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)

    identifier = str(payload.identifier or payload.username or "").strip()
    if not identifier:
        return _err("validation_error", "identifier is required.", status=422)
    username = identifier
    limited = _rate_limit(
        subject_key=f"login:{identifier.lower()}",
        route="auth_login",
        limit_env="WEB_LOGIN_RATE_LIMIT",
        window_env="WEB_LOGIN_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited

    ok, user = auth_service.authenticate_user(identifier, payload.password)
    if not ok:
        return _err("invalid_credentials", "Invalid email/username or password.", status=401)

    sid = auth_service.persist_session(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or username),
        source="flask_ui",
    )
    csrf = uuid4().hex
    resp = jsonify(
        {
            "ok": True,
            "data": {
                "username": str(user.get("username") or username),
                "source": str(user.get("source") or "db"),
                "session_id": sid,
                "csrf_token": csrf,
            },
            "request_id": _request_id(),
        }
    )
    _set_auth_cookies(resp, session_id=sid, csrf_token=csrf)
    return resp


@api_bp.route("/auth/logout", methods=["POST"])
@require_auth
def logout():
    sid = str(getattr(g, "session_id", "") or "")
    if sid:
        auth_service.revoke_session(_db_url(), session_id=sid)
    resp = jsonify({"ok": True, "data": {"logged_out": True}, "request_id": _request_id()})
    _clear_auth_cookies(resp)
    return resp


@api_bp.route("/auth/forgot-password", methods=["POST"])
def forgot_password():
    try:
        payload = parse_model(ForgotPasswordRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)

    identifier = str(payload.identifier or "").strip()
    # Always return a generic success response to avoid account enumeration.
    token_info = auth_service.request_password_reset(
        _db_url(),
        identifier=identifier,
        request_ip=str(request.remote_addr or ""),
        user_agent=str(request.headers.get("User-Agent") or ""),
        ttl_minutes=int(os.getenv("WEB_RESET_TOKEN_TTL_MINUTES", "30")),
    )
    sent = False
    if token_info:
        sent = _send_password_reset_email(
            email=str(token_info.get("email") or ""),
            token=str(token_info.get("token") or ""),
            username=str(token_info.get("username") or ""),
        )
    data: Dict[str, Any] = {
        "accepted": True,
        "message": "If an account exists for that identifier, reset instructions were sent.",
        "email_sent": bool(sent),
    }
    if str(os.getenv("WEB_PASSWORD_RESET_DEBUG") or "").strip() == "1" and token_info:
        data["debug_reset_token"] = str(token_info.get("token") or "")
    return _ok(data)


@api_bp.route("/auth/reset-password", methods=["POST"])
def reset_password():
    try:
        payload = parse_model(ResetPasswordRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ok = auth_service.reset_password_with_token(
        _db_url(),
        token=payload.token,
        new_password=payload.new_password,
    )
    if not ok:
        return _err("invalid_or_expired_token", "Reset token is invalid or expired.", status=400)
    return _ok({"reset": True})


@api_bp.route("/auth/me", methods=["GET"])
@require_auth
def me():
    user = dict(getattr(g, "current_user", {}) or {})
    sid = str(getattr(g, "session_id", "") or "")
    csrf = str(request.cookies.get(_csrf_cookie_name()) or "").strip() or uuid4().hex
    resp = jsonify(
        {
            "ok": True,
            "data": {
                "username": user.get("username"),
                "user_id": user.get("user_id"),
                "session_id": user.get("session_id"),
                "csrf_token": csrf,
            },
            "request_id": _request_id(),
        }
    )
    _set_auth_cookies(resp, session_id=sid, csrf_token=csrf)
    return resp


@api_bp.route("/papers", methods=["GET"])
@require_auth
def papers():
    settings = load_settings()
    refs = papers_service.list_papers(settings=settings)
    rows = [papers_service.paper_overview(r) for r in refs]
    return _ok({"papers": rows, "count": len(rows)})


@api_bp.route("/papers/<paper_id>", methods=["GET"])
@require_auth
def paper_detail(paper_id: str):
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return _err("paper_not_found", "Unknown paper id.", status=404)
    return _ok(papers_service.paper_overview(ref))


@api_bp.route("/papers/<paper_id>/overview", methods=["GET"])
@require_auth
def paper_overview(paper_id: str):
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return _err("paper_not_found", "Unknown paper id.", status=404)
    return _ok(papers_service.paper_overview(ref))


@api_bp.route("/papers/<paper_id>/content", methods=["GET"])
@require_auth
def paper_content(paper_id: str):
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return _err("paper_not_found", "Unknown paper id.", status=404)
    path = Path(ref.path)
    if not path.exists() or not path.is_file():
        return _err("paper_not_found", "Selected paper file is unavailable.", status=404)
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=path.name)


def _resolve_paper_or_404(paper_id: str):
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return None, _err("paper_not_found", "Unknown paper id.", status=404)
    return ref, None


def _paper_title_hint(ref: papers_service.PaperRef) -> str:
    overview = papers_service.paper_overview(ref)
    title = str(overview.get("display_title") or overview.get("title") or "").strip()
    if title:
        return title
    stem = Path(str(ref.name or "")).stem
    return " ".join(stem.replace("_", " ").split())


@api_bp.route("/workflow/runs", methods=["GET"])
@require_auth
def workflow_runs():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        limit = int(request.args.get("limit") or 50)
    except Exception:
        limit = 50
    runs = workflow_cache_service.list_runs_for_paper(ref.path, limit=limit)
    selected_run_id = str((runs[0] if runs else {}).get("run_id") or "")
    return _ok(
        {
            "paper_id": ref.paper_id,
            "runs": runs,
            "count": len(runs),
            "selected_run_id": selected_run_id,
        }
    )


@api_bp.route("/workflow/runs/<run_id>/steps", methods=["GET"])
@require_auth
def workflow_run_steps(run_id: str):
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    include_internals = str(request.args.get("include_internals") or "1").strip().lower() not in {"0", "false", "no"}
    run = workflow_cache_service.get_run_record(run_id)
    if not run:
        return _err("workflow_run_not_found", "Workflow run not found.", status=404)
    if not workflow_cache_service.run_belongs_to_paper(run, ref.path):
        return _err("workflow_run_scope_mismatch", "Workflow run is not scoped to the selected paper.", status=404)
    steps = workflow_cache_service.list_steps_for_run(run_id)
    question_rows = workflow_cache_service.list_question_rows_for_run(run_id)
    usage_rows = workflow_cache_service.usage_rollup_for_run(run_id)
    usage_by_step = workflow_cache_service.summarize_usage_by_step(usage_rows)
    agentic_step: Optional[Dict[str, Any]] = None
    for row in steps:
        if str(row.get("step") or "") == "agentic":
            agentic_step = row
            break
    internals = (
        workflow_cache_service.derive_agentic_internals(agentic_step, question_rows, usage_rows)
        if include_internals
        else []
    )
    return _ok(
        {
            "paper_id": ref.paper_id,
            "run": run,
            "steps": steps,
            "count": len(steps),
            "internals": internals,
            "internals_count": len(internals),
            "include_internals": include_internals,
            "question_rows_count": len(question_rows),
            "usage_by_step": usage_by_step,
            "usage_rows": usage_rows,
        }
    )


@api_bp.route("/papers/<paper_id>/notes", methods=["GET"])
@require_auth
def paper_notes_list(paper_id: str):
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        page_number = int(request.args.get("page") or 0) or None
    except Exception:
        page_number = None
    user = dict(getattr(g, "current_user", {}) or {})
    rows = notes_service.list_notes(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        paper_id=ref.paper_id,
        page_number=page_number,
    )
    return _ok({"paper_id": ref.paper_id, "rows": rows, "count": len(rows)})


@api_bp.route("/papers/<paper_id>/notes", methods=["POST"])
@require_auth
def paper_notes_create(paper_id: str):
    try:
        payload = parse_model(PaperNoteCreateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    if str(payload.paper_id or "").strip() and str(payload.paper_id).strip() != ref.paper_id:
        return _err("validation_error", "paper_id mismatch in request body.", status=422)
    user = dict(getattr(g, "current_user", {}) or {})
    row = notes_service.create_note(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        paper_id=ref.paper_id,
        page_number=payload.page_number,
        highlight_text=payload.highlight_text,
        highlight_terms=list(payload.highlight_terms or []),
        note_text=payload.note_text,
        color=payload.color,
    )
    if not row:
        return _err("note_create_failed", "Unable to create note.", status=400)
    return _ok(row, status=201)


@api_bp.route("/papers/<paper_id>/notes/<int:note_id>", methods=["PATCH"])
@require_auth
def paper_notes_update(paper_id: str, note_id: int):
    try:
        payload = parse_model(PaperNoteUpdateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    row = notes_service.update_note(
        _db_url(),
        note_id=note_id,
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        note_text=payload.note_text,
        color=payload.color,
        highlight_text=payload.highlight_text,
        highlight_terms=payload.highlight_terms,
    )
    if not row or str(row.get("paper_id") or "") != ref.paper_id:
        return _err("note_not_found", "Note not found for this user and paper.", status=404)
    return _ok(row)


@api_bp.route("/papers/<paper_id>/notes/<int:note_id>", methods=["DELETE"])
@require_auth
def paper_notes_delete(paper_id: str, note_id: int):
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    rows = notes_service.list_notes(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        paper_id=ref.paper_id,
    )
    if not any(int(item.get("id") or 0) == int(note_id) for item in rows):
        return _err("note_not_found", "Note not found for this user and paper.", status=404)
    ok = notes_service.delete_note(
        _db_url(),
        note_id=note_id,
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
    )
    if not ok:
        return _err("note_delete_failed", "Unable to delete note.", status=400)
    return _ok({"deleted": True, "note_id": int(note_id)})


@api_bp.route("/openalex/metadata", methods=["GET"])
@require_auth
def openalex_metadata():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    data = openalex_metadata_service.metadata_for_paper(ref)
    return _ok(data)


@api_bp.route("/openalex/citation-network", methods=["GET"])
@require_auth
def openalex_citation_network():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        max_references = int(request.args.get("max_references") or 10)
    except Exception:
        max_references = 10
    try:
        max_citing = int(request.args.get("max_citing") or 10)
    except Exception:
        max_citing = 10
    data = citation_network_service.citation_network_for_paper(
        ref,
        max_references=max_references,
        max_citing=max_citing,
    )
    return _ok(data)


@api_bp.route("/chat/turn", methods=["POST"])
@require_auth
def chat_turn():
    try:
        payload = parse_model(ChatTurnRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    current_user = dict(getattr(g, "current_user", {}) or {})
    limited = _rate_limit(
        subject_key=f"chat:{current_user.get('user_id') or current_user.get('username')}",
        route="chat_turn",
        limit_env="WEB_CHAT_RATE_LIMIT",
        window_env="WEB_CHAT_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited
    history = [item.model_dump() for item in payload.history]
    req_id = _request_id()
    out = chat_service.chat_turn(
        paper_ref=ref,
        query=payload.question,
        model=payload.model,
        top_k=payload.top_k,
        session_id=str(getattr(g, "session_id", "") or ""),
        request_id=req_id,
        history=history,
        variation_mode=bool(payload.variation_mode),
    )
    answer = str(out.get("answer") or "").strip()
    if answer:
        chat_history_service.append_turn(
            _db_url(),
            user_id=current_user.get("user_id"),
            username=str(current_user.get("username") or ""),
            session_id=str(getattr(g, "session_id", "") or ""),
            paper_id=payload.paper_id,
            paper_path=ref.path,
            model=str(out.get("model") or payload.model or ""),
            variation_mode=bool(payload.variation_mode),
            query=payload.question,
            answer=answer,
            citations=out.get("citations") if isinstance(out.get("citations"), list) else [],
            retrieval_stats=out.get("retrieval_stats") if isinstance(out.get("retrieval_stats"), dict) else {},
            cache_hit=out.get("cache_hit") if isinstance(out.get("cache_hit"), bool) else None,
            request_id=req_id,
        )
    return _ok(out)


@api_bp.route("/chat/turn-stream", methods=["POST"])
@require_auth
def chat_turn_stream():
    try:
        payload = parse_model(ChatTurnRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    current_user = dict(getattr(g, "current_user", {}) or {})
    limited = _rate_limit(
        subject_key=f"chat:{current_user.get('user_id') or current_user.get('username')}",
        route="chat_turn_stream",
        limit_env="WEB_CHAT_RATE_LIMIT",
        window_env="WEB_CHAT_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited
    history = [item.model_dump() for item in payload.history]
    req_id = _request_id()
    session_id = str(getattr(g, "session_id", "") or "")
    username = str(current_user.get("username") or "")
    user_id = current_user.get("user_id")

    def stream_rows() -> Iterable[str]:
        persisted = False
        try:
            for row in chat_service.stream_chat_turn(
                paper_ref=ref,
                query=payload.question,
                model=payload.model,
                top_k=payload.top_k,
                session_id=session_id,
                request_id=req_id,
                history=history,
                variation_mode=bool(payload.variation_mode),
            ):
                if not persisted:
                    try:
                        parsed = json.loads(str(row or "").strip())
                        if isinstance(parsed, dict) and str(parsed.get("event") or "") == "done":
                            answer = str(parsed.get("answer") or "").strip()
                            if answer:
                                chat_history_service.append_turn(
                                    _db_url(),
                                    user_id=user_id,
                                    username=username,
                                    session_id=session_id,
                                    paper_id=payload.paper_id,
                                    paper_path=ref.path,
                                    model=str(parsed.get("model") or payload.model or ""),
                                    variation_mode=bool(payload.variation_mode),
                                    query=payload.question,
                                    answer=answer,
                                    citations=parsed.get("citations") if isinstance(parsed.get("citations"), list) else [],
                                    retrieval_stats=parsed.get("retrieval_stats")
                                    if isinstance(parsed.get("retrieval_stats"), dict)
                                    else {},
                                    cache_hit=parsed.get("cache_hit")
                                    if isinstance(parsed.get("cache_hit"), bool)
                                    else None,
                                    request_id=req_id,
                                )
                                persisted = True
                    except Exception:
                        pass
                yield row
        except Exception as exc:
            payload_row = {
                "event": "error",
                "code": "chat_failed",
                "message": str(exc),
                "request_id": req_id,
            }
            yield json.dumps(payload_row, ensure_ascii=False) + "\n"

    return Response(stream_with_context(stream_rows()), mimetype="application/x-ndjson")


@api_bp.route("/chat/suggestions", methods=["GET"])
@require_auth
def chat_suggestions():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    title_hint = _paper_title_hint(ref)
    return _ok(
        {
            "paper_id": ref.paper_id,
            "paper_title_hint": title_hint,
            "questions": chat_service.suggested_paper_questions(paper_title=title_hint),
        }
    )


@api_bp.route("/chat/history", methods=["GET"])
@require_auth
def chat_history_get():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        limit = int(request.args.get("limit") or 50)
    except Exception:
        limit = 50
    user = dict(getattr(g, "current_user", {}) or {})
    rows = chat_history_service.list_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        paper_id=paper_id,
        limit=limit,
    )
    return _ok({"paper_id": ref.paper_id, "rows": rows, "count": len(rows)})


@api_bp.route("/chat/history", methods=["DELETE"])
@require_auth
def chat_history_delete():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    deleted_count = chat_history_service.clear_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        paper_id=paper_id,
    )
    return _ok({"paper_id": ref.paper_id, "deleted_count": deleted_count})


@api_bp.route("/structured/questions", methods=["GET"])
@require_auth
def structured_questions():
    return _ok({"questions": structured_service.structured_report_questions()})


@api_bp.route("/structured/answers", methods=["GET"])
@require_auth
def structured_answers():
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    model = str(request.args.get("model") or "").strip() or None
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    answers = structured_service.db_workflow_structured_answers_for_paper(ref.path, model=model)
    return _ok({"answers": answers, "count": len(answers)})


@api_bp.route("/structured/generate", methods=["POST"])
@require_auth
def structured_generate():
    try:
        payload = parse_model(StructuredGenerateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    idem = str(request.headers.get("Idempotency-Key") or payload.idempotency_key or "").strip()
    out = structured_service.generate_and_cache_structured_answer(
        paper_ref=ref,
        question_id=payload.question_id,
        category=payload.category,
        question=payload.question,
        selected_model=payload.model,
        session_id=str(getattr(g, "session_id", "") or ""),
        top_k=payload.top_k,
        idempotency_key=idem,
    )
    return _ok(out)


@api_bp.route("/structured/generate-missing", methods=["POST"])
@require_auth
def structured_generate_missing():
    try:
        payload = parse_model(StructuredGenerateMissingRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    out = structured_service.generate_missing_structured_answers(
        paper_ref=ref,
        selected_model=payload.model,
        session_id=str(getattr(g, "session_id", "") or ""),
        top_k=payload.top_k,
        question_ids=list(payload.question_ids or []),
    )
    return _ok(out)


@api_bp.route("/structured/export", methods=["POST"])
@require_auth
def structured_export():
    try:
        payload = parse_model(StructuredExportRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    selected_model = str(payload.model or load_settings().chat_model)
    cache_scope = str(payload.cache_scope or "Selected model only")
    bundle = structured_service.export_bundle_for_paper(
        paper_ref=ref,
        selected_model=selected_model,
        cache_scope=cache_scope,
        export_format=str(payload.export_format or "compact"),
        question_ids=list(payload.question_ids or []),
    )
    if str(payload.output or "json").lower() == "pdf":
        try:
            pdf_bytes = structured_service.structured_workstream_pdf_bytes(bundle)
        except Exception as exc:
            return _err("pdf_render_failed", f"PDF export failed: {exc}", status=500)
        if pdf_bytes is None:
            return _err("pdf_unavailable", "PDF export dependency is unavailable.", status=503)
        filename = f"structured-workstream-{ref.paper_id}-{bundle.get('export_format') or 'compact'}.pdf"
        response = Response(pdf_bytes, mimetype="application/pdf")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Request-Id"] = _request_id()
        return response
    return _ok(bundle)


@api_bp.route("/usage/summary", methods=["GET"])
@require_auth
def usage_summary():
    since = str(request.args.get("since") or "").strip() or None
    session_only = str(request.args.get("session_only") or "1").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    data = usage_service.usage_summary(session_id=session_id, since=since)
    return _ok(data)


@api_bp.route("/usage/by-model", methods=["GET"])
@require_auth
def usage_by_model():
    since = str(request.args.get("since") or "").strip() or None
    session_only = str(request.args.get("session_only") or "1").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    rows = usage_service.usage_by_model(session_id=session_id, since=since)
    return _ok({"rows": rows, "count": len(rows)})


@api_bp.route("/usage/recent", methods=["GET"])
@require_auth
def usage_recent():
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    limit = max(1, min(1000, limit))
    session_only = str(request.args.get("session_only") or "1").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    rows = usage_service.recent_usage(limit=limit, session_id=session_id)
    return _ok({"rows": rows, "count": len(rows)})
