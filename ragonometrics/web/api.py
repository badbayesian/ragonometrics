"""Flask API blueprint for the multi-user web surface."""

from __future__ import annotations

import json
import os
import smtplib
from pathlib import Path
from functools import wraps
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4
from email.message import EmailMessage

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file, stream_with_context
from pydantic import ValidationError

from ragonometrics.core.main import load_settings
from ragonometrics.services import auth as auth_service
from ragonometrics.services import cache_inspector as cache_inspector_service
from ragonometrics.services import chat as chat_service
from ragonometrics.services import chat_history as chat_history_service
from ragonometrics.services import citation_network as citation_network_service
from ragonometrics.services import multi_paper_chat as multi_paper_chat_service
from ragonometrics.services import openalex_metadata as openalex_metadata_service
from ragonometrics.services import notes as notes_service
from ragonometrics.services import paper_compare as paper_compare_service
from ragonometrics.services import papers as papers_service
from ragonometrics.services import projects as projects_service
from ragonometrics.services import provenance as provenance_service
from ragonometrics.services import rate_limit as rate_limit_service
from ragonometrics.services import structured as structured_service
from ragonometrics.services import usage as usage_service
from ragonometrics.services import workflow_cache as workflow_cache_service
from ragonometrics.web.schemas import (
    ChatProvenanceScoreRequest,
    ChatTurnRequest,
    CompareCreateRequest,
    CompareExportRequest,
    CompareFillMissingRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MultiChatNetworkRequest,
    MultiChatTurnRequest,
    OpenAlexManualLinkRequest,
    PaperNoteCreateRequest,
    PaperNoteUpdateRequest,
    RegisterRequest,
    ResetPasswordRequest,
    StructuredExportRequest,
    StructuredGenerateMissingRequest,
    StructuredGenerateRequest,
    parse_model,
)

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _request_id() -> str:
    """Internal helper for request id."""
    return str(getattr(g, "request_id", "") or uuid4().hex)


def _ok(data: Any, *, status: int = 200):
    """Internal helper for ok."""
    return jsonify({"ok": True, "data": data, "request_id": _request_id()}), status


def _err(code: str, message: str, *, status: int = 400):
    """Internal helper for err."""
    return jsonify({"ok": False, "error": {"code": code, "message": message}, "request_id": _request_id()}), status


def _log_mutation(event: str, *, paper_id: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    """Emit one structured mutation log row for observability."""
    user = dict(getattr(g, "current_user", {}) or {})
    payload: Dict[str, Any] = {
        "event": str(event or "").strip(),
        "request_id": _request_id(),
        "method": request.method,
        "path": request.path,
        "paper_id": str(paper_id or ""),
        "user_id": user.get("user_id"),
        "username": str(user.get("username") or ""),
        "session_id": str(getattr(g, "session_id", "") or ""),
        "project_id": str(getattr(g, "project_id", "") or ""),
        "persona_id": str(getattr(g, "persona_id", "") or ""),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        current_app.logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        return


def _db_url() -> str:
    """Internal helper for db url."""
    return (os.environ.get("DATABASE_URL") or "").strip()


def _current_project_context() -> projects_service.ProjectContext:
    """Internal helper for current project context."""
    context = getattr(g, "project_context", None)
    if isinstance(context, projects_service.ProjectContext):
        return context
    user = dict(getattr(g, "current_user", {}) or {})
    return projects_service.ProjectContext(
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=projects_service.DEFAULT_PROJECT_ID,
        project_name=projects_service.DEFAULT_PROJECT_NAME,
        role="viewer",
        persona_id=projects_service.DEFAULT_PERSONA_ID,
        persona_name=projects_service.DEFAULT_PERSONA_NAME,
        allow_cross_project_answer_reuse=True,
        allow_custom_question_sharing=False,
    )


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


def _send_new_account_alert_email(
    *,
    created_username: str,
    created_email: str,
    request_ip: str,
    user_agent: str,
) -> bool:
    """Send a notification email when a new account is created."""
    smtp_host = str(os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int(str(os.getenv("SMTP_PORT") or "587").strip() or "587")
    smtp_user = str(os.getenv("SMTP_USERNAME") or "").strip()
    smtp_password = str(os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = str(os.getenv("SMTP_FROM") or smtp_user or "noreply@localhost").strip()
    use_tls = str(os.getenv("SMTP_USE_TLS") or "1").strip().lower() not in {"0", "false", "no"}
    alert_to_raw = str(os.getenv("WEB_NEW_ACCOUNT_ALERT_TO") or "").strip()
    recipients = [item.strip() for item in alert_to_raw.split(",") if item.strip()]
    if not smtp_host or not recipients:
        return False

    msg = EmailMessage()
    msg["Subject"] = "Ragonometrics new account created"
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        "\n".join(
            [
                "A new Ragonometrics account was created.",
                f"Username: {created_username}",
                f"Email: {created_email or 'n/a'}",
                f"IP: {request_ip or 'n/a'}",
                f"User-Agent: {user_agent or 'n/a'}",
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
    """Internal helper for session cookie name."""
    return str(current_app.config.get("WEB_SESSION_COOKIE_NAME", "rag_session"))


def _csrf_cookie_name() -> str:
    """Internal helper for csrf cookie name."""
    return str(current_app.config.get("WEB_CSRF_COOKIE_NAME", "rag_csrf"))


def _cookie_secure() -> bool:
    """Internal helper for cookie secure."""
    raw = str(current_app.config.get("WEB_COOKIE_SECURE", "1")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cookie_samesite() -> str:
    """Internal helper for cookie samesite."""
    value = str(current_app.config.get("WEB_COOKIE_SAMESITE", "Lax")).strip() or "Lax"
    if value.lower() not in {"lax", "strict", "none"}:
        return "Lax"
    return value


def _registration_enabled() -> bool:
    """Internal helper for registration enabled."""
    raw = str(os.getenv("WEB_REGISTRATION_ENABLED", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _registration_auto_approve_enabled() -> bool:
    """Internal helper for registration auto approve."""
    raw = str(os.getenv("WEB_REGISTRATION_AUTO_APPROVE", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _set_auth_cookies(response: Response, *, session_id: str, csrf_token: str) -> None:
    """Internal helper for set auth cookies."""
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
    """Internal helper for clear auth cookies."""
    response.delete_cookie(_session_cookie_name(), path="/")
    response.delete_cookie(_csrf_cookie_name(), path="/")


def _rate_limit(*, subject_key: str, route: str, limit_env: str, window_env: str) -> Optional[tuple]:
    """Internal helper for rate limit."""
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
    """Internal helper for csrf valid."""
    expected = str(request.cookies.get(_csrf_cookie_name()) or "")
    provided = str(request.headers.get("X-CSRF-Token") or "")
    return bool(expected and provided and expected == provided)


def require_auth(fn):
    """Require one authenticated DB-backed session."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        """Handle wrapper."""
        sid = str(request.cookies.get(_session_cookie_name()) or "").strip()
        user = auth_service.get_session_user(_db_url(), session_id=sid)
        if not user:
            return _err("unauthorized", "Authentication required.", status=401)
        g.current_user = user
        g.session_id = sid
        project_context = projects_service.get_project_context(
            _db_url(),
            session_id=sid,
            user_id=user.get("user_id"),
            username=str(user.get("username") or ""),
        )
        g.project_context = project_context
        g.project_id = project_context.project_id
        g.persona_id = project_context.persona_id
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "api_v1.login":
            if not _csrf_valid():
                return _err("csrf_invalid", "Missing or invalid CSRF token.", status=403)
        return fn(*args, **kwargs)

    return wrapper


@api_bp.route("/auth/login", methods=["POST"])
def login():
    """Authenticate a user and establish an authenticated web session."""
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
        err_code = str((user or {}).get("code") or "").strip()
        if err_code == "account_pending_approval":
            return _err(
                "account_pending_approval",
                "Account created. An administrator must approve it before login.",
                status=403,
            )
        return _err("invalid_credentials", "Invalid email/username or password.", status=401)

    sid = auth_service.persist_session(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or username),
        source="flask_ui",
    )
    project_context = projects_service.get_project_context(
        _db_url(),
        session_id=sid,
        user_id=user.get("user_id"),
        username=str(user.get("username") or username),
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
                "project_context": {
                    "project_id": project_context.project_id,
                    "project_name": project_context.project_name,
                    "role": project_context.role,
                    "persona_id": project_context.persona_id,
                    "persona_name": project_context.persona_name,
                    "allow_cross_project_answer_reuse": project_context.allow_cross_project_answer_reuse,
                    "allow_custom_question_sharing": project_context.allow_custom_question_sharing,
                },
            },
            "request_id": _request_id(),
        }
    )
    _set_auth_cookies(resp, session_id=sid, csrf_token=csrf)
    _log_mutation(
        "auth.login",
        extra={
            "login_identifier": identifier,
            "auth_source": str(user.get("source") or "db"),
        },
    )
    return resp


@api_bp.route("/auth/register", methods=["POST"])
def register():
    """Create a user account and return an authenticated session for the new user."""
    if not _registration_enabled():
        return _err(
            "registration_disabled",
            "Account registration is currently disabled. Contact an administrator.",
            status=403,
        )
    try:
        payload = parse_model(RegisterRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)

    username = str(payload.username or "").strip()
    email = str(payload.email or "").strip()
    password = str(payload.password or "")
    limited = _rate_limit(
        subject_key=f"register:{username.lower() or request.remote_addr or 'unknown'}",
        route="auth_register",
        limit_env="WEB_REGISTER_RATE_LIMIT",
        window_env="WEB_REGISTER_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited

    ok, out = auth_service.register_db_user(
        _db_url(),
        username=username,
        email=email,
        password=password,
        is_active=_registration_auto_approve_enabled(),
    )
    if not ok:
        code = str(out.get("code") or "register_failed")
        message = str(out.get("message") or "Could not create account.")
        status_code = 409 if code in {"username_taken", "email_taken"} else 400
        return _err(code, message, status=status_code)

    alert_sent = _send_new_account_alert_email(
        created_username=str(out.get("username") or username),
        created_email=str(out.get("email") or email),
        request_ip=str(request.remote_addr or ""),
        user_agent=str(request.headers.get("User-Agent") or ""),
    )
    _log_mutation(
        "auth.register",
        extra={
            "registered_username": str(out.get("username") or username),
            "registered_email": str(out.get("email") or email),
            "alert_email_sent": bool(alert_sent),
        },
    )
    return _ok(
        {
            "created": True,
            "username": str(out.get("username") or username),
            "email": str(out.get("email") or email),
            "approved": bool(out.get("is_active")),
            "requires_approval": not bool(out.get("is_active")),
            "alert_email_sent": bool(alert_sent),
        },
        status=201,
    )


@api_bp.route("/auth/logout", methods=["POST"])
@require_auth
def logout():
    """Invalidate the current authenticated session and clear auth cookies."""
    sid = str(getattr(g, "session_id", "") or "")
    if sid:
        auth_service.revoke_session(_db_url(), session_id=sid)
    _log_mutation("auth.logout")
    resp = jsonify({"ok": True, "data": {"logged_out": True}, "request_id": _request_id()})
    _clear_auth_cookies(resp)
    return resp


@api_bp.route("/auth/forgot-password", methods=["POST"])
def forgot_password():
    """Start a password reset flow and send reset instructions when possible."""
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
    _log_mutation(
        "auth.forgot_password",
        extra={"identifier": identifier, "email_sent": bool(sent), "token_issued": bool(token_info)},
    )
    return _ok(data)


@api_bp.route("/auth/reset-password", methods=["POST"])
def reset_password():
    """Complete a password reset using a valid reset token."""
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
        _log_mutation("auth.reset_password_failed")
        return _err("invalid_or_expired_token", "Reset token is invalid or expired.", status=400)
    _log_mutation("auth.reset_password")
    return _ok({"reset": True})


@api_bp.route("/auth/me", methods=["GET"])
@require_auth
def me():
    """Return the authenticated user profile and current project/persona context."""
    user = dict(getattr(g, "current_user", {}) or {})
    sid = str(getattr(g, "session_id", "") or "")
    context = _current_project_context()
    projects = projects_service.list_user_projects(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
    )
    personas = projects_service.list_project_personas(_db_url(), project_id=context.project_id)
    csrf = str(request.cookies.get(_csrf_cookie_name()) or "").strip() or uuid4().hex
    resp = jsonify(
        {
            "ok": True,
            "data": {
                "username": user.get("username"),
                "user_id": user.get("user_id"),
                "session_id": user.get("session_id"),
                "csrf_token": csrf,
                "project_context": {
                    "project_id": context.project_id,
                    "project_name": context.project_name,
                    "role": context.role,
                    "persona_id": context.persona_id,
                    "persona_name": context.persona_name,
                    "allow_cross_project_answer_reuse": context.allow_cross_project_answer_reuse,
                    "allow_custom_question_sharing": context.allow_custom_question_sharing,
                },
                "projects": projects,
                "personas": personas,
            },
            "request_id": _request_id(),
        }
    )
    _set_auth_cookies(resp, session_id=sid, csrf_token=csrf)
    return resp


def _user_project_map() -> Dict[str, Dict[str, Any]]:
    """Internal helper for user project map."""
    user = dict(getattr(g, "current_user", {}) or {})
    rows = projects_service.list_user_projects(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
    )
    return {str(item.get("project_id") or ""): item for item in rows if str(item.get("project_id") or "").strip()}


def _ensure_project_access_or_404(project_id: str) -> Optional[tuple]:
    """Internal helper for ensure project access or 404."""
    wanted = str(project_id or "").strip()
    projects = _user_project_map()
    if not wanted or wanted not in projects:
        return _err("project_not_found", "Project not found.", status=404)
    return None


@api_bp.route("/projects", methods=["GET"])
@require_auth
def projects_list():
    """List projects visible to the authenticated user."""
    user = dict(getattr(g, "current_user", {}) or {})
    rows = projects_service.list_user_projects(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
    )
    context = _current_project_context()
    return _ok({"rows": rows, "count": len(rows), "current_project_id": context.project_id})


@api_bp.route("/projects", methods=["POST"])
@require_auth
def projects_create():
    """Create a new project owned by the authenticated user."""
    payload = request.get_json(silent=True) or {}
    name = str((payload or {}).get("name") or "").strip()
    user = dict(getattr(g, "current_user", {}) or {})
    try:
        created = projects_service.create_project(
            _db_url(),
            name=name,
            created_by_user_id=user.get("user_id"),
            created_by_username=str(user.get("username") or ""),
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    _log_mutation("projects.create", extra={"project_id": str(created.get("project_id") or "")})
    return _ok(created, status=201)


@api_bp.route("/projects/current", methods=["GET"])
@require_auth
def projects_current():
    """Return the currently selected project and persona context."""
    context = _current_project_context()
    personas = projects_service.list_project_personas(_db_url(), project_id=context.project_id)
    return _ok(
        {
            "project_id": context.project_id,
            "project_name": context.project_name,
            "role": context.role,
            "persona_id": context.persona_id,
            "persona_name": context.persona_name,
            "allow_cross_project_answer_reuse": context.allow_cross_project_answer_reuse,
            "allow_custom_question_sharing": context.allow_custom_question_sharing,
            "personas": personas,
        }
    )


@api_bp.route("/projects/<project_id>/select", methods=["POST"])
@require_auth
def projects_select(project_id: str):
    """Set the active project for the current authenticated session."""
    user = dict(getattr(g, "current_user", {}) or {})
    try:
        context = projects_service.select_project(
            _db_url(),
            session_id=str(getattr(g, "session_id", "") or ""),
            user_id=user.get("user_id"),
            username=str(user.get("username") or ""),
            project_id=project_id,
        )
    except ValueError:
        return _err("project_not_found", "Project not found.", status=404)
    g.project_context = context
    g.project_id = context.project_id
    g.persona_id = context.persona_id
    _log_mutation("projects.select", extra={"project_id": context.project_id, "persona_id": context.persona_id})
    return _ok(
        {
            "project_id": context.project_id,
            "project_name": context.project_name,
            "role": context.role,
            "persona_id": context.persona_id,
            "persona_name": context.persona_name,
            "allow_cross_project_answer_reuse": context.allow_cross_project_answer_reuse,
            "allow_custom_question_sharing": context.allow_custom_question_sharing,
        }
    )


@api_bp.route("/projects/<project_id>/members", methods=["POST"])
@require_auth
def projects_add_member(project_id: str):
    """Add a member to a project when the caller has sufficient permissions."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    payload = request.get_json(silent=True) or {}
    identifier = str((payload or {}).get("identifier") or "").strip()
    role = str((payload or {}).get("role") or "viewer").strip().lower()
    try:
        added = projects_service.add_project_member(
            _db_url(),
            project_id=project_id,
            identifier=identifier,
            role=role,
        )
    except ValueError as exc:
        message = str(exc)
        if message in {"project_not_found", "user_not_found"}:
            return _err(message, message.replace("_", " ").capitalize() + ".", status=404)
        return _err("validation_error", message, status=422)
    _log_mutation(
        "projects.add_member",
        extra={"project_id": project_id, "member_user_id": added.get("user_id"), "role": added.get("role")},
    )
    return _ok(added, status=201)


@api_bp.route("/projects/<project_id>/papers", methods=["GET"])
@require_auth
def projects_list_papers(project_id: str):
    """List papers currently attached to the selected project."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    rows = projects_service.list_project_papers(_db_url(), project_id=project_id)
    return _ok({"rows": rows, "count": len(rows), "project_id": project_id})


@api_bp.route("/projects/<project_id>/papers", methods=["POST"])
@require_auth
def projects_add_paper(project_id: str):
    """Attach a paper to the selected project scope."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    payload = request.get_json(silent=True) or {}
    paper_id = str((payload or {}).get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return _err("paper_not_found", "Unknown paper id.", status=404)
    user = dict(getattr(g, "current_user", {}) or {})
    try:
        row = projects_service.add_project_paper(
            _db_url(),
            project_id=project_id,
            paper_id=ref.paper_id,
            paper_path=ref.path,
            added_by_user_id=user.get("user_id"),
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    _log_mutation("projects.add_paper", paper_id=ref.paper_id, extra={"project_id": project_id})
    return _ok(row, status=201)


@api_bp.route("/projects/<project_id>/settings", methods=["PATCH"])
@require_auth
def projects_patch_settings(project_id: str):
    """Update editable project-level settings for the selected project."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    payload = request.get_json(silent=True) or {}
    allow_cross = payload.get("allow_cross_project_answer_reuse")
    allow_custom = payload.get("allow_custom_question_sharing")
    try:
        data = projects_service.update_project_settings(
            _db_url(),
            project_id=project_id,
            allow_cross_project_answer_reuse=allow_cross if isinstance(allow_cross, bool) else None,
            allow_custom_question_sharing=allow_custom if isinstance(allow_custom, bool) else None,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    _log_mutation(
        "projects.patch_settings",
        extra={
            "project_id": project_id,
            "allow_cross_project_answer_reuse": data.get("allow_cross_project_answer_reuse"),
            "allow_custom_question_sharing": data.get("allow_custom_question_sharing"),
        },
    )
    return _ok(data)


@api_bp.route("/projects/<project_id>/personas", methods=["GET"])
@require_auth
def projects_personas(project_id: str):
    """List personas available for the selected project."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    rows = projects_service.list_project_personas(_db_url(), project_id=project_id)
    return _ok({"rows": rows, "count": len(rows), "project_id": project_id})


@api_bp.route("/projects/<project_id>/personas/<persona_id>/select", methods=["POST"])
@require_auth
def projects_select_persona(project_id: str, persona_id: str):
    """Set the active persona for the current authenticated session."""
    access_err = _ensure_project_access_or_404(project_id)
    if access_err is not None:
        return access_err
    user = dict(getattr(g, "current_user", {}) or {})
    try:
        context = projects_service.select_persona(
            _db_url(),
            session_id=str(getattr(g, "session_id", "") or ""),
            user_id=user.get("user_id"),
            username=str(user.get("username") or ""),
            project_id=project_id,
            persona_id=persona_id,
        )
    except ValueError as exc:
        message = str(exc)
        code = "persona_not_found" if "persona" in message else "validation_error"
        status = 404 if code == "persona_not_found" else 422
        return _err(code, message, status=status)
    g.project_context = context
    g.project_id = context.project_id
    g.persona_id = context.persona_id
    _log_mutation("projects.select_persona", extra={"project_id": project_id, "persona_id": persona_id})
    return _ok(
        {
            "project_id": context.project_id,
            "persona_id": context.persona_id,
            "persona_name": context.persona_name,
            "allow_cross_project_answer_reuse": context.allow_cross_project_answer_reuse,
            "allow_custom_question_sharing": context.allow_custom_question_sharing,
        }
    )


@api_bp.route("/papers", methods=["GET"])
@require_auth
def papers():
    """List available papers for the current project context."""
    settings = load_settings()
    refs = papers_service.list_papers(settings=settings)
    context = _current_project_context()
    allowed_ids = set(projects_service.project_paper_ids(_db_url(), project_id=context.project_id))
    if allowed_ids:
        refs = [ref for ref in refs if ref.paper_id in allowed_ids]
    elif context.project_id != projects_service.DEFAULT_PROJECT_ID:
        refs = []
    rows = [papers_service.paper_overview(r) for r in refs]
    return _ok({"papers": rows, "count": len(rows), "project_id": context.project_id})


@api_bp.route("/papers/<paper_id>", methods=["GET"])
@require_auth
def paper_detail(paper_id: str):
    """Return detailed metadata for a single paper in scope."""
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    return _ok(papers_service.paper_overview(ref))


@api_bp.route("/papers/<paper_id>/overview", methods=["GET"])
@require_auth
def paper_overview(paper_id: str):
    """Return overview content for a single paper."""
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    return _ok(papers_service.paper_overview(ref))


@api_bp.route("/papers/<paper_id>/content", methods=["GET"])
@require_auth
def paper_content(paper_id: str):
    """Return normalized content payloads for a single paper."""
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    path = Path(ref.path)
    if not path.exists() or not path.is_file():
        return _err("paper_not_found", "Selected paper file is unavailable.", status=404)
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=path.name)


def _resolve_paper_or_404(paper_id: str):
    """Internal helper for resolve paper or 404."""
    ref = papers_service.resolve_paper(paper_id, settings=load_settings())
    if not ref:
        return None, _err("paper_not_found", "Unknown paper id.", status=404)
    context = _current_project_context()
    allowed_ids = set(projects_service.project_paper_ids(_db_url(), project_id=context.project_id))
    if allowed_ids and ref.paper_id not in allowed_ids:
        return None, _err("paper_not_found", "Unknown paper id.", status=404)
    if not allowed_ids and context.project_id != projects_service.DEFAULT_PROJECT_ID:
        return None, _err("paper_not_found", "Unknown paper id.", status=404)
    return ref, None


def _paper_title_hint(ref: papers_service.PaperRef) -> str:
    """Internal helper for paper title hint."""
    overview = papers_service.paper_overview(ref)
    title = str(overview.get("display_title") or overview.get("title") or "").strip()
    if title:
        return title
    stem = Path(str(ref.name or "")).stem
    return " ".join(stem.replace("_", " ").split())


def _query_list_param(name: str) -> List[str]:
    """Parse repeated or comma-separated query parameters into a cleaned list."""
    raw_items = list(request.args.getlist(name) or [])
    if len(raw_items) <= 1:
        one = str(raw_items[0] if raw_items else request.args.get(name) or "").strip()
        if one:
            raw_items = [part.strip() for part in one.split(",")]
    out: List[str] = []
    seen = set()
    for item in raw_items:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


@api_bp.route("/compare/similar-papers", methods=["GET"])
@require_auth
def compare_similar_papers():
    """Return similar-paper suggestions for comparison workflows."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    try:
        context = _current_project_context()
        try:
            data = paper_compare_service.suggest_similar_papers(
                ref.paper_id,
                limit=limit,
                project_id=context.project_id,
            )
        except TypeError:
            data = paper_compare_service.suggest_similar_papers(ref.paper_id, limit=limit)
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("compare_suggestions_failed", f"Failed to rank similar papers: {exc}", status=500)
    return _ok(data)


@api_bp.route("/compare/runs", methods=["POST"])
@require_auth
def compare_create_run():
    """Create and start a multi-paper comparison run."""
    try:
        payload = parse_model(CompareCreateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    current_user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    try:
        data = paper_compare_service.create_comparison_run(
            seed_paper_id=str(payload.seed_paper_id or "").strip() or None,
            paper_ids=[str(item or "").strip() for item in list(payload.paper_ids or [])],
            questions=[str(item or "") for item in list(payload.questions or [])],
            model=payload.model,
            name=str(payload.name or "").strip() or None,
            created_by_user_id=current_user.get("user_id"),
            created_by_username=str(current_user.get("username") or ""),
            project_id=context.project_id,
            persona_id=context.persona_id,
            allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
            allow_custom_question_sharing=context.allow_custom_question_sharing,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("compare_create_failed", f"Failed to create comparison: {exc}", status=500)
    _log_mutation(
        "compare.create",
        extra={
            "comparison_id": str(data.get("comparison_id") or ""),
            "paper_count": len(data.get("papers") or []),
            "question_count": len(data.get("questions") or []),
        },
    )
    return _ok(data, status=201)


@api_bp.route("/compare/runs", methods=["GET"])
@require_auth
def compare_list_runs():
    """List comparison runs for the current project and user context."""
    try:
        limit = int(request.args.get("limit") or 50)
    except Exception:
        limit = 50
    try:
        offset = int(request.args.get("offset") or 0)
    except Exception:
        offset = 0
    context = _current_project_context()
    try:
        data = paper_compare_service.list_comparison_runs(
            limit=limit,
            offset=offset,
            project_id=context.project_id,
        )
    except TypeError:
        data = paper_compare_service.list_comparison_runs(limit=limit, offset=offset)
    return _ok(data)


@api_bp.route("/compare/runs/<comparison_id>", methods=["GET"])
@require_auth
def compare_get_run(comparison_id: str):
    """Return detailed results for a specific comparison run."""
    context = _current_project_context()
    try:
        data = paper_compare_service.get_comparison_run(
            comparison_id,
            project_id=context.project_id,
        )
    except TypeError:
        data = paper_compare_service.get_comparison_run(comparison_id)
    if not data:
        return _err("comparison_not_found", "Comparison run not found.", status=404)
    return _ok(data)


@api_bp.route("/compare/runs/<comparison_id>/fill-missing", methods=["POST"])
@require_auth
def compare_fill_missing(comparison_id: str):
    """Backfill missing comparison outputs for an existing run."""
    try:
        payload = parse_model(CompareFillMissingRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    context = _current_project_context()
    try:
        data = paper_compare_service.fill_missing_cells(
            comparison_id=comparison_id,
            paper_ids=[str(item or "").strip() for item in list(payload.paper_ids or [])],
            question_ids=[str(item or "").strip() for item in list(payload.question_ids or [])],
            project_id=context.project_id,
            persona_id=context.persona_id,
            allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
            allow_custom_question_sharing=context.allow_custom_question_sharing,
        )
    except ValueError as exc:
        if str(exc) == "comparison_not_found":
            return _err("comparison_not_found", "Comparison run not found.", status=404)
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("compare_fill_missing_failed", f"Failed to fill missing cells: {exc}", status=500)
    _log_mutation(
        "compare.fill_missing",
        extra={
            "comparison_id": comparison_id,
            "missing_cells": int((data.get("summary") or {}).get("missing_cells") or 0),
            "failed_cells": int((data.get("summary") or {}).get("failed_cells") or 0),
        },
    )
    return _ok(data)


@api_bp.route("/compare/runs/<comparison_id>/export", methods=["POST"])
@require_auth
def compare_export(comparison_id: str):
    """Export comparison results in the requested format."""
    try:
        payload = parse_model(CompareExportRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    context = _current_project_context()
    try:
        try:
            data = paper_compare_service.export_comparison(
                comparison_id=comparison_id,
                export_format=str(payload.format or "json"),
                project_id=context.project_id,
            )
        except TypeError:
            data = paper_compare_service.export_comparison(
                comparison_id=comparison_id,
                export_format=str(payload.format or "json"),
            )
    except ValueError as exc:
        if str(exc) == "comparison_not_found":
            return _err("comparison_not_found", "Comparison run not found.", status=404)
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("compare_export_failed", f"Failed to export comparison: {exc}", status=500)
    return _ok(data)


@api_bp.route("/workflow/runs", methods=["GET"])
@require_auth
def workflow_runs():
    """List workflow runs for a paper under the current access scope."""
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
    context = _current_project_context()
    runs = workflow_cache_service.list_runs_for_paper(
        ref.path,
        limit=limit,
        project_id=context.project_id,
    )
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
    """Return step-level details for one workflow run."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    include_internals = str(request.args.get("include_internals") or "1").strip().lower() not in {"0", "false", "no"}
    context = _current_project_context()
    run = workflow_cache_service.get_run_record(run_id, project_id=context.project_id)
    if not run:
        return _err("workflow_run_not_found", "Workflow run not found.", status=404)
    if not workflow_cache_service.run_belongs_to_paper(run, ref.path):
        return _err("workflow_run_scope_mismatch", "Workflow run is not scoped to the selected paper.", status=404)
    steps = workflow_cache_service.list_steps_for_run(run_id, project_id=context.project_id)
    question_rows = workflow_cache_service.list_question_rows_for_run(run_id, project_id=context.project_id)
    usage_rows = workflow_cache_service.usage_rollup_for_run(run_id, project_id=context.project_id)
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
    """List viewer notes for a paper in the current user/project scope."""
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        page_number = int(request.args.get("page") or 0) or None
    except Exception:
        page_number = None
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    rows = notes_service.list_notes(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        paper_id=ref.paper_id,
        page_number=page_number,
    )
    return _ok({"paper_id": ref.paper_id, "rows": rows, "count": len(rows)})


@api_bp.route("/papers/<paper_id>/notes", methods=["POST"])
@require_auth
def paper_notes_create(paper_id: str):
    """Create a new viewer note for a paper."""
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
    context = _current_project_context()
    row = notes_service.create_note(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        paper_id=ref.paper_id,
        page_number=payload.page_number,
        highlight_text=payload.highlight_text,
        highlight_terms=list(payload.highlight_terms or []),
        note_text=payload.note_text,
        color=payload.color,
    )
    if not row:
        _log_mutation("notes.create_failed", paper_id=ref.paper_id)
        return _err("note_create_failed", "Unable to create note.", status=400)
    _log_mutation("notes.create", paper_id=ref.paper_id, extra={"note_id": row.get("id")})
    return _ok(row, status=201)


@api_bp.route("/papers/<paper_id>/notes/<int:note_id>", methods=["PATCH"])
@require_auth
def paper_notes_update(paper_id: str, note_id: int):
    """Update a viewer note owned by the current user."""
    try:
        payload = parse_model(PaperNoteUpdateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    row = notes_service.update_note(
        _db_url(),
        note_id=note_id,
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        note_text=payload.note_text,
        color=payload.color,
        highlight_text=payload.highlight_text,
        highlight_terms=payload.highlight_terms,
    )
    if not row or str(row.get("paper_id") or "") != ref.paper_id:
        _log_mutation("notes.update_not_found", paper_id=ref.paper_id, extra={"note_id": int(note_id)})
        return _err("note_not_found", "Note not found for this user and paper.", status=404)
    _log_mutation("notes.update", paper_id=ref.paper_id, extra={"note_id": int(note_id)})
    return _ok(row)


@api_bp.route("/papers/<paper_id>/notes/<int:note_id>", methods=["DELETE"])
@require_auth
def paper_notes_delete(paper_id: str, note_id: int):
    """Delete a viewer note owned by the current user."""
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    rows = notes_service.list_notes(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        paper_id=ref.paper_id,
    )
    if not any(int(item.get("id") or 0) == int(note_id) for item in rows):
        _log_mutation("notes.delete_not_found", paper_id=ref.paper_id, extra={"note_id": int(note_id)})
        return _err("note_not_found", "Note not found for this user and paper.", status=404)
    ok = notes_service.delete_note(
        _db_url(),
        note_id=note_id,
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
    )
    if not ok:
        _log_mutation("notes.delete_failed", paper_id=ref.paper_id, extra={"note_id": int(note_id)})
        return _err("note_delete_failed", "Unable to delete note.", status=400)
    _log_mutation("notes.delete", paper_id=ref.paper_id, extra={"note_id": int(note_id)})
    return _ok({"deleted": True, "note_id": int(note_id)})


@api_bp.route("/openalex/metadata", methods=["GET"])
@require_auth
def openalex_metadata():
    """Return OpenAlex metadata for a selected paper."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        data = openalex_metadata_service.metadata_for_paper(ref)
    except Exception as exc:
        return _err("openalex_metadata_failed", f"OpenAlex metadata request failed: {exc}", status=502)
    return _ok(data)


@api_bp.route("/openalex/metadata/manual-link", methods=["POST"])
@require_auth
def openalex_metadata_manual_link():
    """Manually link a paper to a specific OpenAlex work record."""
    try:
        payload = parse_model(OpenAlexManualLinkRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    try:
        data = openalex_metadata_service.manual_link_openalex_for_paper(
            paper_ref=ref,
            openalex_api_url=str(payload.openalex_api_url or "").strip(),
            db_url=_db_url(),
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("openalex_manual_link_failed", f"OpenAlex manual link failed: {exc}", status=502)
    _log_mutation(
        "openalex.metadata.manual_link",
        paper_id=ref.paper_id,
        extra={
            "openalex_url": str(payload.openalex_api_url or ""),
            "openalex_id": str(data.get("openalex_id") or ""),
            "aliases_updated": int(data.get("aliases_updated") or 0),
        },
    )
    return _ok(data)


@api_bp.route("/openalex/citation-network", methods=["GET"])
@require_auth
def openalex_citation_network():
    """Return citation network data for a selected paper."""
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
    try:
        n_hops = int(request.args.get("n_hops") or 1)
    except Exception:
        n_hops = 1
    try:
        data = citation_network_service.citation_network_for_paper(
            ref,
            max_references=max_references,
            max_citing=max_citing,
            n_hops=n_hops,
        )
    except Exception as exc:
        return _err("citation_network_failed", f"Citation network request failed: {exc}", status=502)
    return _ok(data)


@api_bp.route("/cache/chat/inspect", methods=["GET"])
@require_auth
def cache_chat_inspect():
    """Inspect cached chat entries for the current paper and context."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    question = str(request.args.get("question") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    if not question:
        return _err("validation_error", "question is required.", status=422)
    model = str(request.args.get("model") or "").strip() or None
    top_k_raw = str(request.args.get("top_k") or "").strip()
    top_k: Optional[int] = None
    if top_k_raw:
        try:
            top_k = int(top_k_raw)
        except Exception:
            return _err("validation_error", "top_k must be an integer.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        data = cache_inspector_service.inspect_chat_cache(
            paper_ref=ref,
            question=question,
            model=model,
            top_k=top_k,
        )
    except Exception as exc:
        return _err("cache_chat_inspect_failed", f"Cache inspect failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/cache/structured/inspect", methods=["GET"])
@require_auth
def cache_structured_inspect():
    """Inspect cached structured-answer entries for the current paper and context."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    model = str(request.args.get("model") or "").strip() or None
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    try:
        data = cache_inspector_service.inspect_structured_cache(paper_ref=ref, model=model)
    except Exception as exc:
        return _err("cache_structured_inspect_failed", f"Structured cache inspect failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/chat/provenance-score", methods=["POST"])
@require_auth
def chat_provenance_score():
    """Compute a provenance score for a candidate chat answer."""
    try:
        payload = parse_model(ChatProvenanceScoreRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    try:
        data = provenance_service.score_answer_provenance(
            paper_ref=ref,
            question=payload.question,
            answer=payload.answer,
            citations=list(payload.citations or []),
        )
    except Exception as exc:
        return _err("provenance_scoring_failed", f"Provenance scoring failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/chat/turn", methods=["POST"])
@require_auth
def chat_turn():
    """Execute one non-streaming chat turn and return the full response payload."""
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
    context = _current_project_context()
    out = chat_service.chat_turn(
        paper_ref=ref,
        query=payload.question,
        model=payload.model,
        top_k=payload.top_k,
        session_id=str(getattr(g, "session_id", "") or ""),
        request_id=req_id,
        history=history,
        variation_mode=bool(payload.variation_mode),
        project_id=context.project_id,
        persona_id=context.persona_id,
        allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
        allow_custom_question_sharing=context.allow_custom_question_sharing,
        user_id=current_user.get("user_id"),
    )
    answer = str(out.get("answer") or "").strip()
    if answer:
        chat_history_service.append_turn(
            _db_url(),
            user_id=current_user.get("user_id"),
            username=str(current_user.get("username") or ""),
            project_id=context.project_id,
            persona_id=context.persona_id,
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
    _log_mutation(
        "chat.turn",
        paper_id=ref.paper_id,
        extra={
            "cache_hit": bool(out.get("cache_hit")),
            "cache_hit_layer": str(out.get("cache_hit_layer") or ""),
            "cache_scope": str(out.get("cache_scope") or ""),
            "variation_mode": bool(payload.variation_mode),
        },
    )
    return _ok(out)


@api_bp.route("/chat/turn-stream", methods=["POST"])
@require_auth
def chat_turn_stream():
    """Execute one streaming chat turn and emit NDJSON events."""
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
    context = _current_project_context()

    def stream_rows() -> Iterable[str]:
        """Handle stream rows."""
        persisted = False
        _log_mutation(
            "chat.turn_stream_start",
            paper_id=ref.paper_id,
            extra={"variation_mode": bool(payload.variation_mode)},
        )
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
                project_id=context.project_id,
                persona_id=context.persona_id,
                allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
                allow_custom_question_sharing=context.allow_custom_question_sharing,
                user_id=user_id,
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
                                    project_id=context.project_id,
                                    persona_id=context.persona_id,
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
                                _log_mutation(
                                    "chat.turn_stream_done",
                                    paper_id=ref.paper_id,
                                    extra={
                                        "cache_hit": bool(parsed.get("cache_hit")),
                                        "cache_hit_layer": str(parsed.get("cache_hit_layer") or ""),
                                        "cache_scope": str(parsed.get("cache_scope") or ""),
                                    },
                                )
                    except Exception:
                        pass
                yield row
        except Exception as exc:
            _log_mutation("chat.turn_stream_error", paper_id=ref.paper_id, extra={"error": str(exc)})
            payload_row = {
                "event": "error",
                "code": "chat_failed",
                "message": str(exc),
                "request_id": req_id,
            }
            yield json.dumps(payload_row, ensure_ascii=False) + "\n"

    return Response(stream_with_context(stream_rows()), mimetype="application/x-ndjson")


@api_bp.route("/chat/multi/turn", methods=["POST"])
@require_auth
def chat_multi_turn():
    """Execute one non-streaming multi-paper synthesis chat turn."""
    try:
        payload = parse_model(MultiChatTurnRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    current_user = dict(getattr(g, "current_user", {}) or {})
    limited = _rate_limit(
        subject_key=f"chat_multi:{current_user.get('user_id') or current_user.get('username')}",
        route="chat_multi_turn",
        limit_env="WEB_CHAT_RATE_LIMIT",
        window_env="WEB_CHAT_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited
    context = _current_project_context()
    req_id = _request_id()
    history = [item.model_dump() for item in payload.history]
    try:
        out = multi_paper_chat_service.multi_chat_turn(
            paper_ids=[str(item or "").strip() for item in list(payload.paper_ids or [])],
            question=payload.question,
            model=payload.model,
            top_k=payload.top_k,
            session_id=str(getattr(g, "session_id", "") or ""),
            request_id=req_id,
            history=history,
            project_id=context.project_id,
            persona_id=context.persona_id,
            allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
            allow_custom_question_sharing=context.allow_custom_question_sharing,
            user_id=current_user.get("user_id"),
            conversation_id=str(payload.conversation_id or "").strip() or None,
            seed_paper_id=str(payload.seed_paper_id or "").strip() or None,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("chat_multi_failed", f"Multi-paper chat failed: {exc}", status=500)

    paper_answers = [item for item in (out.get("paper_answers") or []) if isinstance(item, dict)]
    scope = out.get("scope") if isinstance(out.get("scope"), dict) else {}
    paper_ids = [str(v or "") for v in list(scope.get("paper_ids") or []) if str(v or "").strip()]
    paper_paths = [str((row or {}).get("paper_path") or "") for row in paper_answers if str((row or {}).get("paper_path") or "").strip()]
    conversation_id = multi_paper_chat_service.ensure_conversation(
        _db_url(),
        user_id=current_user.get("user_id"),
        username=str(current_user.get("username") or ""),
        project_id=context.project_id,
        persona_id=context.persona_id,
        session_id=str(getattr(g, "session_id", "") or ""),
        paper_ids=paper_ids,
        paper_paths=paper_paths,
        conversation_id=str(out.get("conversation_id") or "").strip() or None,
        seed_paper_id=str(scope.get("seed_paper_id") or "").strip() or None,
    )
    out["conversation_id"] = conversation_id
    answer = str(out.get("answer") or "").strip()
    if answer:
        multi_paper_chat_service.append_turn(
            _db_url(),
            conversation_id=conversation_id,
            user_id=current_user.get("user_id"),
            username=str(current_user.get("username") or ""),
            project_id=context.project_id,
            persona_id=context.persona_id,
            session_id=str(getattr(g, "session_id", "") or ""),
            model=str(out.get("model") or payload.model or ""),
            query=payload.question,
            answer=answer,
            paper_ids=paper_ids,
            paper_answers=paper_answers,
            comparison_summary=out.get("comparison_summary") if isinstance(out.get("comparison_summary"), dict) else {},
            aggregate_provenance=out.get("aggregate_provenance") if isinstance(out.get("aggregate_provenance"), dict) else {},
            suggested_papers=out.get("suggested_papers") if isinstance(out.get("suggested_papers"), dict) else {},
            request_id=req_id,
        )
    _log_mutation(
        "chat.multi_turn",
        extra={
            "conversation_id": conversation_id,
            "paper_count": len(paper_ids),
            "variation_mode_requested": bool(payload.variation_mode),
        },
    )
    return _ok(out)


@api_bp.route("/chat/multi/turn-stream", methods=["POST"])
@require_auth
def chat_multi_turn_stream():
    """Execute one streaming multi-paper synthesis chat turn and emit NDJSON."""
    try:
        payload = parse_model(MultiChatTurnRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    current_user = dict(getattr(g, "current_user", {}) or {})
    limited = _rate_limit(
        subject_key=f"chat_multi:{current_user.get('user_id') or current_user.get('username')}",
        route="chat_multi_turn_stream",
        limit_env="WEB_CHAT_RATE_LIMIT",
        window_env="WEB_CHAT_RATE_WINDOW_SECONDS",
    )
    if limited is not None:
        return limited
    context = _current_project_context()
    req_id = _request_id()
    session_id = str(getattr(g, "session_id", "") or "")
    history = [item.model_dump() for item in payload.history]
    username = str(current_user.get("username") or "")
    user_id = current_user.get("user_id")

    def stream_rows() -> Iterable[str]:
        """Handle multi-paper stream rows."""
        persisted = False
        _log_mutation(
            "chat.multi_turn_stream_start",
            extra={
                "paper_count": len(list(payload.paper_ids or [])),
                "variation_mode_requested": bool(payload.variation_mode),
            },
        )
        try:
            for row in multi_paper_chat_service.stream_multi_chat_turn(
                paper_ids=[str(item or "").strip() for item in list(payload.paper_ids or [])],
                question=payload.question,
                model=payload.model,
                top_k=payload.top_k,
                session_id=session_id,
                request_id=req_id,
                history=history,
                project_id=context.project_id,
                persona_id=context.persona_id,
                allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
                allow_custom_question_sharing=context.allow_custom_question_sharing,
                user_id=user_id,
                conversation_id=str(payload.conversation_id or "").strip() or None,
                seed_paper_id=str(payload.seed_paper_id or "").strip() or None,
            ):
                if not persisted:
                    try:
                        parsed = json.loads(str(row or "").strip())
                        if isinstance(parsed, dict) and str(parsed.get("event") or "") == "done":
                            scope = parsed.get("scope") if isinstance(parsed.get("scope"), dict) else {}
                            paper_answers = [item for item in (parsed.get("paper_answers") or []) if isinstance(item, dict)]
                            paper_ids = [str(v or "") for v in list(scope.get("paper_ids") or []) if str(v or "").strip()]
                            paper_paths = [
                                str((item or {}).get("paper_path") or "")
                                for item in paper_answers
                                if str((item or {}).get("paper_path") or "").strip()
                            ]
                            conversation_id = multi_paper_chat_service.ensure_conversation(
                                _db_url(),
                                user_id=user_id,
                                username=username,
                                project_id=context.project_id,
                                persona_id=context.persona_id,
                                session_id=session_id,
                                paper_ids=paper_ids,
                                paper_paths=paper_paths,
                                conversation_id=str(parsed.get("conversation_id") or "").strip() or None,
                                seed_paper_id=str(scope.get("seed_paper_id") or "").strip() or None,
                            )
                            parsed["conversation_id"] = conversation_id
                            answer = str(parsed.get("answer") or "").strip()
                            if answer:
                                multi_paper_chat_service.append_turn(
                                    _db_url(),
                                    conversation_id=conversation_id,
                                    user_id=user_id,
                                    username=username,
                                    project_id=context.project_id,
                                    persona_id=context.persona_id,
                                    session_id=session_id,
                                    model=str(parsed.get("model") or payload.model or ""),
                                    query=payload.question,
                                    answer=answer,
                                    paper_ids=paper_ids,
                                    paper_answers=paper_answers,
                                    comparison_summary=parsed.get("comparison_summary") if isinstance(parsed.get("comparison_summary"), dict) else {},
                                    aggregate_provenance=parsed.get("aggregate_provenance") if isinstance(parsed.get("aggregate_provenance"), dict) else {},
                                    suggested_papers=parsed.get("suggested_papers") if isinstance(parsed.get("suggested_papers"), dict) else {},
                                    request_id=req_id,
                                )
                            persisted = True
                            row = json.dumps(parsed, ensure_ascii=False) + "\n"
                            _log_mutation(
                                "chat.multi_turn_stream_done",
                                extra={
                                    "conversation_id": conversation_id,
                                    "paper_count": len(paper_ids),
                                },
                            )
                    except Exception:
                        pass
                yield row
        except ValueError as exc:
            payload_row = {
                "event": "error",
                "code": "validation_error",
                "message": str(exc),
                "request_id": req_id,
            }
            yield json.dumps(payload_row, ensure_ascii=False) + "\n"
        except Exception as exc:
            _log_mutation("chat.multi_turn_stream_error", extra={"error": str(exc)})
            payload_row = {
                "event": "error",
                "code": "chat_multi_failed",
                "message": str(exc),
                "request_id": req_id,
            }
            yield json.dumps(payload_row, ensure_ascii=False) + "\n"

    return Response(stream_with_context(stream_rows()), mimetype="application/x-ndjson")


@api_bp.route("/chat/suggestions", methods=["GET"])
@require_auth
def chat_suggestions():
    """Return suggested prompt starters for chat in the current context."""
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
    """Return persisted chat history for the current user and context."""
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
    context = _current_project_context()
    rows = chat_history_service.list_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        paper_id=paper_id,
        limit=limit,
    )
    return _ok({"paper_id": ref.paper_id, "rows": rows, "count": len(rows)})


@api_bp.route("/chat/history", methods=["DELETE"])
@require_auth
def chat_history_delete():
    """Delete persisted chat history for the current user and context."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    deleted_count = chat_history_service.clear_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        paper_id=paper_id,
    )
    _log_mutation("chat.history_clear", paper_id=ref.paper_id, extra={"deleted_count": int(deleted_count)})
    return _ok({"paper_id": ref.paper_id, "deleted_count": deleted_count})


@api_bp.route("/chat/multi/standard-questions", methods=["GET"])
@require_auth
def chat_multi_standard_questions():
    """Return deterministic standard prompts for multi-paper synthesis chat."""
    questions = multi_paper_chat_service.suggested_multi_paper_questions()
    return _ok({"questions": questions, "count": len(questions)})


@api_bp.route("/chat/multi/suggestions", methods=["GET"])
@require_auth
def chat_multi_suggestions():
    """Return companion paper suggestions for a selected multi-paper set."""
    paper_ids = _query_list_param("paper_ids")
    if len(paper_ids) < 2:
        return _err("validation_error", "At least 2 paper_ids are required.", status=422)
    context = _current_project_context()
    try:
        data = multi_paper_chat_service.suggest_companion_papers(
            paper_ids=paper_ids,
            project_id=context.project_id,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("chat_multi_suggestions_failed", f"Multi-paper suggestions failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/chat/multi/network", methods=["POST"])
@require_auth
def chat_multi_network():
    """Return selected-paper interaction graph data for multi-paper chat."""
    try:
        payload = parse_model(MultiChatNetworkRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    context = _current_project_context()
    try:
        data = multi_paper_chat_service.selected_paper_interaction_graph(
            paper_ids=list(payload.paper_ids or []),
            project_id=context.project_id,
            include_topic_edges=bool(payload.include_topic_edges),
            include_author_edges=bool(payload.include_author_edges),
            include_citation_edges=bool(payload.include_citation_edges),
            min_similarity=float(payload.min_similarity),
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), status=422)
    except Exception as exc:
        return _err("chat_multi_network_failed", f"Multi-paper network failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/chat/multi/history", methods=["GET"])
@require_auth
def chat_multi_history_get():
    """Return persisted multi-paper chat history for the current user and context."""
    paper_ids = _query_list_param("paper_ids")
    conversation_id = str(request.args.get("conversation_id") or "").strip() or None
    if not conversation_id and len(paper_ids) < 2:
        return _err("validation_error", "conversation_id or at least 2 paper_ids are required.", status=422)
    try:
        limit = int(request.args.get("limit") or 50)
    except Exception:
        limit = 50
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    data = multi_paper_chat_service.list_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        conversation_id=conversation_id,
        paper_ids=paper_ids or None,
        limit=limit,
    )
    return _ok(data)


@api_bp.route("/chat/multi/history", methods=["DELETE"])
@require_auth
def chat_multi_history_delete():
    """Delete persisted multi-paper chat history for the current user and context."""
    paper_ids = _query_list_param("paper_ids")
    conversation_id = str(request.args.get("conversation_id") or "").strip() or None
    if not conversation_id and len(paper_ids) < 2:
        return _err("validation_error", "conversation_id or at least 2 paper_ids are required.", status=422)
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    data = multi_paper_chat_service.clear_turns(
        _db_url(),
        user_id=user.get("user_id"),
        username=str(user.get("username") or ""),
        project_id=context.project_id,
        conversation_id=conversation_id,
        paper_ids=paper_ids or None,
    )
    _log_mutation(
        "chat.multi_history_clear",
        extra={
            "conversation_id": str(data.get("conversation_id") or ""),
            "deleted_count": int(data.get("deleted_count") or 0),
        },
    )
    return _ok(data)


@api_bp.route("/structured/questions", methods=["GET"])
@require_auth
def structured_questions():
    """Return the canonical structured question set for the current configuration."""
    return _ok({"questions": structured_service.structured_report_questions()})


@api_bp.route("/structured/answers", methods=["GET"])
@require_auth
def structured_answers():
    """Return cached structured answers for the selected paper."""
    paper_id = str(request.args.get("paper_id") or "").strip()
    if not paper_id:
        return _err("validation_error", "paper_id is required.", status=422)
    model = str(request.args.get("model") or "").strip() or None
    ref, error = _resolve_paper_or_404(paper_id)
    if error is not None:
        return error
    context = _current_project_context()
    answers = structured_service.db_workflow_structured_answers_for_paper(
        ref.path,
        model=model,
        project_id=context.project_id,
    )
    return _ok({"answers": answers, "count": len(answers)})


@api_bp.route("/structured/generate", methods=["POST"])
@require_auth
def structured_generate():
    """Generate structured answers for requested papers and questions."""
    try:
        payload = parse_model(StructuredGenerateRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    idem = str(request.headers.get("Idempotency-Key") or payload.idempotency_key or "").strip()
    context = _current_project_context()
    current_user = dict(getattr(g, "current_user", {}) or {})
    out = structured_service.generate_and_cache_structured_answer(
        paper_ref=ref,
        question_id=payload.question_id,
        category=payload.category,
        question=payload.question,
        selected_model=payload.model,
        session_id=str(getattr(g, "session_id", "") or ""),
        top_k=payload.top_k,
        idempotency_key=idem,
        project_id=context.project_id,
        persona_id=context.persona_id,
        allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
        allow_custom_question_sharing=context.allow_custom_question_sharing,
        user_id=current_user.get("user_id"),
    )
    _log_mutation(
        "structured.generate",
        paper_id=ref.paper_id,
        extra={"question_id": payload.question_id, "model": str(payload.model or "")},
    )
    return _ok(out)


@api_bp.route("/structured/generate-missing", methods=["POST"])
@require_auth
def structured_generate_missing():
    """Generate only missing structured answers in the selected scope."""
    try:
        payload = parse_model(StructuredGenerateMissingRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    context = _current_project_context()
    current_user = dict(getattr(g, "current_user", {}) or {})
    out = structured_service.generate_missing_structured_answers(
        paper_ref=ref,
        selected_model=payload.model,
        session_id=str(getattr(g, "session_id", "") or ""),
        top_k=payload.top_k,
        question_ids=list(payload.question_ids or []),
        project_id=context.project_id,
        persona_id=context.persona_id,
        allow_cross_project_answer_reuse=context.allow_cross_project_answer_reuse,
        allow_custom_question_sharing=context.allow_custom_question_sharing,
        user_id=current_user.get("user_id"),
    )
    _log_mutation(
        "structured.generate_missing",
        paper_id=ref.paper_id,
        extra={"generated_count": int(out.get("generated_count") or 0), "model": str(payload.model or "")},
    )
    return _ok(out)


@api_bp.route("/structured/export", methods=["POST"])
@require_auth
def structured_export():
    """Export structured answers using the requested export format and scope."""
    try:
        payload = parse_model(StructuredExportRequest, request.get_json(silent=True))
    except ValidationError as exc:
        return _err("validation_error", str(exc), status=422)
    ref, error = _resolve_paper_or_404(payload.paper_id)
    if error is not None:
        return error
    selected_model = str(payload.model or load_settings().chat_model)
    cache_scope = str(payload.cache_scope or "Selected model only")
    context = _current_project_context()
    bundle = structured_service.export_bundle_for_paper(
        paper_ref=ref,
        selected_model=selected_model,
        cache_scope=cache_scope,
        export_format=str(payload.export_format or "compact"),
        question_ids=list(payload.question_ids or []),
        project_id=context.project_id,
    )
    if str(payload.output or "json").lower() == "pdf":
        try:
            pdf_bytes = structured_service.structured_workstream_pdf_bytes(bundle)
        except Exception as exc:
            _log_mutation("structured.export_pdf_failed", paper_id=ref.paper_id, extra={"error": str(exc)})
            return _err("pdf_render_failed", f"PDF export failed: {exc}", status=500)
        if pdf_bytes is None:
            _log_mutation("structured.export_pdf_unavailable", paper_id=ref.paper_id)
            return _err("pdf_unavailable", "PDF export dependency is unavailable.", status=503)
        filename = f"structured-workstream-{ref.paper_id}-{bundle.get('export_format') or 'compact'}.pdf"
        response = Response(pdf_bytes, mimetype="application/pdf")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Request-Id"] = _request_id()
        _log_mutation(
            "structured.export_pdf",
            paper_id=ref.paper_id,
            extra={"export_format": str(bundle.get("export_format") or ""), "question_count": len(bundle.get("questions") or [])},
        )
        return response
    _log_mutation(
        "structured.export_json",
        paper_id=ref.paper_id,
        extra={"export_format": str(bundle.get("export_format") or ""), "question_count": len(bundle.get("questions") or [])},
    )
    return _ok(bundle)


@api_bp.route("/usage/summary", methods=["GET"])
@require_auth
def usage_summary():
    """Return aggregate usage metrics for the current account scope."""
    since = str(request.args.get("since") or "").strip() or None
    session_only = str(request.args.get("session_only") or "0").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    try:
        data = usage_service.usage_summary(
            session_id=session_id,
            since=since,
            account_user_id=user.get("user_id"),
            account_username=str(user.get("username") or ""),
            project_id=context.project_id,
        )
    except Exception as exc:
        return _err("usage_summary_failed", f"Usage summary failed: {exc}", status=500)
    return _ok(data)


@api_bp.route("/usage/by-model", methods=["GET"])
@require_auth
def usage_by_model():
    """Return usage metrics grouped by model for the current scope."""
    since = str(request.args.get("since") or "").strip() or None
    session_only = str(request.args.get("session_only") or "0").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    try:
        rows = usage_service.usage_by_model(
            session_id=session_id,
            since=since,
            account_user_id=user.get("user_id"),
            account_username=str(user.get("username") or ""),
            project_id=context.project_id,
        )
    except Exception as exc:
        return _err("usage_by_model_failed", f"Usage by-model failed: {exc}", status=500)
    return _ok({"rows": rows, "count": len(rows)})


@api_bp.route("/usage/recent", methods=["GET"])
@require_auth
def usage_recent():
    """Return recent usage events for the current account scope."""
    try:
        limit = int(request.args.get("limit") or 200)
    except Exception:
        limit = 200
    limit = max(1, min(1000, limit))
    session_only = str(request.args.get("session_only") or "0").strip().lower() not in {"0", "false", "no"}
    session_id = str(getattr(g, "session_id", "") or "") if session_only else None
    user = dict(getattr(g, "current_user", {}) or {})
    context = _current_project_context()
    try:
        rows = usage_service.recent_usage(
            limit=limit,
            session_id=session_id,
            account_user_id=user.get("user_id"),
            account_username=str(user.get("username") or ""),
            project_id=context.project_id,
        )
    except Exception as exc:
        return _err("usage_recent_failed", f"Usage recent failed: {exc}", status=500)
    return _ok({"rows": rows, "count": len(rows)})
