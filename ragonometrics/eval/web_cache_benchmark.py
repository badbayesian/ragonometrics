"""Concurrent benchmark for cached structured-question access on the web API."""

from __future__ import annotations

import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests

from ragonometrics.services.structured import normalize_question_key


SessionFactory = Callable[[], requests.Session]


def _percentile(values: Sequence[float], p: float) -> float:
    """Internal helper for percentile."""
    if not values:
        return 0.0
    sorted_values = sorted(float(v) for v in values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * float(p)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_values[low])
    weight = rank - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def _latency_summary(samples: Sequence[float]) -> Dict[str, float]:
    """Internal helper for latency summary."""
    vals = [float(v) for v in samples]
    if not vals:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "avg": sum(vals) / len(vals),
        "p50": _percentile(vals, 0.50),
        "p95": _percentile(vals, 0.95),
        "max": max(vals),
    }


def _load_credentials(
    *,
    identifier: str,
    password: str,
    credentials_file: Optional[str],
    users: int,
) -> List[Tuple[str, str]]:
    """Internal helper for load credentials."""
    pairs: List[Tuple[str, str]] = []
    if credentials_file:
        path = Path(credentials_file)
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                for row in reader:
                    if not row or len(row) < 2:
                        continue
                    left = str(row[0] or "").strip()
                    right = str(row[1] or "").strip()
                    if not left or not right:
                        continue
                    if left.lower() == "identifier" and right.lower() == "password":
                        continue
                    pairs.append((left, right))
    if not pairs:
        clean_identifier = str(identifier or "").strip()
        clean_password = str(password or "").strip()
        if not clean_identifier or not clean_password:
            return []
        pairs = [(clean_identifier, clean_password)]
    out: List[Tuple[str, str]] = []
    for idx in range(max(1, int(users))):
        out.append(pairs[idx % len(pairs)])
    return out


def _request_json(
    session: requests.Session,
    *,
    method: str,
    url: str,
    timeout: float,
    verify_tls: bool,
    json_payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Internal helper for request json."""
    start = time.perf_counter()
    try:
        resp = session.request(
            method=method,
            url=url,
            json=json_payload,
            headers=headers or {},
            timeout=float(timeout),
            verify=bool(verify_tls),
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "elapsed_ms": elapsed_ms if "elapsed_ms" in locals() else 0.0,
            "message": f"request_failed: {exc}",
            "data": None,
        }

    content_type = str(resp.headers.get("content-type") or "")
    payload: Dict[str, Any] = {}
    if "application/json" in content_type.lower():
        try:
            raw = resp.json()
            payload = raw if isinstance(raw, dict) else {}
        except Exception:
            payload = {}
    else:
        snippet = str(resp.text or "").replace("\n", " ").strip()[:180]
        return {
            "ok": False,
            "status": int(resp.status_code),
            "elapsed_ms": elapsed_ms,
            "message": f"unexpected_content_type={content_type or 'unknown'} body={snippet}",
            "data": None,
        }
    if int(resp.status_code) >= 400 or not bool(payload.get("ok")):
        err = payload.get("error") if isinstance(payload, dict) else {}
        msg = ""
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("code") or "").strip()
        if not msg:
            msg = f"http_{int(resp.status_code)}"
        return {
            "ok": False,
            "status": int(resp.status_code),
            "elapsed_ms": elapsed_ms,
            "message": msg,
            "data": payload.get("data") if isinstance(payload, dict) else None,
        }
    return {
        "ok": True,
        "status": int(resp.status_code),
        "elapsed_ms": elapsed_ms,
        "message": "",
        "data": payload.get("data") if isinstance(payload, dict) else None,
    }


def _select_paper_id(
    session: requests.Session,
    *,
    base_url: str,
    timeout: float,
    verify_tls: bool,
    paper_id: Optional[str],
    paper_name: Optional[str],
) -> Dict[str, Any]:
    """Internal helper for select paper id."""
    if str(paper_id or "").strip():
        return {"ok": True, "paper_id": str(paper_id or "").strip(), "elapsed_ms": 0.0, "status": 200}

    out = _request_json(
        session,
        method="GET",
        url=f"{base_url}/api/v1/papers",
        timeout=timeout,
        verify_tls=verify_tls,
    )
    if not out.get("ok"):
        return {
            "ok": False,
            "paper_id": "",
            "elapsed_ms": float(out.get("elapsed_ms") or 0.0),
            "status": int(out.get("status") or 0),
            "message": str(out.get("message") or "paper_lookup_failed"),
        }
    rows = (out.get("data") or {}).get("papers") if isinstance(out.get("data"), dict) else []
    papers = rows if isinstance(rows, list) else []
    if not papers:
        return {
            "ok": False,
            "paper_id": "",
            "elapsed_ms": float(out.get("elapsed_ms") or 0.0),
            "status": int(out.get("status") or 0),
            "message": "no_papers_found",
        }
    wanted = str(paper_name or "").strip().lower()
    selected = papers[0]
    if wanted:
        for row in papers:
            text = " ".join(
                [
                    str(row.get("display_title") or ""),
                    str(row.get("title") or ""),
                    str(row.get("name") or ""),
                ]
            ).lower()
            if wanted in text:
                selected = row
                break
    pid = str(selected.get("paper_id") or "").strip()
    if not pid:
        return {
            "ok": False,
            "paper_id": "",
            "elapsed_ms": float(out.get("elapsed_ms") or 0.0),
            "status": int(out.get("status") or 0),
            "message": "paper_id_missing",
        }
    return {
        "ok": True,
        "paper_id": pid,
        "elapsed_ms": float(out.get("elapsed_ms") or 0.0),
        "status": int(out.get("status") or 200),
    }


def _login_session(
    session: requests.Session,
    *,
    base_url: str,
    timeout: float,
    verify_tls: bool,
    identifier: str,
    password: str,
) -> Dict[str, Any]:
    """Internal helper for login session."""
    out = _request_json(
        session,
        method="POST",
        url=f"{base_url}/api/v1/auth/login",
        timeout=timeout,
        verify_tls=verify_tls,
        json_payload={"identifier": identifier, "password": password},
    )
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    out["csrf_token"] = str(data.get("csrf_token") or "").strip() if isinstance(data, dict) else ""
    out["session_id"] = str(data.get("session_id") or "").strip() if isinstance(data, dict) else ""
    return out


def _build_endpoint_metrics(
    *,
    endpoint_samples: Dict[str, List[float]],
    endpoint_success: Dict[str, int],
    endpoint_total: Dict[str, int],
) -> Dict[str, Any]:
    """Internal helper for build endpoint metrics."""
    metrics: Dict[str, Any] = {}
    for endpoint in endpoint_samples:
        total_count = int(endpoint_total.get(endpoint) or 0)
        success_count = int(endpoint_success.get(endpoint) or 0)
        error_count = max(0, total_count - success_count)
        metrics[endpoint] = {
            "count": total_count,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": (float(success_count) / float(total_count)) if total_count else 0.0,
            "latency_ms": _latency_summary(endpoint_samples.get(endpoint, [])),
        }
    return metrics


def benchmark_web_cached_structured_questions(
    *,
    base_url: str,
    identifier: str,
    password: str,
    users: int = 20,
    iterations: int = 5,
    paper_id: Optional[str] = None,
    paper_name: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: float = 30.0,
    auth_mode: str = "shared-session",
    credentials_file: Optional[str] = None,
    think_time_ms: int = 0,
    verify_tls: bool = True,
    session_factory: SessionFactory = requests.Session,
) -> Dict[str, Any]:
    """Run concurrent benchmark of cached structured-question reads on the web API."""
    clean_base = str(base_url or "").strip().rstrip("/")
    clean_auth_mode = str(auth_mode or "shared-session").strip().lower()
    if clean_auth_mode not in {"shared-session", "per-user-login"}:
        raise ValueError("auth_mode must be one of: shared-session, per-user-login")
    credentials = _load_credentials(
        identifier=identifier,
        password=password,
        credentials_file=credentials_file,
        users=max(1, int(users)),
    )
    if not credentials:
        raise ValueError("No credentials available. Provide --identifier/--password or --credentials-file.")

    started_at = time.time()
    request_errors: List[Dict[str, Any]] = []
    endpoint_samples: Dict[str, List[float]] = {
        "auth.login": [],
        "papers.list": [],
        "structured.questions": [],
        "structured.answers": [],
    }
    endpoint_success: Dict[str, int] = {k: 0 for k in endpoint_samples}
    endpoint_total: Dict[str, int] = {k: 0 for k in endpoint_samples}

    selected_paper_id = str(paper_id or "").strip()
    shared_cookie_jar = None

    if clean_auth_mode == "shared-session":
        seed_identifier, seed_password = credentials[0]
        seed_session = session_factory()
        login_out = _request_json(
            seed_session,
            method="POST",
            url=f"{clean_base}/api/v1/auth/login",
            timeout=timeout_seconds,
            verify_tls=verify_tls,
            json_payload={"identifier": seed_identifier, "password": seed_password},
        )
        endpoint_total["auth.login"] += 1
        endpoint_samples["auth.login"].append(float(login_out.get("elapsed_ms") or 0.0))
        if login_out.get("ok"):
            endpoint_success["auth.login"] += 1
        else:
            return {
                "config": {
                    "base_url": clean_base,
                    "users": int(users),
                    "iterations": int(iterations),
                    "auth_mode": clean_auth_mode,
                },
                "summary": {"elapsed_seconds": time.time() - started_at, "successful_iterations": 0, "failed_iterations": users * iterations},
                "endpoints": {},
                "cache_coverage": {"avg_ratio": 0.0, "min_ratio": 0.0, "max_ratio": 0.0},
                "errors": [{"endpoint": "auth.login", "message": str(login_out.get("message") or "login_failed")}],
            }
        paper_out = _select_paper_id(
            seed_session,
            base_url=clean_base,
            timeout=timeout_seconds,
            verify_tls=verify_tls,
            paper_id=selected_paper_id,
            paper_name=paper_name,
        )
        if int(paper_out.get("status") or 0) > 0:
            endpoint_total["papers.list"] += 1
            endpoint_samples["papers.list"].append(float(paper_out.get("elapsed_ms") or 0.0))
            if paper_out.get("ok"):
                endpoint_success["papers.list"] += 1
        if not paper_out.get("ok"):
            return {
                "config": {
                    "base_url": clean_base,
                    "users": int(users),
                    "iterations": int(iterations),
                    "auth_mode": clean_auth_mode,
                },
                "summary": {"elapsed_seconds": time.time() - started_at, "successful_iterations": 0, "failed_iterations": users * iterations},
                "endpoints": {},
                "cache_coverage": {"avg_ratio": 0.0, "min_ratio": 0.0, "max_ratio": 0.0},
                "errors": [{"endpoint": "papers.list", "message": str(paper_out.get("message") or "paper_lookup_failed")}],
            }
        selected_paper_id = str(paper_out.get("paper_id") or "").strip()
        shared_cookie_jar = requests.cookies.RequestsCookieJar()
        shared_cookie_jar.update(seed_session.cookies.get_dict())
        seed_session.close()

    coverage_rows: List[Dict[str, Any]] = []
    think_time = max(0, int(think_time_ms)) / 1000.0

    def _worker(worker_idx: int) -> Dict[str, Any]:
        """Internal helper for worker."""
        worker_errors: List[Dict[str, Any]] = []
        worker_samples: Dict[str, List[float]] = {k: [] for k in endpoint_samples}
        worker_success: Dict[str, int] = {k: 0 for k in endpoint_samples}
        worker_total: Dict[str, int] = {k: 0 for k in endpoint_samples}
        worker_coverage: List[Dict[str, Any]] = []
        worker_paper_id = selected_paper_id

        session = session_factory()
        try:
            if clean_auth_mode == "shared-session":
                if shared_cookie_jar is not None:
                    session.cookies.update(shared_cookie_jar.get_dict())
            else:
                login_identifier, login_password = credentials[worker_idx % len(credentials)]
                login_out = _request_json(
                    session,
                    method="POST",
                    url=f"{clean_base}/api/v1/auth/login",
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    json_payload={"identifier": login_identifier, "password": login_password},
                )
                worker_total["auth.login"] += 1
                worker_samples["auth.login"].append(float(login_out.get("elapsed_ms") or 0.0))
                if login_out.get("ok"):
                    worker_success["auth.login"] += 1
                else:
                    worker_errors.append(
                        {
                            "worker": worker_idx,
                            "iteration": 0,
                            "endpoint": "auth.login",
                            "message": str(login_out.get("message") or "login_failed"),
                        }
                    )
                    return {
                        "samples": worker_samples,
                        "success": worker_success,
                        "total": worker_total,
                        "coverage": worker_coverage,
                        "errors": worker_errors,
                    }

            if not worker_paper_id:
                paper_out = _select_paper_id(
                    session,
                    base_url=clean_base,
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    paper_id=None,
                    paper_name=paper_name,
                )
                if int(paper_out.get("status") or 0) > 0:
                    worker_total["papers.list"] += 1
                    worker_samples["papers.list"].append(float(paper_out.get("elapsed_ms") or 0.0))
                    if paper_out.get("ok"):
                        worker_success["papers.list"] += 1
                if not paper_out.get("ok"):
                    worker_errors.append(
                        {
                            "worker": worker_idx,
                            "iteration": 0,
                            "endpoint": "papers.list",
                            "message": str(paper_out.get("message") or "paper_lookup_failed"),
                        }
                    )
                    return {
                        "samples": worker_samples,
                        "success": worker_success,
                        "total": worker_total,
                        "coverage": worker_coverage,
                        "errors": worker_errors,
                    }
                worker_paper_id = str(paper_out.get("paper_id") or "").strip()

            model_query = f"&model={model}" if str(model or "").strip() else ""
            for iteration_idx in range(max(1, int(iterations))):
                questions_out = _request_json(
                    session,
                    method="GET",
                    url=f"{clean_base}/api/v1/structured/questions",
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                )
                worker_total["structured.questions"] += 1
                worker_samples["structured.questions"].append(float(questions_out.get("elapsed_ms") or 0.0))
                if questions_out.get("ok"):
                    worker_success["structured.questions"] += 1

                answers_out = _request_json(
                    session,
                    method="GET",
                    url=f"{clean_base}/api/v1/structured/answers?paper_id={worker_paper_id}{model_query}",
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                )
                worker_total["structured.answers"] += 1
                worker_samples["structured.answers"].append(float(answers_out.get("elapsed_ms") or 0.0))
                if answers_out.get("ok"):
                    worker_success["structured.answers"] += 1

                if not questions_out.get("ok"):
                    worker_errors.append(
                        {
                            "worker": worker_idx,
                            "iteration": iteration_idx,
                            "endpoint": "structured.questions",
                            "message": str(questions_out.get("message") or "failed"),
                        }
                    )
                if not answers_out.get("ok"):
                    worker_errors.append(
                        {
                            "worker": worker_idx,
                            "iteration": iteration_idx,
                            "endpoint": "structured.answers",
                            "message": str(answers_out.get("message") or "failed"),
                        }
                    )
                if questions_out.get("ok") and answers_out.get("ok"):
                    questions_data = (questions_out.get("data") or {}).get("questions")
                    answers_data = (answers_out.get("data") or {}).get("answers")
                    questions = questions_data if isinstance(questions_data, list) else []
                    answers = answers_data if isinstance(answers_data, dict) else {}
                    total_questions = len(questions)
                    cached = 0
                    for item in questions:
                        question_text = str((item or {}).get("question") or "")
                        qkey = normalize_question_key(question_text)
                        if qkey and qkey in answers:
                            cached += 1
                    ratio = (float(cached) / float(total_questions)) if total_questions > 0 else 0.0
                    worker_coverage.append(
                        {
                            "worker": worker_idx,
                            "iteration": iteration_idx,
                            "cached_questions": cached,
                            "total_questions": total_questions,
                            "coverage_ratio": ratio,
                        }
                    )
                if think_time > 0:
                    time.sleep(think_time)
        finally:
            session.close()
        return {
            "samples": worker_samples,
            "success": worker_success,
            "total": worker_total,
            "coverage": worker_coverage,
            "errors": worker_errors,
        }

    max_workers = max(1, int(users))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_worker, idx) for idx in range(max_workers)]
        for fut in as_completed(futures):
            worker_out = fut.result()
            for key in endpoint_samples:
                endpoint_samples[key].extend(worker_out["samples"].get(key, []))
                endpoint_success[key] += int(worker_out["success"].get(key, 0) or 0)
                endpoint_total[key] += int(worker_out["total"].get(key, 0) or 0)
            coverage_rows.extend(worker_out.get("coverage") or [])
            request_errors.extend(worker_out.get("errors") or [])

    elapsed_seconds = time.time() - started_at
    successful_iterations = len(coverage_rows)
    target_iterations = max_workers * max(1, int(iterations))
    failed_iterations = max(0, target_iterations - successful_iterations)
    coverage_values = [float(item.get("coverage_ratio") or 0.0) for item in coverage_rows]
    cached_counts = [int(item.get("cached_questions") or 0) for item in coverage_rows]
    total_counts = [int(item.get("total_questions") or 0) for item in coverage_rows]

    endpoint_metrics: Dict[str, Any] = {}
    for endpoint in endpoint_samples:
        total_count = int(endpoint_total.get(endpoint) or 0)
        success_count = int(endpoint_success.get(endpoint) or 0)
        error_count = max(0, total_count - success_count)
        endpoint_metrics[endpoint] = {
            "count": total_count,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": (float(success_count) / float(total_count)) if total_count else 0.0,
            "latency_ms": _latency_summary(endpoint_samples.get(endpoint, [])),
        }

    return {
        "config": {
            "base_url": clean_base,
            "users": max_workers,
            "iterations_per_user": max(1, int(iterations)),
            "paper_id": selected_paper_id,
            "paper_name_filter": str(paper_name or ""),
            "model_filter": str(model or ""),
            "timeout_seconds": float(timeout_seconds),
            "auth_mode": clean_auth_mode,
            "credentials_file": str(credentials_file or ""),
            "think_time_ms": int(max(0, int(think_time_ms))),
            "verify_tls": bool(verify_tls),
        },
        "summary": {
            "elapsed_seconds": elapsed_seconds,
            "target_iterations": target_iterations,
            "successful_iterations": successful_iterations,
            "failed_iterations": failed_iterations,
            "iterations_per_second": (float(successful_iterations) / float(elapsed_seconds)) if elapsed_seconds > 0 else 0.0,
            "error_count": len(request_errors),
        },
        "cache_coverage": {
            "avg_ratio": (sum(coverage_values) / len(coverage_values)) if coverage_values else 0.0,
            "min_ratio": min(coverage_values) if coverage_values else 0.0,
            "max_ratio": max(coverage_values) if coverage_values else 0.0,
            "avg_cached_questions": (sum(cached_counts) / len(cached_counts)) if cached_counts else 0.0,
            "avg_total_questions": (sum(total_counts) / len(total_counts)) if total_counts else 0.0,
        },
        "endpoints": endpoint_metrics,
        "errors": request_errors[:200],
    }


def benchmark_web_tabs(
    *,
    base_url: str,
    identifier: str,
    password: str,
    users: int = 20,
    iterations: int = 3,
    paper_id: Optional[str] = None,
    paper_name: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: float = 30.0,
    auth_mode: str = "shared-session",
    credentials_file: Optional[str] = None,
    think_time_ms: int = 0,
    verify_tls: bool = True,
    include_chat: bool = True,
    include_structured: bool = True,
    include_openalex: bool = True,
    include_network: bool = True,
    include_usage: bool = True,
    network_max_references: int = 10,
    network_max_citing: int = 10,
    usage_recent_limit: int = 200,
    usage_session_only: bool = False,
    session_factory: SessionFactory = requests.Session,
) -> Dict[str, Any]:
    """Run concurrent benchmark for web tab API endpoints."""
    clean_base = str(base_url or "").strip().rstrip("/")
    clean_auth_mode = str(auth_mode or "shared-session").strip().lower()
    if clean_auth_mode not in {"shared-session", "per-user-login"}:
        raise ValueError("auth_mode must be one of: shared-session, per-user-login")
    credentials = _load_credentials(
        identifier=identifier,
        password=password,
        credentials_file=credentials_file,
        users=max(1, int(users)),
    )
    if not credentials:
        raise ValueError("No credentials available. Provide --identifier/--password or --credentials-file.")

    endpoint_list: List[str] = ["auth.login", "papers.list"]
    if include_chat:
        endpoint_list.extend(["chat.suggestions", "chat.history"])
    if include_structured:
        endpoint_list.extend(["structured.questions", "structured.answers"])
    if include_openalex:
        endpoint_list.append("openalex.metadata")
    if include_network:
        endpoint_list.append("openalex.citation_network")
    if include_usage:
        endpoint_list.extend(["usage.summary", "usage.by_model", "usage.recent"])

    endpoint_samples: Dict[str, List[float]] = {name: [] for name in endpoint_list}
    endpoint_success: Dict[str, int] = {name: 0 for name in endpoint_list}
    endpoint_total: Dict[str, int] = {name: 0 for name in endpoint_list}
    request_errors: List[Dict[str, Any]] = []

    needs_paper = bool(include_chat or include_structured or include_openalex or include_network)
    selected_paper_id = str(paper_id or "").strip()
    shared_cookie_jar = None
    started_at = time.time()

    if clean_auth_mode == "shared-session":
        seed_identifier, seed_password = credentials[0]
        seed_session = session_factory()
        login_out = _login_session(
            seed_session,
            base_url=clean_base,
            timeout=timeout_seconds,
            verify_tls=verify_tls,
            identifier=seed_identifier,
            password=seed_password,
        )
        endpoint_total["auth.login"] += 1
        endpoint_samples["auth.login"].append(float(login_out.get("elapsed_ms") or 0.0))
        if login_out.get("ok"):
            endpoint_success["auth.login"] += 1
        else:
            return {
                "config": {
                    "base_url": clean_base,
                    "users": int(users),
                    "iterations_per_user": int(iterations),
                    "auth_mode": clean_auth_mode,
                },
                "summary": {
                    "elapsed_seconds": time.time() - started_at,
                    "target_iterations": int(users) * max(1, int(iterations)),
                    "successful_iterations": 0,
                    "failed_iterations": int(users) * max(1, int(iterations)),
                    "iterations_per_second": 0.0,
                    "error_count": 1,
                },
                "endpoints": _build_endpoint_metrics(
                    endpoint_samples=endpoint_samples,
                    endpoint_success=endpoint_success,
                    endpoint_total=endpoint_total,
                ),
                "errors": [{"endpoint": "auth.login", "message": str(login_out.get("message") or "login_failed")}],
            }
        if needs_paper and not selected_paper_id:
            paper_out = _select_paper_id(
                seed_session,
                base_url=clean_base,
                timeout=timeout_seconds,
                verify_tls=verify_tls,
                paper_id=None,
                paper_name=paper_name,
            )
            endpoint_total["papers.list"] += 1
            endpoint_samples["papers.list"].append(float(paper_out.get("elapsed_ms") or 0.0))
            if paper_out.get("ok"):
                endpoint_success["papers.list"] += 1
                selected_paper_id = str(paper_out.get("paper_id") or "").strip()
            else:
                return {
                    "config": {
                        "base_url": clean_base,
                        "users": int(users),
                        "iterations_per_user": int(iterations),
                        "auth_mode": clean_auth_mode,
                    },
                    "summary": {
                        "elapsed_seconds": time.time() - started_at,
                        "target_iterations": int(users) * max(1, int(iterations)),
                        "successful_iterations": 0,
                        "failed_iterations": int(users) * max(1, int(iterations)),
                        "iterations_per_second": 0.0,
                        "error_count": 1,
                    },
                    "endpoints": _build_endpoint_metrics(
                        endpoint_samples=endpoint_samples,
                        endpoint_success=endpoint_success,
                        endpoint_total=endpoint_total,
                    ),
                    "errors": [{"endpoint": "papers.list", "message": str(paper_out.get("message") or "paper_lookup_failed")}],
                }
        shared_cookie_jar = requests.cookies.RequestsCookieJar()
        shared_cookie_jar.update(seed_session.cookies.get_dict())
        seed_session.close()

    think_time = max(0, int(think_time_ms)) / 1000.0

    def _worker(worker_idx: int) -> Dict[str, Any]:
        """Internal helper for worker."""
        worker_errors: List[Dict[str, Any]] = []
        worker_samples: Dict[str, List[float]] = {name: [] for name in endpoint_list}
        worker_success: Dict[str, int] = {name: 0 for name in endpoint_list}
        worker_total: Dict[str, int] = {name: 0 for name in endpoint_list}
        worker_successful_iterations = 0
        worker_paper_id = selected_paper_id
        session = session_factory()

        def _record(endpoint: str, out: Dict[str, Any], iteration_idx: int) -> bool:
            """Internal helper for record."""
            worker_total[endpoint] += 1
            worker_samples[endpoint].append(float(out.get("elapsed_ms") or 0.0))
            if out.get("ok"):
                worker_success[endpoint] += 1
                return True
            worker_errors.append(
                {
                    "worker": worker_idx,
                    "iteration": iteration_idx,
                    "endpoint": endpoint,
                    "message": str(out.get("message") or "failed"),
                }
            )
            return False

        try:
            if clean_auth_mode == "shared-session":
                if shared_cookie_jar is not None:
                    session.cookies.update(shared_cookie_jar.get_dict())
            else:
                login_identifier, login_password = credentials[worker_idx % len(credentials)]
                login_out = _login_session(
                    session,
                    base_url=clean_base,
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    identifier=login_identifier,
                    password=login_password,
                )
                if not _record("auth.login", login_out, 0):
                    return {
                        "samples": worker_samples,
                        "success": worker_success,
                        "total": worker_total,
                        "successful_iterations": 0,
                        "errors": worker_errors,
                    }
            if needs_paper and not worker_paper_id:
                paper_out = _select_paper_id(
                    session,
                    base_url=clean_base,
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    paper_id=None,
                    paper_name=paper_name,
                )
                if not _record("papers.list", paper_out, 0):
                    return {
                        "samples": worker_samples,
                        "success": worker_success,
                        "total": worker_total,
                        "successful_iterations": 0,
                        "errors": worker_errors,
                    }
                worker_paper_id = str(paper_out.get("paper_id") or "").strip()
            usage_flag = "1" if usage_session_only else "0"
            model_query = f"&model={model}" if str(model or "").strip() else ""
            for iteration_idx in range(max(1, int(iterations))):
                ok_iteration = True
                if include_chat:
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/chat/suggestions?paper_id={worker_paper_id}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("chat.suggestions", out, iteration_idx) and ok_iteration
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/chat/history?paper_id={worker_paper_id}&limit=50",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("chat.history", out, iteration_idx) and ok_iteration
                if include_structured:
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/structured/questions",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("structured.questions", out, iteration_idx) and ok_iteration
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/structured/answers?paper_id={worker_paper_id}{model_query}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("structured.answers", out, iteration_idx) and ok_iteration
                if include_openalex:
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/openalex/metadata?paper_id={worker_paper_id}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("openalex.metadata", out, iteration_idx) and ok_iteration
                if include_network:
                    out = _request_json(
                        session,
                        method="GET",
                        url=(
                            f"{clean_base}/api/v1/openalex/citation-network?paper_id={worker_paper_id}"
                            f"&max_references={int(network_max_references)}&max_citing={int(network_max_citing)}"
                        ),
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("openalex.citation_network", out, iteration_idx) and ok_iteration
                if include_usage:
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/usage/summary?session_only={usage_flag}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("usage.summary", out, iteration_idx) and ok_iteration
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/usage/by-model?session_only={usage_flag}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("usage.by_model", out, iteration_idx) and ok_iteration
                    out = _request_json(
                        session,
                        method="GET",
                        url=f"{clean_base}/api/v1/usage/recent?session_only={usage_flag}&limit={int(usage_recent_limit)}",
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                    )
                    ok_iteration = _record("usage.recent", out, iteration_idx) and ok_iteration
                if ok_iteration:
                    worker_successful_iterations += 1
                if think_time > 0:
                    time.sleep(think_time)
        finally:
            session.close()
        return {
            "samples": worker_samples,
            "success": worker_success,
            "total": worker_total,
            "successful_iterations": worker_successful_iterations,
            "errors": worker_errors,
        }

    max_workers = max(1, int(users))
    successful_iterations = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_worker, idx) for idx in range(max_workers)]
        for fut in as_completed(futures):
            worker_out = fut.result()
            successful_iterations += int(worker_out.get("successful_iterations") or 0)
            for key in endpoint_list:
                endpoint_samples[key].extend(worker_out["samples"].get(key, []))
                endpoint_success[key] += int(worker_out["success"].get(key, 0) or 0)
                endpoint_total[key] += int(worker_out["total"].get(key, 0) or 0)
            request_errors.extend(worker_out.get("errors") or [])

    elapsed_seconds = time.time() - started_at
    target_iterations = max_workers * max(1, int(iterations))
    failed_iterations = max(0, target_iterations - successful_iterations)
    return {
        "config": {
            "base_url": clean_base,
            "users": max_workers,
            "iterations_per_user": max(1, int(iterations)),
            "auth_mode": clean_auth_mode,
            "paper_id": selected_paper_id,
            "paper_name_filter": str(paper_name or ""),
            "model_filter": str(model or ""),
            "timeout_seconds": float(timeout_seconds),
            "credentials_file": str(credentials_file or ""),
            "think_time_ms": int(max(0, int(think_time_ms))),
            "verify_tls": bool(verify_tls),
            "include_chat": bool(include_chat),
            "include_structured": bool(include_structured),
            "include_openalex": bool(include_openalex),
            "include_network": bool(include_network),
            "include_usage": bool(include_usage),
        },
        "summary": {
            "elapsed_seconds": elapsed_seconds,
            "target_iterations": target_iterations,
            "successful_iterations": successful_iterations,
            "failed_iterations": failed_iterations,
            "iterations_per_second": (float(successful_iterations) / float(elapsed_seconds)) if elapsed_seconds > 0 else 0.0,
            "error_count": len(request_errors),
        },
        "endpoints": _build_endpoint_metrics(
            endpoint_samples=endpoint_samples,
            endpoint_success=endpoint_success,
            endpoint_total=endpoint_total,
        ),
        "errors": request_errors[:200],
    }


def benchmark_web_chat_turns(
    *,
    base_url: str,
    identifier: str,
    password: str,
    users: int = 10,
    iterations: int = 3,
    question: str = "What is the main research question of this paper?",
    paper_id: Optional[str] = None,
    paper_name: Optional[str] = None,
    model: Optional[str] = None,
    timeout_seconds: float = 60.0,
    auth_mode: str = "shared-session",
    credentials_file: Optional[str] = None,
    think_time_ms: int = 0,
    verify_tls: bool = True,
    variation_mode: bool = False,
    top_k: Optional[int] = None,
    session_factory: SessionFactory = requests.Session,
) -> Dict[str, Any]:
    """Run concurrent benchmark for web chat turns and report cache-hit behavior."""
    clean_base = str(base_url or "").strip().rstrip("/")
    clean_auth_mode = str(auth_mode or "shared-session").strip().lower()
    if clean_auth_mode not in {"shared-session", "per-user-login"}:
        raise ValueError("auth_mode must be one of: shared-session, per-user-login")
    credentials = _load_credentials(
        identifier=identifier,
        password=password,
        credentials_file=credentials_file,
        users=max(1, int(users)),
    )
    if not credentials:
        raise ValueError("No credentials available. Provide --identifier/--password or --credentials-file.")

    endpoint_samples: Dict[str, List[float]] = {"auth.login": [], "papers.list": [], "chat.turn": []}
    endpoint_success: Dict[str, int] = {k: 0 for k in endpoint_samples}
    endpoint_total: Dict[str, int] = {k: 0 for k in endpoint_samples}
    request_errors: List[Dict[str, Any]] = []
    cache_hits: List[bool] = []
    cache_layers: Dict[str, int] = {}
    selected_paper_id = str(paper_id or "").strip()
    shared_cookie_jar = None
    shared_csrf_token = ""
    started_at = time.time()

    if clean_auth_mode == "shared-session":
        seed_identifier, seed_password = credentials[0]
        seed_session = session_factory()
        login_out = _login_session(
            seed_session,
            base_url=clean_base,
            timeout=timeout_seconds,
            verify_tls=verify_tls,
            identifier=seed_identifier,
            password=seed_password,
        )
        endpoint_total["auth.login"] += 1
        endpoint_samples["auth.login"].append(float(login_out.get("elapsed_ms") or 0.0))
        if login_out.get("ok"):
            endpoint_success["auth.login"] += 1
            shared_csrf_token = str(login_out.get("csrf_token") or "")
        else:
            return {
                "config": {
                    "base_url": clean_base,
                    "users": int(users),
                    "iterations_per_user": int(iterations),
                    "auth_mode": clean_auth_mode,
                },
                "summary": {
                    "elapsed_seconds": time.time() - started_at,
                    "target_iterations": int(users) * max(1, int(iterations)),
                    "successful_iterations": 0,
                    "failed_iterations": int(users) * max(1, int(iterations)),
                    "iterations_per_second": 0.0,
                    "error_count": 1,
                },
                "chat_cache": {"cache_hit_ratio": 0.0, "layer_counts": {}},
                "endpoints": _build_endpoint_metrics(
                    endpoint_samples=endpoint_samples,
                    endpoint_success=endpoint_success,
                    endpoint_total=endpoint_total,
                ),
                "errors": [{"endpoint": "auth.login", "message": str(login_out.get("message") or "login_failed")}],
            }
        if not selected_paper_id:
            paper_out = _select_paper_id(
                seed_session,
                base_url=clean_base,
                timeout=timeout_seconds,
                verify_tls=verify_tls,
                paper_id=None,
                paper_name=paper_name,
            )
            endpoint_total["papers.list"] += 1
            endpoint_samples["papers.list"].append(float(paper_out.get("elapsed_ms") or 0.0))
            if paper_out.get("ok"):
                endpoint_success["papers.list"] += 1
                selected_paper_id = str(paper_out.get("paper_id") or "").strip()
            else:
                return {
                    "config": {
                        "base_url": clean_base,
                        "users": int(users),
                        "iterations_per_user": int(iterations),
                        "auth_mode": clean_auth_mode,
                    },
                    "summary": {
                        "elapsed_seconds": time.time() - started_at,
                        "target_iterations": int(users) * max(1, int(iterations)),
                        "successful_iterations": 0,
                        "failed_iterations": int(users) * max(1, int(iterations)),
                        "iterations_per_second": 0.0,
                        "error_count": 1,
                    },
                    "chat_cache": {"cache_hit_ratio": 0.0, "layer_counts": {}},
                    "endpoints": _build_endpoint_metrics(
                        endpoint_samples=endpoint_samples,
                        endpoint_success=endpoint_success,
                        endpoint_total=endpoint_total,
                    ),
                    "errors": [{"endpoint": "papers.list", "message": str(paper_out.get("message") or "paper_lookup_failed")}],
                }
        shared_cookie_jar = requests.cookies.RequestsCookieJar()
        shared_cookie_jar.update(seed_session.cookies.get_dict())
        seed_session.close()

    think_time = max(0, int(think_time_ms)) / 1000.0

    def _worker(worker_idx: int) -> Dict[str, Any]:
        """Internal helper for worker."""
        worker_errors: List[Dict[str, Any]] = []
        worker_samples: Dict[str, List[float]] = {k: [] for k in endpoint_samples}
        worker_success: Dict[str, int] = {k: 0 for k in endpoint_samples}
        worker_total: Dict[str, int] = {k: 0 for k in endpoint_samples}
        worker_cache_hits: List[bool] = []
        worker_layers: Dict[str, int] = {}
        worker_successful_iterations = 0
        worker_paper_id = selected_paper_id
        worker_csrf = shared_csrf_token
        session = session_factory()

        def _record(endpoint: str, out: Dict[str, Any], iteration_idx: int) -> bool:
            """Internal helper for record."""
            worker_total[endpoint] += 1
            worker_samples[endpoint].append(float(out.get("elapsed_ms") or 0.0))
            if out.get("ok"):
                worker_success[endpoint] += 1
                return True
            worker_errors.append(
                {
                    "worker": worker_idx,
                    "iteration": iteration_idx,
                    "endpoint": endpoint,
                    "message": str(out.get("message") or "failed"),
                }
            )
            return False

        try:
            if clean_auth_mode == "shared-session":
                if shared_cookie_jar is not None:
                    session.cookies.update(shared_cookie_jar.get_dict())
            else:
                login_identifier, login_password = credentials[worker_idx % len(credentials)]
                login_out = _login_session(
                    session,
                    base_url=clean_base,
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    identifier=login_identifier,
                    password=login_password,
                )
                if not _record("auth.login", login_out, 0):
                    return {
                        "samples": worker_samples,
                        "success": worker_success,
                        "total": worker_total,
                        "successful_iterations": 0,
                        "cache_hits": worker_cache_hits,
                        "cache_layers": worker_layers,
                        "errors": worker_errors,
                    }
                worker_csrf = str(login_out.get("csrf_token") or "")
                if not worker_paper_id:
                    paper_out = _select_paper_id(
                        session,
                        base_url=clean_base,
                        timeout=timeout_seconds,
                        verify_tls=verify_tls,
                        paper_id=None,
                        paper_name=paper_name,
                    )
                    if not _record("papers.list", paper_out, 0):
                        return {
                            "samples": worker_samples,
                            "success": worker_success,
                            "total": worker_total,
                            "successful_iterations": 0,
                            "cache_hits": worker_cache_hits,
                            "cache_layers": worker_layers,
                            "errors": worker_errors,
                        }
                    worker_paper_id = str(paper_out.get("paper_id") or "").strip()

            for iteration_idx in range(max(1, int(iterations))):
                payload: Dict[str, Any] = {
                    "paper_id": worker_paper_id,
                    "question": str(question or "").strip(),
                    "history": [],
                    "variation_mode": bool(variation_mode),
                }
                if str(model or "").strip():
                    payload["model"] = str(model or "").strip()
                if top_k is not None:
                    payload["top_k"] = int(top_k)
                out = _request_json(
                    session,
                    method="POST",
                    url=f"{clean_base}/api/v1/chat/turn",
                    timeout=timeout_seconds,
                    verify_tls=verify_tls,
                    json_payload=payload,
                    headers={"X-CSRF-Token": worker_csrf},
                )
                if _record("chat.turn", out, iteration_idx):
                    worker_successful_iterations += 1
                    data = out.get("data") if isinstance(out.get("data"), dict) else {}
                    hit = bool(data.get("cache_hit")) if isinstance(data, dict) else False
                    worker_cache_hits.append(hit)
                    layer = str(data.get("cache_hit_layer") or "") if isinstance(data, dict) else ""
                    if layer:
                        worker_layers[layer] = int(worker_layers.get(layer, 0) or 0) + 1
                if think_time > 0:
                    time.sleep(think_time)
        finally:
            session.close()
        return {
            "samples": worker_samples,
            "success": worker_success,
            "total": worker_total,
            "successful_iterations": worker_successful_iterations,
            "cache_hits": worker_cache_hits,
            "cache_layers": worker_layers,
            "errors": worker_errors,
        }

    max_workers = max(1, int(users))
    successful_iterations = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_worker, idx) for idx in range(max_workers)]
        for fut in as_completed(futures):
            worker_out = fut.result()
            successful_iterations += int(worker_out.get("successful_iterations") or 0)
            for key in endpoint_samples:
                endpoint_samples[key].extend(worker_out["samples"].get(key, []))
                endpoint_success[key] += int(worker_out["success"].get(key, 0) or 0)
                endpoint_total[key] += int(worker_out["total"].get(key, 0) or 0)
            cache_hits.extend(worker_out.get("cache_hits") or [])
            layers = worker_out.get("cache_layers") or {}
            if isinstance(layers, dict):
                for key, value in layers.items():
                    layer_key = str(key or "").strip()
                    if not layer_key:
                        continue
                    cache_layers[layer_key] = int(cache_layers.get(layer_key, 0) or 0) + int(value or 0)
            request_errors.extend(worker_out.get("errors") or [])

    elapsed_seconds = time.time() - started_at
    target_iterations = max_workers * max(1, int(iterations))
    failed_iterations = max(0, target_iterations - successful_iterations)
    cache_hit_ratio = (sum(1 for v in cache_hits if bool(v)) / len(cache_hits)) if cache_hits else 0.0
    return {
        "config": {
            "base_url": clean_base,
            "users": max_workers,
            "iterations_per_user": max(1, int(iterations)),
            "auth_mode": clean_auth_mode,
            "paper_id": selected_paper_id,
            "paper_name_filter": str(paper_name or ""),
            "question": str(question or "").strip(),
            "model": str(model or ""),
            "variation_mode": bool(variation_mode),
            "top_k": int(top_k) if top_k is not None else None,
            "timeout_seconds": float(timeout_seconds),
            "credentials_file": str(credentials_file or ""),
            "think_time_ms": int(max(0, int(think_time_ms))),
            "verify_tls": bool(verify_tls),
        },
        "summary": {
            "elapsed_seconds": elapsed_seconds,
            "target_iterations": target_iterations,
            "successful_iterations": successful_iterations,
            "failed_iterations": failed_iterations,
            "iterations_per_second": (float(successful_iterations) / float(elapsed_seconds)) if elapsed_seconds > 0 else 0.0,
            "error_count": len(request_errors),
        },
        "chat_cache": {
            "cache_hit_ratio": cache_hit_ratio,
            "hit_count": int(sum(1 for v in cache_hits if bool(v))),
            "sample_count": len(cache_hits),
            "layer_counts": cache_layers,
        },
        "endpoints": _build_endpoint_metrics(
            endpoint_samples=endpoint_samples,
            endpoint_success=endpoint_success,
            endpoint_total=endpoint_total,
        ),
        "errors": request_errors[:200],
    }


def write_web_cache_benchmark_report(report: Dict[str, Any], out_path: str) -> str:
    """Write benchmark report JSON to disk and return the absolute path."""
    target = Path(out_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(target)
