#!/usr/bin/env python
"""Web smoke script that exercises core tab actions and fails on 5xx responses."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

import requests


def _die(message: str, *, code: int = 1) -> int:
    print(f"[error] {message}", file=sys.stderr)
    return int(code)


def _request_json(
    session: requests.Session,
    *,
    method: str,
    url: str,
    csrf_token: str = "",
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    if csrf_token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["X-CSRF-Token"] = csrf_token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    resp = session.request(
        method=method.upper(),
        url=url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    content_type = str(resp.headers.get("content-type") or "")
    if int(resp.status_code) >= 500:
        raise RuntimeError(f"server_5xx status={resp.status_code} path={url} body={str(resp.text or '')[:240]}")
    if "application/json" not in content_type.lower():
        raise RuntimeError(f"unexpected_content_type status={resp.status_code} path={url} type={content_type}")
    data = resp.json() if resp.text else {}
    if int(resp.status_code) >= 400:
        message = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("code") or "")
        raise RuntimeError(f"http_error status={resp.status_code} path={url} message={message}")
    if not isinstance(data, dict) or not bool(data.get("ok")):
        raise RuntimeError(f"bad_payload path={url}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check core web actions; fails fast on 5xx.")
    parser.add_argument("--base-url", type=str, default="http://localhost:8590")
    parser.add_argument("--identifier", type=str, required=True)
    parser.add_argument("--password", type=str, required=True)
    parser.add_argument("--paper-id", type=str, default="")
    parser.add_argument("--question", type=str, default="What is the main research question of this paper?")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    base_url = str(args.base_url or "").rstrip("/")
    if not base_url:
        return _die("base URL is required.")

    with requests.Session() as session:
        print("[step] login")
        login = _request_json(
            session,
            method="POST",
            url=f"{base_url}/api/v1/auth/login",
            payload={"identifier": args.identifier, "password": args.password},
            timeout=float(args.timeout),
        )
        login_data = login.get("data") if isinstance(login, dict) else {}
        csrf = str((login_data or {}).get("csrf_token") or "").strip()
        if not csrf:
            return _die("csrf token missing from login response.")

        print("[step] papers")
        papers = _request_json(session, method="GET", url=f"{base_url}/api/v1/papers", timeout=float(args.timeout))
        rows = ((papers.get("data") or {}).get("papers") if isinstance(papers, dict) else []) or []
        if not isinstance(rows, list) or not rows:
            return _die("no papers returned.")
        paper_id = str(args.paper_id or "").strip() or str((rows[0] or {}).get("paper_id") or "").strip()
        if not paper_id:
            return _die("paper_id unavailable from papers endpoint.")

        print("[step] paper detail + content")
        _request_json(session, method="GET", url=f"{base_url}/api/v1/papers/{paper_id}", timeout=float(args.timeout))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/papers/{paper_id}/overview", timeout=float(args.timeout))
        content = session.get(f"{base_url}/api/v1/papers/{paper_id}/content", timeout=float(args.timeout))
        if int(content.status_code) >= 500:
            return _die(f"paper content 5xx status={content.status_code}")

        print("[step] chat tab actions")
        _request_json(session, method="GET", url=f"{base_url}/api/v1/chat/suggestions?paper_id={paper_id}", timeout=float(args.timeout))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/chat/history?paper_id={paper_id}&limit=20", timeout=float(args.timeout))
        chat = _request_json(
            session,
            method="POST",
            url=f"{base_url}/api/v1/chat/turn",
            csrf_token=csrf,
            payload={"paper_id": paper_id, "question": args.question},
            timeout=float(args.timeout),
        )
        chat_data = chat.get("data") if isinstance(chat, dict) else {}
        _request_json(
            session,
            method="POST",
            url=f"{base_url}/api/v1/chat/provenance-score",
            csrf_token=csrf,
            payload={
                "paper_id": paper_id,
                "question": args.question,
                "answer": str((chat_data or {}).get("answer") or "No answer"),
                "citations": (chat_data or {}).get("citations") if isinstance(chat_data, dict) else [],
            },
            timeout=float(args.timeout),
        )

        print("[step] notes CRUD")
        created = _request_json(
            session,
            method="POST",
            url=f"{base_url}/api/v1/papers/{paper_id}/notes",
            csrf_token=csrf,
            payload={
                "paper_id": paper_id,
                "note_text": "Smoke note",
                "page_number": 1,
                "highlight_text": "smoke",
                "highlight_terms": ["smoke"],
            },
            timeout=float(args.timeout),
        )
        note_id = int(((created.get("data") or {}).get("id") or 0))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/papers/{paper_id}/notes?page=1", timeout=float(args.timeout))
        if note_id > 0:
            _request_json(
                session,
                method="DELETE",
                url=f"{base_url}/api/v1/papers/{paper_id}/notes/{note_id}",
                csrf_token=csrf,
                timeout=float(args.timeout),
            )

        print("[step] structured tab actions")
        _request_json(session, method="GET", url=f"{base_url}/api/v1/structured/questions", timeout=float(args.timeout))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/structured/answers?paper_id={paper_id}", timeout=float(args.timeout))
        _request_json(
            session,
            method="POST",
            url=f"{base_url}/api/v1/structured/export",
            csrf_token=csrf,
            payload={"paper_id": paper_id, "export_format": "compact", "output": "json"},
            timeout=float(args.timeout),
        )

        print("[step] cache inspector actions")
        _request_json(
            session,
            method="GET",
            url=f"{base_url}/api/v1/cache/chat/inspect?paper_id={paper_id}&question={requests.utils.quote(args.question)}",
            timeout=float(args.timeout),
        )
        _request_json(
            session,
            method="GET",
            url=f"{base_url}/api/v1/cache/structured/inspect?paper_id={paper_id}",
            timeout=float(args.timeout),
        )

        print("[step] metadata/network tabs")
        _request_json(session, method="GET", url=f"{base_url}/api/v1/openalex/metadata?paper_id={paper_id}", timeout=float(args.timeout))
        _request_json(
            session,
            method="GET",
            url=f"{base_url}/api/v1/openalex/citation-network?paper_id={paper_id}&max_references=10&max_citing=10",
            timeout=float(args.timeout),
        )

        print("[step] usage tab")
        _request_json(session, method="GET", url=f"{base_url}/api/v1/usage/summary?session_only=0", timeout=float(args.timeout))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/usage/by-model?session_only=0", timeout=float(args.timeout))
        _request_json(session, method="GET", url=f"{base_url}/api/v1/usage/recent?session_only=0&limit=50", timeout=float(args.timeout))

        print("[step] workflow cache tab")
        runs = _request_json(
            session,
            method="GET",
            url=f"{base_url}/api/v1/workflow/runs?paper_id={paper_id}&limit=5",
            timeout=float(args.timeout),
        )
        run_rows = ((runs.get("data") or {}).get("runs") if isinstance(runs, dict) else []) or []
        if isinstance(run_rows, list) and run_rows:
            run_id = str((run_rows[0] or {}).get("run_id") or "")
            if run_id:
                _request_json(
                    session,
                    method="GET",
                    url=f"{base_url}/api/v1/workflow/runs/{run_id}/steps?paper_id={paper_id}&include_internals=1",
                    timeout=float(args.timeout),
                )

        print("[step] logout")
        _request_json(session, method="POST", url=f"{base_url}/api/v1/auth/logout", csrf_token=csrf, timeout=float(args.timeout))

    print(json.dumps({"ok": True, "paper_id": paper_id, "base_url": base_url}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

