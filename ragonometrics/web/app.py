"""Flask app factory for Ragonometrics web API + SPA."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, g, request, send_from_directory
from pydantic import ValidationError

from ragonometrics.db.connection import connect
from ragonometrics.indexing import metadata
from ragonometrics.web.api import api_bp


def _record_request_failure(component: str, error: str, context: dict[str, Any]) -> None:
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return
    try:
        conn = connect(db_url, require_migrated=True)
        metadata.record_failure(conn, component, error, context=context)
        conn.close()
    except Exception:
        return


def create_app() -> Flask:
    """Create and configure the Flask app."""
    package_dir = Path(__file__).resolve().parent
    static_dir = package_dir / "static"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/")
    app.json.sort_keys = False
    app.config["WEB_SESSION_COOKIE_NAME"] = os.getenv("WEB_SESSION_COOKIE_NAME", "rag_session")
    app.config["WEB_CSRF_COOKIE_NAME"] = os.getenv("WEB_CSRF_COOKIE_NAME", "rag_csrf")
    app.config["WEB_COOKIE_SECURE"] = os.getenv("WEB_COOKIE_SECURE", "0")
    app.config["WEB_COOKIE_SAMESITE"] = os.getenv("WEB_COOKIE_SAMESITE", "Lax")
    app.config["WEB_SESSION_MAX_AGE_SECONDS"] = int(os.getenv("WEB_SESSION_MAX_AGE_SECONDS", str(60 * 60 * 12)))

    @app.before_request
    def attach_request_id():
        req_id = str(request.headers.get("X-Request-Id") or "").strip() or uuid4().hex
        g.request_id = req_id

    @app.after_request
    def attach_headers(response):
        response.headers["X-Request-Id"] = str(getattr(g, "request_id", "") or "")
        return response

    @app.errorhandler(ValidationError)
    def handle_validation_error(exc: ValidationError):
        _record_request_failure(
            "web_api_validation",
            str(exc),
            {"request_id": getattr(g, "request_id", ""), "path": request.path, "method": request.method},
        )
        return (
            {
                "ok": False,
                "error": {"code": "validation_error", "message": str(exc)},
                "request_id": str(getattr(g, "request_id", "") or ""),
            },
            422,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        _record_request_failure(
            "web_api",
            str(exc),
            {
                "request_id": getattr(g, "request_id", ""),
                "path": request.path,
                "method": request.method,
                "query": dict(request.args or {}),
            },
        )
        return (
            {
                "ok": False,
                "error": {"code": "internal_error", "message": "Internal server error."},
                "request_id": str(getattr(g, "request_id", "") or ""),
            },
            500,
        )

    app.register_blueprint(api_bp)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "status": "healthy"}

    @app.get("/")
    def index():
        index_path = static_dir / "index.html"
        if index_path.exists():
            return send_from_directory(static_dir, "index.html")
        return {
            "ok": True,
            "message": "Web UI assets not built yet. Build webapp/ and copy assets to ragonometrics/web/static.",
        }

    @app.get("/<path:path>")
    def spa(path: str):
        if path.startswith("api/"):
            return {"ok": False, "error": {"code": "not_found", "message": "Not found"}}, 404
        file_path = static_dir / path
        if file_path.exists() and file_path.is_file():
            return send_from_directory(static_dir, path)
        index_path = static_dir / "index.html"
        if index_path.exists():
            return send_from_directory(static_dir, "index.html")
        return {"ok": False, "error": {"code": "not_found", "message": "Not found"}}, 404

    return app


def main() -> None:
    """Run a local Flask dev server."""
    app = create_app()
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "8590"))
    debug = str(os.getenv("WEB_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
