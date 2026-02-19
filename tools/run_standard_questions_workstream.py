#!/usr/bin/env python
"""Run six standard chat questions + full structured workstream for all papers via web API.

The script verifies success from web-facing cache behavior:
- chat verification checks that each standard question returns `cache_hit=true`
  on a second pass and that `/cache/chat/inspect` reports a cached layer.
- structured verification checks `/cache/structured/inspect` reports full
  coverage for the canonical structured question set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote_plus

import requests


STANDARD_QUESTIONS: List[str] = [
    "What is the main research question of this paper?",
    "What identification strategy does the paper use?",
    "What dataset and sample period are used?",
    "What are the key quantitative findings?",
    "What are the main limitations and caveats?",
    "What policy implications follow from the results?",
]


class ApiError(RuntimeError):
    """HTTP/API error raised by `_request_json` helpers."""

    def __init__(self, *, status: int, code: str, message: str, url: str) -> None:
        self.status = int(status or 0)
        self.code = str(code or "").strip()
        self.message = str(message or "").strip()
        self.url = str(url or "").strip()
        suffix = f" code={self.code}" if self.code else ""
        super().__init__(f"status={self.status}{suffix} url={self.url} message={self.message}")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the cache warm/verify run."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the six standard chat questions + full structured workstream for all papers, "
            "then verify cache hits through web API endpoints."
        )
    )
    parser.add_argument("--base-url", type=str, default=os.environ.get("RAGONOMETRICS_WEB_BASE_URL", "http://localhost:8590"))
    parser.add_argument(
        "--identifier",
        type=str,
        default=os.environ.get("RAGONOMETRICS_WEB_IDENTIFIER") or os.environ.get("WEB_IDENTIFIER", ""),
        help="Web username/email. Defaults to RAGONOMETRICS_WEB_IDENTIFIER or WEB_IDENTIFIER.",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=os.environ.get("RAGONOMETRICS_WEB_PASSWORD") or os.environ.get("WEB_PASSWORD", ""),
        help="Web password. Defaults to RAGONOMETRICS_WEB_PASSWORD or WEB_PASSWORD.",
    )
    parser.add_argument("--model", type=str, default="", help="Optional model override for chat/structured calls.")
    parser.add_argument("--top-k", type=int, default=0, help="Optional retrieval top_k override (>0 to enable).")
    parser.add_argument(
        "--paper-filter",
        type=str,
        default="",
        help="Optional case-insensitive filter on paper display title/name/path.",
    )
    parser.add_argument("--max-papers", type=int, default=0, help="Optional maximum number of papers to process (>0).")
    parser.add_argument(
        "--structured-batch-size",
        type=int,
        default=8,
        help="Batch size for structured `generate-missing` requests (default: 8).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=90.0,
        help="Default timeout seconds for most API calls (default: 90).",
    )
    parser.add_argument(
        "--structured-timeout",
        type=float,
        default=240.0,
        help="Timeout seconds for structured generation calls (default: 240).",
    )
    parser.add_argument(
        "--chat-delay-seconds",
        type=float,
        default=2.2,
        help="Delay between chat POST calls to avoid rate-limit bursts (default: 2.2).",
    )
    parser.add_argument(
        "--rate-limit-retries",
        type=int,
        default=12,
        help="Max retries for 429 responses on chat calls (default: 12).",
    )
    parser.add_argument(
        "--rate-limit-sleep-seconds",
        type=float,
        default=5.0,
        help="Sleep between 429 retries (default: 5).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/misc/web-cache-standard-questions-workstream.json"),
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for HTTPS URLs.",
    )
    return parser.parse_args()


def _request_json(
    session: requests.Session,
    *,
    method: str,
    url: str,
    timeout: float,
    verify_tls: bool,
    payload: Optional[Dict[str, Any]] = None,
    csrf_token: str = "",
) -> Dict[str, Any]:
    """Send one request and return parsed `{ok,data,error}` JSON payload."""
    headers: Dict[str, str] = {}
    method_up = str(method or "GET").upper()
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if csrf_token and method_up in {"POST", "PUT", "PATCH", "DELETE"}:
        headers["X-CSRF-Token"] = csrf_token

    response = session.request(
        method=method_up,
        url=url,
        json=payload,
        headers=headers,
        timeout=float(timeout),
        verify=bool(verify_tls),
    )
    content_type = str(response.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        snippet = str(response.text or "").strip().replace("\n", " ")[:220]
        raise ApiError(
            status=int(response.status_code),
            code="unexpected_content_type",
            message=f"type={content_type or 'unknown'} body={snippet}",
            url=url,
        )
    try:
        body = response.json()
    except Exception as exc:
        raise ApiError(
            status=int(response.status_code),
            code="invalid_json",
            message=f"failed to parse json: {exc}",
            url=url,
        ) from exc
    if not isinstance(body, dict):
        raise ApiError(
            status=int(response.status_code),
            code="bad_payload",
            message="response is not an object",
            url=url,
        )
    if int(response.status_code) >= 400 or not bool(body.get("ok")):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        raise ApiError(
            status=int(response.status_code),
            code=str(err.get("code") or ""),
            message=str(err.get("message") or "") or f"http_{int(response.status_code)}",
            url=url,
        )
    return body


def _request_json_retry_429(
    session: requests.Session,
    *,
    method: str,
    url: str,
    timeout: float,
    verify_tls: bool,
    payload: Optional[Dict[str, Any]] = None,
    csrf_token: str = "",
    retries: int,
    sleep_seconds: float,
) -> Dict[str, Any]:
    """Send one JSON request and retry 429 responses."""
    max_attempts = max(1, int(retries) + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            return _request_json(
                session,
                method=method,
                url=url,
                timeout=timeout,
                verify_tls=verify_tls,
                payload=payload,
                csrf_token=csrf_token,
            )
        except ApiError as exc:
            if exc.status != 429 or attempt >= max_attempts:
                raise
            time.sleep(max(0.0, float(sleep_seconds)))
    raise RuntimeError("unreachable")


def _chunked(values: Sequence[str], chunk_size: int) -> Iterable[List[str]]:
    """Yield fixed-size chunks from one sequence."""
    size = max(1, int(chunk_size))
    for idx in range(0, len(values), size):
        yield list(values[idx : idx + size])


def _paper_text(row: Dict[str, Any]) -> str:
    """Return one searchable lower-case text blob for a paper row."""
    return " ".join(
        [
            str(row.get("paper_id") or ""),
            str(row.get("display_title") or ""),
            str(row.get("title") or ""),
            str(row.get("name") or ""),
            str(row.get("path") or ""),
        ]
    ).lower()


def _build_chat_payload(*, paper_id: str, question: str, model: str, top_k: int) -> Dict[str, Any]:
    """Build one web chat payload with deterministic fields."""
    payload: Dict[str, Any] = {
        "paper_id": str(paper_id or "").strip(),
        "question": str(question or "").strip(),
        "history": [],
        "variation_mode": False,
    }
    if str(model or "").strip():
        payload["model"] = str(model).strip()
    if int(top_k or 0) > 0:
        payload["top_k"] = int(top_k)
    return payload


def _build_structured_payload(*, paper_id: str, question_ids: List[str], model: str, top_k: int) -> Dict[str, Any]:
    """Build one structured generate-missing payload."""
    payload: Dict[str, Any] = {
        "paper_id": str(paper_id or "").strip(),
        "question_ids": list(question_ids),
    }
    if str(model or "").strip():
        payload["model"] = str(model).strip()
    if int(top_k or 0) > 0:
        payload["top_k"] = int(top_k)
    return payload


def _chat_inspect_url(*, base_url: str, paper_id: str, question: str, model: str, top_k: int) -> str:
    """Build `/cache/chat/inspect` URL for one question/paper pair."""
    url = f"{base_url}/api/v1/cache/chat/inspect?paper_id={quote_plus(paper_id)}&question={quote_plus(question)}"
    if str(model or "").strip():
        url += f"&model={quote_plus(model)}"
    if int(top_k or 0) > 0:
        url += f"&top_k={int(top_k)}"
    return url


def _structured_inspect_url(*, base_url: str, paper_id: str, model: str) -> str:
    """Build `/cache/structured/inspect` URL for one paper."""
    url = f"{base_url}/api/v1/cache/structured/inspect?paper_id={quote_plus(paper_id)}"
    if str(model or "").strip():
        url += f"&model={quote_plus(model)}"
    return url


def main() -> int:
    """Run the full standard-questions + structured workflow and verify cache behavior."""
    args = parse_args()
    base_url = str(args.base_url or "").strip().rstrip("/")
    identifier = str(args.identifier or "").strip()
    password = str(args.password or "").strip()
    model = str(args.model or "").strip()
    top_k = int(args.top_k or 0)
    verify_tls = not bool(args.insecure)
    started_at = time.time()

    if not base_url:
        print("[error] --base-url is required.", file=sys.stderr)
        return 1
    if not identifier or not password:
        print(
            "[error] missing credentials. Provide --identifier/--password or "
            "RAGONOMETRICS_WEB_IDENTIFIER + RAGONOMETRICS_WEB_PASSWORD.",
            file=sys.stderr,
        )
        return 1

    report: Dict[str, Any] = {
        "ok": False,
        "started_at": started_at,
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "config": {
            "base_url": base_url,
            "model": model,
            "top_k": top_k if top_k > 0 else None,
            "structured_batch_size": int(args.structured_batch_size),
            "request_timeout": float(args.request_timeout),
            "structured_timeout": float(args.structured_timeout),
            "chat_delay_seconds": float(args.chat_delay_seconds),
            "rate_limit_retries": int(args.rate_limit_retries),
            "rate_limit_sleep_seconds": float(args.rate_limit_sleep_seconds),
            "paper_filter": str(args.paper_filter or ""),
            "max_papers": int(args.max_papers or 0),
            "verify_tls": bool(verify_tls),
            "standard_questions": list(STANDARD_QUESTIONS),
        },
        "summary": {},
        "papers": [],
        "errors": [],
    }

    csrf = ""
    structured_question_ids: List[str] = []
    expected_structured_question_count = 0

    with requests.Session() as session:
        try:
            print("[step] login")
            login = _request_json(
                session,
                method="POST",
                url=f"{base_url}/api/v1/auth/login",
                timeout=float(args.request_timeout),
                verify_tls=verify_tls,
                payload={"identifier": identifier, "password": password},
            )
            login_data = login.get("data") if isinstance(login.get("data"), dict) else {}
            csrf = str((login_data or {}).get("csrf_token") or "").strip()
            if not csrf:
                raise RuntimeError("csrf token missing from login response")

            print("[step] list papers")
            papers_out = _request_json(
                session,
                method="GET",
                url=f"{base_url}/api/v1/papers",
                timeout=float(args.request_timeout),
                verify_tls=verify_tls,
            )
            papers_rows = (papers_out.get("data") or {}).get("papers")
            papers = papers_rows if isinstance(papers_rows, list) else []
            if not papers:
                raise RuntimeError("no papers returned from /api/v1/papers")

            paper_filter = str(args.paper_filter or "").strip().lower()
            if paper_filter:
                papers = [row for row in papers if paper_filter in _paper_text(row)]
            if int(args.max_papers or 0) > 0:
                papers = papers[: int(args.max_papers)]
            if not papers:
                raise RuntimeError("no papers matched the provided filters")

            print("[step] load structured question catalog")
            sq = _request_json(
                session,
                method="GET",
                url=f"{base_url}/api/v1/structured/questions",
                timeout=float(args.request_timeout),
                verify_tls=verify_tls,
            )
            sq_rows = (sq.get("data") or {}).get("questions")
            sq_items = sq_rows if isinstance(sq_rows, list) else []
            structured_question_ids = [str(item.get("id") or "").strip() for item in sq_items if str(item.get("id") or "").strip()]
            expected_structured_question_count = len(structured_question_ids)
            if not structured_question_ids:
                raise RuntimeError("structured question list is empty")

            print(f"[step] processing {len(papers)} paper(s)")
            for index, paper in enumerate(papers, start=1):
                paper_id = str(paper.get("paper_id") or "").strip()
                if not paper_id:
                    report["errors"].append({"paper_index": index, "error": "missing paper_id in papers list row"})
                    continue
                paper_title = str(paper.get("display_title") or paper.get("title") or paper.get("name") or paper_id)
                print(f"[paper {index}/{len(papers)}] {paper_title} ({paper_id})")
                paper_record: Dict[str, Any] = {
                    "paper_id": paper_id,
                    "paper_title": paper_title,
                    "structured": {
                        "expected_question_count": expected_structured_question_count,
                        "generated_count": 0,
                        "skipped_cached_count": 0,
                        "batch_count": 0,
                        "coverage_ratio": 0.0,
                        "cached_questions": 0,
                        "missing_questions": expected_structured_question_count,
                        "coverage_ok": False,
                        "coverage_total_matches_catalog": False,
                    },
                    "chat": {
                        "questions": [],
                        "all_cached_on_verify": False,
                    },
                    "ok": False,
                    "errors": [],
                }

                # 1) Full structured workstream (batched generate-missing).
                for batch in _chunked(structured_question_ids, int(args.structured_batch_size)):
                    payload = _build_structured_payload(
                        paper_id=paper_id,
                        question_ids=batch,
                        model=model,
                        top_k=top_k,
                    )
                    try:
                        out = _request_json(
                            session,
                            method="POST",
                            url=f"{base_url}/api/v1/structured/generate-missing",
                            timeout=float(args.structured_timeout),
                            verify_tls=verify_tls,
                            payload=payload,
                            csrf_token=csrf,
                        )
                        data = out.get("data") if isinstance(out.get("data"), dict) else {}
                        paper_record["structured"]["generated_count"] += int(data.get("generated_count") or 0)
                        paper_record["structured"]["skipped_cached_count"] += int(data.get("skipped_cached_count") or 0)
                        paper_record["structured"]["batch_count"] += 1
                    except Exception as exc:
                        msg = f"structured_generate_missing_failed batch_size={len(batch)} error={exc}"
                        paper_record["errors"].append(msg)
                        report["errors"].append({"paper_id": paper_id, "step": "structured_generate_missing", "error": str(exc)})
                        break

                # Structured coverage verification via web cache-inspect endpoint.
                try:
                    inspect_url = _structured_inspect_url(base_url=base_url, paper_id=paper_id, model=model)
                    inspect_out = _request_json(
                        session,
                        method="GET",
                        url=inspect_url,
                        timeout=float(args.request_timeout),
                        verify_tls=verify_tls,
                    )
                    inspect_data = inspect_out.get("data") if isinstance(inspect_out.get("data"), dict) else {}
                    total_questions = int(inspect_data.get("total_questions") or 0)
                    cached_questions = int(inspect_data.get("cached_questions") or 0)
                    missing_questions = int(inspect_data.get("missing_questions") or max(0, total_questions - cached_questions))
                    coverage_ratio = float(inspect_data.get("coverage_ratio") or 0.0)
                    total_match = total_questions == expected_structured_question_count
                    coverage_ok = total_match and missing_questions == 0 and cached_questions >= total_questions
                    paper_record["structured"].update(
                        {
                            "total_questions": total_questions,
                            "cached_questions": cached_questions,
                            "missing_questions": missing_questions,
                            "coverage_ratio": coverage_ratio,
                            "coverage_ok": coverage_ok,
                            "coverage_total_matches_catalog": total_match,
                            "missing_question_ids": list(inspect_data.get("missing_question_ids") or []),
                        }
                    )
                    if not coverage_ok:
                        paper_record["errors"].append(
                            "structured_coverage_failed "
                            f"(total={total_questions}, expected={expected_structured_question_count}, "
                            f"cached={cached_questions}, missing={missing_questions})"
                        )
                except Exception as exc:
                    paper_record["errors"].append(f"structured_inspect_failed error={exc}")
                    report["errors"].append({"paper_id": paper_id, "step": "structured_inspect", "error": str(exc)})

                # 2) Six standard questions, then verify second pass is cached.
                for question in STANDARD_QUESTIONS:
                    chat_row: Dict[str, Any] = {
                        "question": question,
                        "prime_cache_hit": None,
                        "prime_cache_hit_layer": "",
                        "verify_cache_hit": None,
                        "verify_cache_hit_layer": "",
                        "inspect_selected_layer": "",
                        "inspect_strict_hit": None,
                        "inspect_fallback_hit": None,
                        "ok": False,
                        "error": "",
                    }
                    payload = _build_chat_payload(
                        paper_id=paper_id,
                        question=question,
                        model=model,
                        top_k=top_k,
                    )
                    try:
                        prime = _request_json_retry_429(
                            session,
                            method="POST",
                            url=f"{base_url}/api/v1/chat/turn",
                            timeout=float(args.request_timeout),
                            verify_tls=verify_tls,
                            payload=payload,
                            csrf_token=csrf,
                            retries=int(args.rate_limit_retries),
                            sleep_seconds=float(args.rate_limit_sleep_seconds),
                        )
                        prime_data = prime.get("data") if isinstance(prime.get("data"), dict) else {}
                        chat_row["prime_cache_hit"] = bool(prime_data.get("cache_hit")) if isinstance(prime_data.get("cache_hit"), bool) else None
                        chat_row["prime_cache_hit_layer"] = str(prime_data.get("cache_hit_layer") or "")
                    except Exception as exc:
                        chat_row["error"] = f"chat_prime_failed: {exc}"
                        paper_record["errors"].append(chat_row["error"])
                        report["errors"].append({"paper_id": paper_id, "step": "chat_prime", "question": question, "error": str(exc)})
                        paper_record["chat"]["questions"].append(chat_row)
                        continue

                    if float(args.chat_delay_seconds) > 0:
                        time.sleep(float(args.chat_delay_seconds))

                    try:
                        verify = _request_json_retry_429(
                            session,
                            method="POST",
                            url=f"{base_url}/api/v1/chat/turn",
                            timeout=float(args.request_timeout),
                            verify_tls=verify_tls,
                            payload=payload,
                            csrf_token=csrf,
                            retries=int(args.rate_limit_retries),
                            sleep_seconds=float(args.rate_limit_sleep_seconds),
                        )
                        verify_data = verify.get("data") if isinstance(verify.get("data"), dict) else {}
                        chat_row["verify_cache_hit"] = (
                            bool(verify_data.get("cache_hit")) if isinstance(verify_data.get("cache_hit"), bool) else None
                        )
                        chat_row["verify_cache_hit_layer"] = str(verify_data.get("cache_hit_layer") or "")
                    except Exception as exc:
                        chat_row["error"] = f"chat_verify_failed: {exc}"
                        paper_record["errors"].append(chat_row["error"])
                        report["errors"].append({"paper_id": paper_id, "step": "chat_verify", "question": question, "error": str(exc)})
                        paper_record["chat"]["questions"].append(chat_row)
                        continue

                    if float(args.chat_delay_seconds) > 0:
                        time.sleep(float(args.chat_delay_seconds))

                    try:
                        inspect = _request_json(
                            session,
                            method="GET",
                            url=_chat_inspect_url(
                                base_url=base_url,
                                paper_id=paper_id,
                                question=question,
                                model=model,
                                top_k=top_k,
                            ),
                            timeout=float(args.request_timeout),
                            verify_tls=verify_tls,
                        )
                        inspect_data = inspect.get("data") if isinstance(inspect.get("data"), dict) else {}
                        chat_row["inspect_selected_layer"] = str(inspect_data.get("selected_layer") or "")
                        chat_row["inspect_strict_hit"] = (
                            bool(inspect_data.get("strict_hit")) if isinstance(inspect_data.get("strict_hit"), bool) else None
                        )
                        chat_row["inspect_fallback_hit"] = (
                            bool(inspect_data.get("fallback_hit")) if isinstance(inspect_data.get("fallback_hit"), bool) else None
                        )
                    except Exception as exc:
                        chat_row["error"] = f"chat_inspect_failed: {exc}"
                        paper_record["errors"].append(chat_row["error"])
                        report["errors"].append({"paper_id": paper_id, "step": "chat_inspect", "question": question, "error": str(exc)})
                        paper_record["chat"]["questions"].append(chat_row)
                        continue

                    verify_hit = chat_row["verify_cache_hit"] is True
                    inspect_hit = chat_row["inspect_selected_layer"] in {"strict", "fallback"}
                    chat_row["ok"] = bool(verify_hit and inspect_hit)
                    if not chat_row["ok"] and not chat_row["error"]:
                        chat_row["error"] = (
                            "chat_cache_verification_failed "
                            f"(verify_cache_hit={chat_row['verify_cache_hit']}, "
                            f"verify_layer={chat_row['verify_cache_hit_layer']}, "
                            f"inspect_layer={chat_row['inspect_selected_layer']})"
                        )
                        paper_record["errors"].append(chat_row["error"])
                    paper_record["chat"]["questions"].append(chat_row)

                chat_rows = paper_record["chat"]["questions"]
                chat_ok = bool(chat_rows) and all(bool(item.get("ok")) for item in chat_rows)
                paper_record["chat"]["all_cached_on_verify"] = chat_ok
                structured_ok = bool(paper_record["structured"].get("coverage_ok"))
                paper_record["ok"] = bool(chat_ok and structured_ok and not paper_record["errors"])
                report["papers"].append(paper_record)

            # Best-effort logout.
            try:
                _request_json(
                    session,
                    method="POST",
                    url=f"{base_url}/api/v1/auth/logout",
                    timeout=float(args.request_timeout),
                    verify_tls=verify_tls,
                    csrf_token=csrf,
                )
            except Exception:
                pass

        except Exception as exc:
            report["errors"].append({"step": "fatal", "error": str(exc)})

    total_papers = len(report["papers"])
    ok_papers = sum(1 for row in report["papers"] if bool(row.get("ok")))
    failed_papers = max(0, total_papers - ok_papers)
    total_chat_checks = sum(len((row.get("chat") or {}).get("questions") or []) for row in report["papers"])
    passed_chat_checks = sum(
        1
        for row in report["papers"]
        for item in ((row.get("chat") or {}).get("questions") or [])
        if bool(item.get("ok"))
    )
    report["finished_at"] = time.time()
    report["elapsed_seconds"] = float(report["finished_at"] - started_at)
    report["summary"] = {
        "total_papers": total_papers,
        "ok_papers": ok_papers,
        "failed_papers": failed_papers,
        "expected_structured_questions": expected_structured_question_count,
        "total_chat_checks": total_chat_checks,
        "passed_chat_checks": passed_chat_checks,
        "error_count": len(report["errors"]),
    }
    report["ok"] = bool(total_papers > 0 and failed_papers == 0 and passed_chat_checks == total_chat_checks and not report["errors"])

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"[done] ok={report['ok']} papers_ok={ok_papers}/{total_papers} "
        f"chat_checks_ok={passed_chat_checks}/{total_chat_checks} report={out_path}"
    )
    if not report["ok"]:
        print("[note] see report JSON for per-paper errors.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

