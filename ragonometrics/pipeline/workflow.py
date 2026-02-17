"""Workflow orchestration for ingest, enrichment, agentic QA, indexing, evaluation, and reporting."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openai import OpenAI
from tqdm import tqdm

from ragonometrics.core.main import (
    embed_texts,
    load_papers,
    load_settings,
    prepare_chunks_for_paper,
    top_k_context,
)
from ragonometrics.core.prompts import RESEARCHER_QA_PROMPT
from ragonometrics.indexing.indexer import build_index
from ragonometrics.pipeline import call_openai, extract_citations as llm_extract_citations
from ragonometrics.pipeline.pipeline import extract_json
from ragonometrics.pipeline.state import (
    DEFAULT_STATE_DB,
    create_workflow_run,
    find_similar_completed_step,
    find_similar_report_question_items,
    record_step,
    set_workflow_status,
)
from ragonometrics.pipeline.report_store import store_workflow_report
from ragonometrics.pipeline.prep import prep_corpus
from ragonometrics.integrations.econ_data import fetch_fred_series
from ragonometrics.db.connection import connect as db_connect


def _utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Returns:
        str: Description.
    """
    return datetime.now(timezone.utc).isoformat()


def _is_insufficient_quota_error(exc: Exception) -> bool:
    """Return whether an exception chain represents an insufficient quota error.

    Args:
        exc (Exception): Description.

    Returns:
        bool: Description.
    """
    current: Exception | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        msg = str(current).lower()
        code = str(getattr(current, "code", "") or "").lower()
        if code == "insufficient_quota":
            return True
        if "insufficient_quota" in msg:
            return True
        if "error code: 429" in msg and ("quota" in msg or "exceeded your current quota" in msg):
            return True
        nxt = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        current = nxt if isinstance(nxt, Exception) else None
    return False


def _estimate_tokens(text: str) -> int:
    """Estimate token usage from text length using a coarse heuristic.

    Args:
        text (str): Description.

    Returns:
        int: Description.
    """
    if not text:
        return 0
    text = str(text)
    words = text.split()
    word_estimate = len(words)
    char_estimate = max(1, int(len(text) / 4))
    return max(word_estimate, char_estimate)


def _resolve_paper_paths(papers_path: Path) -> List[Path]:
    """Resolve a file or directory input into a list of PDF paths.

    Args:
        papers_path (Path): Description.

    Returns:
        List[Path]: Description.
    """
    if papers_path.is_file():
        if papers_path.suffix.lower() == ".pdf":
            return [papers_path]
        return []
    if papers_path.is_dir():
        return sorted(papers_path.glob("*.pdf"))
    return []


def _progress_iter(items, desc: str, *, total: int | None = None):
    """Wrap an iterable with a tqdm progress bar.

    Args:
        items (Any): Description.
        desc (str): Description.
        total (int | None): Description.

    Returns:
        Any: Description.
    """
    return tqdm(items, desc=desc, total=total)


def _can_connect_db(db_url: str) -> bool:
    """Return True when the provided database URL is reachable.

    Args:
        db_url (str): Description.

    Returns:
        bool: Description.
    """
    try:
        conn = db_connect(db_url, require_migrated=False)
        conn.close()
        return True
    except Exception:
        return False


def _resolve_meta_db_url(preferred_db_url: str | None) -> tuple[str | None, Dict[str, Any]]:
    """Select the metadata database URL and capture fallback diagnostics.

    Args:
        preferred_db_url (str | None): Description.

    Returns:
        tuple[str | None, Dict[str, Any]]: Description.
    """
    preferred = (preferred_db_url or "").strip() or None
    env_db = (os.environ.get("DATABASE_URL") or "").strip() or None
    candidates: List[tuple[str, str]] = []
    seen: set[str] = set()
    for source, url in (("meta_db_url", preferred), ("env_DATABASE_URL", env_db)):
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append((source, url))

    info: Dict[str, Any] = {
        "preferred_present": bool(preferred),
        "env_present": bool(env_db),
        "selected_source": None,
        "selected_reachable": False,
        "fallback_used": False,
    }

    for source, url in candidates:
        if _can_connect_db(url):
            info["selected_source"] = source
            info["selected_reachable"] = True
            info["fallback_used"] = bool(preferred) and source != "meta_db_url"
            return url, info

    # No reachable candidate. Keep deterministic preference order.
    if preferred:
        info["selected_source"] = "meta_db_url"
        return preferred, info
    if env_db:
        info["selected_source"] = "env_DATABASE_URL"
        return env_db, info
    return None, info


def _write_report(report_dir: Path, run_id: str, payload: Dict[str, Any]) -> Path:
    """Write the workflow report JSON file and return its path.

    Args:
        report_dir (Path): Description.
        run_id (str): Description.
        payload (Dict[str, Any]): Description.

    Returns:
        Path: Description.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"workflow-report-{run_id}.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def _bool_env(name: str, default: bool) -> bool:
    """Parse a boolean environment variable with a default fallback.

    Args:
        name (str): Description.
        default (bool): Description.

    Returns:
        bool: Description.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _with_reuse_marker(step_output: Any, *, source_run_id: str, source_finished_at: str | None) -> Dict[str, Any]:
    """Attach source-run reuse metadata to a step output payload.

    Args:
        step_output (Any): Description.
        source_run_id (str): Description.
        source_finished_at (str | None): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    if isinstance(step_output, dict):
        out = dict(step_output)
    else:
        out = {"value": step_output}
    out["_reused_from"] = {
        "run_id": source_run_id,
        "finished_at": source_finished_at,
    }
    return out


def _tail(text: str, limit: int = 800) -> str:
    """Return the trailing slice of a string constrained by the provided limit.

    Args:
        text (str): Description.
        limit (int): Description.

    Returns:
        str: Description.
    """
    if not text:
        return ""
    return text[-limit:]


def _run_subprocess(cmd: List[str], *, cwd: Path) -> tuple[int, str, str]:
    """Execute a subprocess command and capture return code, stdout, and stderr.

    Args:
        cmd (List[str]): Description.
        cwd (Path): Description.

    Returns:
        tuple[int, str, str]: Description.
    """
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git_value(args: List[str]) -> str | None:
    """Run a git command and return a trimmed stdout value when successful.

    Args:
        args (List[str]): Description.

    Returns:
        str | None: Description.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        value = (proc.stdout or "").strip()
        if proc.returncode == 0 and value:
            return value
    except Exception:
        pass
    return None


def _render_audit_artifacts(*, run_id: str, report_path: Path, report_dir: Path) -> Dict[str, Any]:
    """Render markdown/PDF audit artifacts for a workflow report.

    Args:
        run_id (str): Description.
        report_path (Path): Description.
        report_dir (Path): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    out: Dict[str, Any] = {
        "enabled": _bool_env("WORKFLOW_RENDER_AUDIT_ARTIFACTS", True),
        "status": "pending",
        "markdown": {"status": "pending"},
        "pdf": {"status": "pending"},
    }
    if not out["enabled"]:
        out["status"] = "skipped"
        out["markdown"] = {"status": "skipped", "reason": "disabled"}
        out["pdf"] = {"status": "skipped", "reason": "disabled"}
        return out

    project_root = Path(__file__).resolve().parents[2]
    md_script = project_root / "tools" / "workflow_report_to_audit_md.py"
    pdf_script = project_root / "tools" / "markdown_to_latex_pdf.py"
    md_path = report_dir / f"audit-workflow-report-{run_id}.md"
    tex_path = report_dir / f"audit-workflow-report-{run_id}.tex"
    pdf_path = report_dir / f"audit-workflow-report-{run_id}-latex.pdf"

    if not md_script.exists():
        out["status"] = "failed"
        out["markdown"] = {"status": "failed", "reason": "md_script_missing", "path": str(md_script)}
        out["pdf"] = {"status": "skipped", "reason": "markdown_failed"}
        return out

    md_cmd = [
        sys.executable,
        str(md_script),
        "--input",
        str(report_path),
        "--output",
        str(md_path),
        "--full",
        "--clean-text",
    ]
    md_code, md_stdout, md_stderr = _run_subprocess(md_cmd, cwd=project_root)
    if md_code != 0:
        out["status"] = "failed"
        out["markdown"] = {
            "status": "failed",
            "exit_code": md_code,
            "stdout_tail": _tail(md_stdout),
            "stderr_tail": _tail(md_stderr),
        }
        out["pdf"] = {"status": "skipped", "reason": "markdown_failed"}
        return out

    out["markdown"] = {"status": "generated", "path": str(md_path)}
    pdf_enabled = _bool_env("WORKFLOW_RENDER_AUDIT_PDF", True)
    if not pdf_enabled:
        out["pdf"] = {"status": "skipped", "reason": "pdf_disabled", "path": str(pdf_path)}
        out["status"] = "completed"
        return out

    if not pdf_script.exists():
        out["pdf"] = {"status": "failed", "reason": "pdf_script_missing", "path": str(pdf_script)}
        out["status"] = "partial"
        return out

    pdf_cmd = [
        sys.executable,
        str(pdf_script),
        "--input",
        str(md_path),
        "--output-tex",
        str(tex_path),
        "--output-pdf",
        str(pdf_path),
        "--engine",
        "xelatex",
        "--quiet",
    ]
    pdf_code, pdf_stdout, pdf_stderr = _run_subprocess(pdf_cmd, cwd=project_root)
    if pdf_code != 0:
        out["pdf"] = {
            "status": "failed",
            "exit_code": pdf_code,
            "stdout_tail": _tail(pdf_stdout),
            "stderr_tail": _tail(pdf_stderr),
            "path": str(pdf_path),
            "tex_path": str(tex_path),
        }
        out["status"] = "partial"
        return out

    out["pdf"] = {"status": "generated", "path": str(pdf_path), "tex_path": str(tex_path)}
    out["status"] = "completed"
    return out


def _store_report_in_db(
    *,
    db_url: str | None,
    run_id: str,
    report_path: Path,
    payload: Dict[str, Any],
    workflow_status: str,
) -> Dict[str, Any]:
    """Persist the finalized workflow report payload to Postgres.

    Args:
        db_url (str | None): Description.
        run_id (str): Description.
        report_path (Path): Description.
        payload (Dict[str, Any]): Description.
        workflow_status (str): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    out: Dict[str, Any] = {"database_url": bool(db_url)}
    if not db_url:
        out["status"] = "skipped"
        out["reason"] = "db_url_missing"
        return out
    db_ok = _can_connect_db(db_url)
    out["database_reachable"] = db_ok
    if not db_ok:
        out["status"] = "skipped"
        out["reason"] = "db_unreachable"
        return out
    try:
        store_workflow_report(
            db_url=db_url,
            run_id=run_id,
            report_path=str(report_path),
            payload=payload,
            status=workflow_status,
        )
        out["status"] = "stored"
    except Exception as exc:
        out["status"] = "failed"
        out["error"] = str(exc)
    return out


def _collect_usage_rollup_for_run(*, db_url: str | None, run_id: str) -> Dict[str, Any]:
    """Collect token-usage rollup rows for a workflow run.

    Args:
        db_url (str | None): Postgres connection URL.
        run_id (str): Workflow run id.

    Returns:
        Dict[str, Any]: Usage summary payload for workflow reports.
    """
    out: Dict[str, Any] = {"database_url": bool(db_url)}
    if not db_url:
        out["status"] = "skipped"
        out["reason"] = "db_url_missing"
        return out
    if not _can_connect_db(db_url):
        out["status"] = "skipped"
        out["reason"] = "db_unreachable"
        return out

    try:
        conn = db_connect(db_url, require_migrated=True)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    COALESCE(step, '') AS step,
                    COALESCE(model, '') AS model,
                    COALESCE(question_id, '') AS question_id,
                    call_count,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    cost_usd_input,
                    cost_usd_output,
                    cost_usd_total,
                    first_seen_at,
                    last_seen_at
                FROM observability.token_usage_rollup
                WHERE run_id = %s
                ORDER BY total_tokens DESC, call_count DESC, step ASC, model ASC
                """,
                (run_id,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        out["status"] = "failed"
        out["error"] = str(exc)
        return out

    usage_rows: List[Dict[str, Any]] = []
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd_input": 0.0,
        "cost_usd_output": 0.0,
        "cost_usd_total": 0.0,
    }
    by_step: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        step = str(row[0] or "")
        model = str(row[1] or "")
        question_id = str(row[2] or "")
        calls = int(row[3] or 0)
        input_tokens = int(row[4] or 0)
        output_tokens = int(row[5] or 0)
        total_tokens = int(row[6] or 0)
        cost_usd_input = float(row[7] or 0.0)
        cost_usd_output = float(row[8] or 0.0)
        cost_usd_total = float(row[9] or 0.0)
        first_seen = row[10].isoformat() if hasattr(row[10], "isoformat") else row[10]
        last_seen = row[11].isoformat() if hasattr(row[11], "isoformat") else row[11]

        usage_rows.append(
            {
                "step": step,
                "model": model,
                "question_id": question_id,
                "calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd_input": cost_usd_input,
                "cost_usd_output": cost_usd_output,
                "cost_usd_total": cost_usd_total,
                "first_seen_at": first_seen,
                "last_seen_at": last_seen,
            }
        )

        totals["calls"] += calls
        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["total_tokens"] += total_tokens
        totals["cost_usd_input"] += cost_usd_input
        totals["cost_usd_output"] += cost_usd_output
        totals["cost_usd_total"] += cost_usd_total

        step_bucket = by_step.setdefault(
            step,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd_total": 0.0,
            },
        )
        step_bucket["calls"] += calls
        step_bucket["input_tokens"] += input_tokens
        step_bucket["output_tokens"] += output_tokens
        step_bucket["total_tokens"] += total_tokens
        step_bucket["cost_usd_total"] += cost_usd_total

    out["status"] = "fetched"
    out["row_count"] = len(usage_rows)
    out["totals"] = totals
    out["by_step"] = by_step
    out["rows"] = usage_rows
    return out


def _finalize_workflow_report(
    *,
    report_dir: Path,
    run_id: str,
    summary: Dict[str, Any],
    state_db: Path,
    report_started_at: str,
    db_url: str | None,
    workflow_status: str,
) -> Dict[str, Any]:
    """Finalize workflow output, write report artifacts, and persist report state.

    Args:
        report_dir (Path): Description.
        run_id (str): Description.
        summary (Dict[str, Any]): Description.
        state_db (Path): Description.
        report_started_at (str): Description.
        db_url (str | None): Description.
        workflow_status (str): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    summary.setdefault("finished_at", _utc_now())
    summary["usage_store"] = _collect_usage_rollup_for_run(db_url=db_url, run_id=run_id)
    summary["report_store"] = {"status": "pending", "database_url": bool(db_url)}
    summary["audit_artifacts"] = {"status": "pending"}
    report_path = _write_report(report_dir, run_id, summary)
    summary["report_path"] = str(report_path)
    audit_out = _render_audit_artifacts(run_id=run_id, report_path=report_path, report_dir=report_dir)
    summary["audit_artifacts"] = audit_out
    report_path = _write_report(report_dir, run_id, summary)
    report_store_out = _store_report_in_db(
        db_url=db_url,
        run_id=run_id,
        report_path=report_path,
        payload=summary,
        workflow_status=workflow_status,
    )
    summary["report_store"] = report_store_out
    report_path = _write_report(report_dir, run_id, summary)
    record_step(
        state_db,
        run_id=run_id,
        step="report",
        status="completed",
        started_at=report_started_at,
        finished_at=_utc_now(),
        output={
            "report_path": str(report_path),
            "report_store": report_store_out,
            "audit_artifacts": audit_out,
        },
    )
    set_workflow_status(state_db, run_id, workflow_status)
    return summary


def _finalize_quota_termination(
    *,
    report_dir: Path,
    run_id: str,
    summary: Dict[str, Any],
    state_db: Path,
    db_url: str | None,
    step: str,
    exc: Exception,
) -> Dict[str, Any]:
    """Finalize workflow state and report when execution stops due to quota limits.

    Args:
        report_dir (Path): Description.
        run_id (str): Description.
        summary (Dict[str, Any]): Description.
        state_db (Path): Description.
        db_url (str | None): Description.
        step (str): Description.
        exc (Exception): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    err_text = str(exc)
    summary["error"] = err_text
    summary["fatal_error"] = {
        "step": step,
        "type": "insufficient_quota",
        "action": "terminated_early",
        "error": err_text,
    }
    summary["finished_at"] = _utc_now()
    report_start = _utc_now()
    record_step(state_db, run_id=run_id, step="report", status="running", started_at=report_start)
    return _finalize_workflow_report(
        report_dir=report_dir,
        run_id=run_id,
        summary=summary,
        state_db=state_db,
        report_started_at=report_start,
        db_url=db_url,
        workflow_status="failed",
    )


def _parse_subquestions(raw: str, max_items: int) -> List[str]:
    """Parse raw planner output into a deduplicated list of subquestions.

    Args:
        raw (str): Description.
        max_items (int): Description.

    Returns:
        List[str]: Description.
    """
    items: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("-*•").strip()
        line = line.lstrip("0123456789. ").strip()
        if not line:
            continue
        if line not in items:
            items.append(line)
        if len(items) >= max_items:
            break
    return items


def _agentic_plan(
    client: OpenAI,
    question: str,
    *,
    model: str,
    max_items: int,
    run_id: str | None = None,
) -> List[str]:
    """Generate agentic subquestions for the main research prompt.

    Args:
        client (OpenAI): Description.
        question (str): Description.
        model (str): Description.
        max_items (int): Description.
        run_id (str | None): Description.

    Returns:
        List[str]: Description.
    """
    instructions = (
        "You are a research analyst. Generate a short list of sub-questions that would help "
        "answer the main question. Return one sub-question per line, no extra text."
    )
    raw = call_openai(
        client,
        model=model,
        instructions=instructions,
        user_input=f"Main question: {question}",
        max_output_tokens=200,
        usage_context="agent_plan",
        run_id=run_id,
        step="agentic_plan",
        question_id="MAIN",
    )
    items = _parse_subquestions(raw, max_items)
    if not items:
        items = [question]
    return items


def _agentic_summarize(
    client: OpenAI,
    *,
    model: str,
    question: str,
    sub_answers: List[Dict[str, str]],
    run_id: str | None = None,
) -> str:
    """Synthesize sub-answer results into a final response.

    Args:
        client (OpenAI): Description.
        model (str): Description.
        question (str): Description.
        sub_answers (List[Dict[str, str]]): Description.
        run_id (str | None): Description.

    Returns:
        str: Description.
    """
    bullets = "\n".join([f"- {item['question']}: {item['answer']}" for item in sub_answers])
    synthesis_prompt = (
        "Synthesize a concise, researcher-grade answer based on the sub-answers below. "
        "Keep it factual and avoid speculation.\n\n"
        f"Main question: {question}\n\nSub-answers:\n{bullets}"
    )
    return call_openai(
        client,
        model=model,
        instructions=RESEARCHER_QA_PROMPT,
        user_input=synthesis_prompt,
        max_output_tokens=None,
        usage_context="agent_synthesis",
        run_id=run_id,
        step="agentic_synthesis",
        question_id="MAIN",
    ).strip()


def _format_citations_context(citations: List[Dict[str, Any]], max_items: int) -> str:
    """Format extracted citations into a compact context block for prompting.

    Args:
        citations (List[Dict[str, Any]]): Description.
        max_items (int): Description.

    Returns:
        str: Description.
    """
    if not citations:
        return ""
    items = citations[:max_items]
    lines = ["Extracted citations (preview):"]
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            lines.append(f"{idx}. {item}")
            continue
        title = item.get("title") or item.get("citation") or item.get("ref") or "Unknown title"
        year = item.get("year")
        authors = item.get("authors")
        author_txt = ""
        if isinstance(authors, list) and authors:
            author_txt = ", ".join([str(a) for a in authors[:3]])
            if len(authors) > 3:
                author_txt += " et al."
        elif isinstance(authors, str):
            author_txt = authors
        suffix = []
        if author_txt:
            suffix.append(author_txt)
        if year:
            suffix.append(str(year))
        meta = f" ({'; '.join(suffix)})" if suffix else ""
        lines.append(f"{idx}. {title}{meta}")
    return "\n".join(lines)


def _split_context_chunks(context: str) -> List[Dict[str, Any]]:
    """Parse provenance-tagged context text into structured chunk metadata.

    Args:
        context (str): Description.

    Returns:
        List[Dict[str, Any]]: Description.
    """
    chunks: List[Dict[str, Any]] = []
    if not context:
        return chunks
    parts = [p for p in context.split("\n\n") if p.strip()]
    pattern = re.compile(r"^\(page\s+(?P<page>\d+)\s+words\s+(?P<start>\d+)-(?P<end>\d+)(?:\s+section\s+(?P<section>[^)]+))?\)\s*$")
    for part in parts:
        lines = part.splitlines()
        if not lines:
            continue
        meta = lines[0].strip()
        match = pattern.match(meta)
        if not match:
            continue
        text = "\n".join(lines[1:]).strip()
        chunks.append(
            {
                "page": int(match.group("page")),
                "start_word": int(match.group("start")),
                "end_word": int(match.group("end")),
                "section": (match.group("section") or "").strip() or None,
                "text": text,
            }
        )
    return chunks


def _confidence_from_retrieval_stats(stats: Dict[str, Any] | None) -> tuple[str, float, str]:
    """Convert retrieval statistics into a confidence label and numeric score.

    Args:
        stats (Dict[str, Any] | None): Description.

    Returns:
        tuple[str, float, str]: Description.
    """
    if not stats or int(stats.get("top_k", 0) or 0) <= 0:
        return "low", 0.0, "unknown"
    method = str(stats.get("method") or "local")
    score_mean = float(stats.get("score_mean") or 0.0)
    score_min = float(stats.get("score_min") or score_mean)
    score_max = float(stats.get("score_max") or score_mean)
    if method == "local":
        score = max(0.0, min(1.0, score_mean))
    else:
        spread = score_max - score_min
        if spread > 0:
            score = (score_mean - score_min) / spread
        else:
            score = 0.5
        score = max(0.0, min(1.0, score))
    if score >= 0.6:
        label = "high"
    elif score >= 0.35:
        label = "medium"
    else:
        label = "low"
    return label, score, method


def _build_report_prompt(
    *,
    question: str,
    context: str,
    anchors: List[Dict[str, Any]],
) -> str:
    """Build the JSON-output prompt for a structured report question.

    Args:
        question (str): Description.
        context (str): Description.
        anchors (List[Dict[str, Any]]): Description.

    Returns:
        str: Description.
    """
    anchor_lines = []
    for item in anchors:
        section = f" section {item['section']}" if item.get("section") else ""
        anchor_lines.append(
            f"- page {item['page']} words {item['start_word']}-{item['end_word']}{section}"
        )
    anchor_block = "\n".join(anchor_lines) if anchor_lines else "None"
    return (
        "Answer the question using only the provided context. Return a single JSON object with keys:\n"
        "answer (string), evidence_type (string), confidence (high|medium|low),\n"
        "citation_anchors (list of {page,start_word,end_word,section,note}),\n"
        "quote_snippet (short verbatim snippet from context, <= 200 chars),\n"
        "table_figure (string or null), data_source (string or null),\n"
        "assumption_flag (boolean or null), assumption_notes (string or null),\n"
        "related_questions (list of ids, or empty list).\n\n"
        f"Available anchors:\n{anchor_block}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}"
    )


REPORT_QUESTION_SECTIONS: List[tuple[str, str, List[str]]] = [
    (
        "A",
        "Research question / contribution",
        [
            "What is the main research question of the paper?",
            "What is the paper's primary contribution relative to the existing literature?",
            "What is the central hypothesis being tested?",
            "What are the main outcomes of interest (dependent variables)?",
            "What are the key treatment/exposure variables (independent variables)?",
            "What setting/context does the paper study (country, market, period)?",
            "What is the main mechanism proposed by the authors?",
            "What alternative mechanisms are discussed?",
            "What are the main policy implications claimed by the paper?",
            "What is the welfare interpretation (if any) of the results?",
            "What are the main limitations acknowledged by the authors?",
            "What does the paper claim is novel about its data or identification?",
        ],
    ),
    (
        "B",
        "Identification strategy / causal design",
        [
            "What is the identification strategy (in one sentence)?",
            "Is the design experimental, quasi-experimental, or observational?",
            "What is the source of exogenous variation used for identification?",
            "What is the treatment definition and timing?",
            "What is the control/comparison group definition?",
            "What is the estimating equation / baseline regression specification?",
            "What fixed effects are included (unit, time, two-way, higher dimensional)?",
            "What standard errors are used (robust, clustered; at what level)?",
            "What is the key identifying assumption (parallel trends, exclusion restriction, ignorability)?",
            "What evidence is provided to support the identifying assumption?",
            "Are there event-study or pre-trend tests? What do they show?",
            "What instruments are used (if IV)? Define instrument and first stage.",
            "What is the first-stage strength (F-stat, partial R^2, relevance evidence)?",
            "If RDD: what is the running variable and cutoff? bandwidth choice?",
            "If DiD: what is the timing variation (staggered adoption)? estimator used?",
        ],
    ),
    (
        "C",
        "Data, sample, and measurement",
        [
            "What dataset(s) are used? (name sources explicitly)",
            "What is the unit of observation (individual, household, firm, county, transaction, product)?",
            "What is the sample period and geographic coverage?",
            "What are the sample restrictions / inclusion criteria?",
            "What is the sample size (N) in the main analysis?",
            "How is the key outcome measured? Any transformations (logs, z-scores, indices)?",
            "How is treatment/exposure measured? Any constructed variables?",
            "Are there key covariates/controls? Which ones are always included?",
            "How are missing data handled (dropping, imputation, weighting)?",
            "Are weights used (survey weights, propensity weights)? How?",
            "Are data linked/merged across sources? How is linkage performed?",
            "What summary statistics are reported for main variables?",
            "Are there descriptive figures/maps that establish baseline patterns?",
        ],
    ),
    (
        "D",
        "Results, magnitudes, heterogeneity, robustness",
        [
            "What is the headline main effect estimate (sign and magnitude)?",
            "What is the preferred specification and why is it preferred?",
            "How economically meaningful is the effect (percent change, elasticity, dollars)?",
            "What are the key robustness checks and do results survive them?",
            "What placebo tests are run and what do they show?",
            "What falsification outcomes are tested (unaffected outcomes)?",
            "What heterogeneity results are reported (by income, size, baseline exposure, region)?",
            "What mechanism tests are performed and what do they imply?",
            "How sensitive are results to alternative samples/bandwidths/controls?",
            "What are the main takeaways in the conclusion (bullet summary)?",
        ],
    ),
    (
        "E",
        "Citations and related literature",
        [
            "What are the most important prior papers cited and why are they central here?",
            "Which papers does this work most directly build on or extend?",
            "Which papers are used as benchmarks or comparisons in the results?",
            "What data sources or datasets are cited and how are they used?",
            "What methodological or econometric references are cited (e.g., DiD, IV, RDD methods)?",
            "Are there any seminal or classic references the paper positions itself against?",
            "Are there citations to code, data repositories, or appendices that are essential to the claims?",
            "What gaps in the literature do the authors say these citations leave open?",
        ],
    ),
    (
        "F",
        "Replication and transparency",
        [
            "Are replication files or code provided? If so, where?",
            "Is there a pre-analysis plan or registered trial? Provide details if mentioned.",
            "Are data access constraints disclosed (restricted access, proprietary data, NDAs)?",
            "Are key steps in data cleaning and construction documented?",
            "Are robustness and sensitivity analyses fully reported or partially omitted?",
        ],
    ),
    (
        "G",
        "External validity and generalization",
        [
            "What populations or settings are most likely to generalize from this study?",
            "What populations or settings are least likely to generalize?",
            "Do the authors discuss boundary conditions or scope limits?",
            "How might the results change in different time periods or markets?",
        ],
    ),
    (
        "H",
        "Measurement validity",
        [
            "Are key variables measured directly or via proxies?",
            "What measurement error risks are acknowledged or likely?",
            "Are there validation checks for key measures?",
            "Do the authors discuss construct validity for core outcomes?",
        ],
    ),
    (
        "I",
        "Policy counterfactuals and welfare",
        [
            "What policy counterfactuals are considered or implied?",
            "What are the main welfare tradeoffs or distributional impacts discussed?",
            "Are cost-benefit or incidence analyses provided?",
            "What policy recommendations are stated or implied?",
        ],
    ),
    (
        "J",
        "Data quality and integrity",
        [
            "What missingness or attrition patterns are reported?",
            "How are outliers handled (winsorization, trimming, exclusions)?",
            "Are there data audits or validation steps described?",
            "Is there evidence of reporting bias or selective sample inclusion?",
        ],
    ),
    (
        "K",
        "Model fit and diagnostics",
        [
            "What goodness-of-fit or diagnostic metrics are reported?",
            "Are functional form choices tested (logs, levels, nonlinearities)?",
            "Are residual checks or specification tests reported?",
            "How sensitive are results to alternative specifications or estimators?",
        ],
    ),
]


def _normalize_report_question_set(value: str | None, enabled_default: bool) -> str:
    """Normalize report question set mode to a supported value.

    Args:
        value (str | None): Description.
        enabled_default (bool): Description.

    Returns:
        str: Description.
    """
    if not value:
        return "structured" if enabled_default else "none"
    text = value.strip().lower()
    if text in {"structured", "full", "fixed"}:
        return "structured"
    if text in {"agentic", "previous", "subquestions", "legacy"}:
        return "agentic"
    if text in {"both", "all"}:
        return "both"
    if text in {"none", "off", "0"}:
        return "none"
    return "structured"


def _build_report_questions() -> List[Dict[str, str]]:
    """Construct the structured report question definitions.

    Returns:
        List[Dict[str, str]]: Description.
    """
    questions: List[Dict[str, str]] = []
    for section_key, section_title, items in REPORT_QUESTION_SECTIONS:
        for idx, question in enumerate(items, start=1):
            questions.append(
                {
                    "id": f"{section_key}{idx:02d}",
                    "category": f"{section_key}) {section_title}",
                    "question": question,
                }
            )
    return questions


def _report_questions_from_sub_answers(sub_answers: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Convert subquestion answers into report-question style entries.

    Args:
        sub_answers (List[Dict[str, str]]): Description.

    Returns:
        List[Dict[str, str]]: Description.
    """
    report: List[Dict[str, str]] = []
    for idx, item in enumerate(sub_answers, start=1):
        report.append(
            {
                "id": f"P{idx:02d}",
                "category": "P) Previous questions",
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "question_tokens_estimate": item.get("question_tokens_estimate"),
            }
        )
    return report


def _summarize_confidence_scores(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize confidence scores and labels across report-question answers.

    Args:
        items (List[Dict[str, Any]]): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    scores: List[float] = []
    labels: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("confidence") or "").lower().strip()
        if label in labels:
            labels[label] += 1
        score = item.get("confidence_score")
        if score is None:
            continue
        try:
            scores.append(float(score))
        except Exception:
            continue
    if not scores:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "min": 0.0,
            "max": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "label_counts": labels,
        }
    scores_sorted = sorted(scores)
    n = len(scores_sorted)
    mid = n // 2
    if n % 2 == 0:
        median = (scores_sorted[mid - 1] + scores_sorted[mid]) / 2
    else:
        median = scores_sorted[mid]

    def _percentile(p: float) -> float:
        """Percentile.

        Args:
            p (float): Description.

        Returns:
            float: Description.
        """
        if n == 1:
            return scores_sorted[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        if lo == hi:
            return scores_sorted[lo]
        frac = idx - lo
        return scores_sorted[lo] * (1 - frac) + scores_sorted[hi] * frac

    return {
        "count": n,
        "mean": sum(scores_sorted) / n,
        "median": median,
        "min": scores_sorted[0],
        "max": scores_sorted[-1],
        "p25": _percentile(0.25),
        "p75": _percentile(0.75),
        "label_counts": labels,
    }


def _answer_report_question_item(
    *,
    client: OpenAI,
    model: str,
    settings,
    chunks: List[Dict[str, Any]] | List[str],
    chunk_embeddings: List[List[float]],
    citations_context: str,
    item: Dict[str, str],
    run_id: str | None = None,
) -> Dict[str, Any]:
    """Answer one structured report question using retrieval and LLM output parsing.

    Args:
        client (OpenAI): Description.
        model (str): Description.
        settings (Any): Description.
        chunks (List[Dict[str, Any]] | List[str]): Description.
        chunk_embeddings (List[List[float]]): Description.
        citations_context (str): Description.
        item (Dict[str, str]): Description.
        run_id (str | None): Description.

    Returns:
        Dict[str, Any]: Description.
    """
    context, retrieval_stats = top_k_context(
        chunks,
        chunk_embeddings,
        query=item["question"],
        client=client,
        settings=settings,
        run_id=run_id,
        step="agentic_report_question_retrieval",
        question_id=item.get("id"),
        return_stats=True,
    )
    if citations_context:
        context = f"{context}\n\n{citations_context}"
    parsed_chunks = _split_context_chunks(context)
    retrieval_confidence, confidence_score, retrieval_method = _confidence_from_retrieval_stats(
        retrieval_stats
    )
    anchor_defaults = [
        {
            "page": c["page"],
            "start_word": c["start_word"],
            "end_word": c["end_word"],
            "section": c.get("section"),
            "note": None,
        }
        for c in parsed_chunks[: settings.top_k]
    ]
    prompt = _build_report_prompt(
        question=item["question"],
        context=context,
        anchors=parsed_chunks[: settings.top_k],
    )
    raw = call_openai(
        client,
        model=model,
        instructions="Return JSON only.",
        user_input=prompt,
        max_output_tokens=None,
        usage_context="agent_report_question",
        run_id=run_id,
        step="agentic_report_question_answer",
        question_id=item.get("id"),
    ).strip()
    parsed: Dict[str, Any] = {}
    try:
        parsed_json = extract_json(raw)
        if isinstance(parsed_json, dict):
            parsed = parsed_json
    except Exception:
        parsed = {}
    answer_text = str(parsed.get("answer") or raw).strip()
    citation_anchors = parsed.get("citation_anchors")
    if not isinstance(citation_anchors, list) or not citation_anchors:
        citation_anchors = anchor_defaults
    quote_snippet = parsed.get("quote_snippet")
    if not isinstance(quote_snippet, str) or not quote_snippet.strip():
        quote_snippet = ""
        if parsed_chunks and parsed_chunks[0].get("text"):
            quote_snippet = str(parsed_chunks[0]["text"])[:200]
    return {
        "id": item["id"],
        "category": item["category"],
        "question": item["question"],
        "answer": answer_text,
        "question_tokens_estimate": _estimate_tokens(item["question"]),
        "evidence_type": parsed.get("evidence_type") or "unspecified",
        "confidence": retrieval_confidence,
        "confidence_score": confidence_score,
        "retrieval_method": retrieval_method,
        "citation_anchors": citation_anchors,
        "quote_snippet": quote_snippet,
        "table_figure": parsed.get("table_figure"),
        "data_source": parsed.get("data_source"),
        "assumption_flag": parsed.get("assumption_flag"),
        "assumption_notes": parsed.get("assumption_notes"),
        "related_questions": parsed.get("related_questions") if isinstance(parsed.get("related_questions"), list) else [],
    }


def _answer_report_questions(
    client: OpenAI,
    *,
    model: str,
    settings,
    chunks: List[Dict[str, Any]] | List[str],
    chunk_embeddings: List[List[float]],
    citations_context: str,
    run_id: str | None = None,
    reusable_items_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Answer all structured report questions with optional reuse of prior results.

    Args:
        client (OpenAI): Description.
        model (str): Description.
        settings (Any): Description.
        chunks (List[Dict[str, Any]] | List[str]): Description.
        chunk_embeddings (List[List[float]]): Description.
        citations_context (str): Description.
        run_id (str | None): Description.
        reusable_items_by_id (Optional[Dict[str, Dict[str, Any]]]): Description.

    Returns:
        List[Dict[str, Any]]: Description.

    Raises:
        Exception: Description.
    """
    questions = _build_report_questions()
    if not questions:
        return []
    reusable_items_by_id = reusable_items_by_id or {}
    results: List[Dict[str, Any] | None] = [None] * len(questions)
    pending: List[tuple[int, Dict[str, str]]] = []
    for idx, item in enumerate(questions):
        reuse_entry = reusable_items_by_id.get(item["id"])
        if not isinstance(reuse_entry, dict):
            pending.append((idx, item))
            continue
        reused_item = reuse_entry.get("item")
        if not isinstance(reused_item, dict):
            pending.append((idx, item))
            continue
        reused_question = str(reused_item.get("question") or "").strip()
        if reused_question != str(item.get("question") or "").strip():
            pending.append((idx, item))
            continue
        reused_result = dict(reused_item)
        reused_result["id"] = item["id"]
        reused_result["category"] = item["category"]
        reused_result["question"] = item["question"]
        if reused_result.get("question_tokens_estimate") is None:
            reused_result["question_tokens_estimate"] = _estimate_tokens(item["question"])
        source_run_id = str(reuse_entry.get("source_run_id") or "")
        source_finished_at = reuse_entry.get("source_finished_at")
        results[idx] = _with_reuse_marker(
            reused_result,
            source_run_id=source_run_id,
            source_finished_at=source_finished_at,
        )
    if not pending:
        return [item for item in results if item is not None]
    try:
        worker_cap = int(os.environ.get("WORKFLOW_REPORT_QUESTION_WORKERS", "8"))
    except Exception:
        worker_cap = 8
    max_workers = max(1, min(worker_cap, len(pending)))
    if max_workers == 1:
        for idx, item in _progress_iter(pending, "Report questions", total=len(pending)):
            results[idx] = _answer_report_question_item(
                client=client,
                model=model,
                settings=settings,
                chunks=chunks,
                chunk_embeddings=chunk_embeddings,
                citations_context=citations_context,
                item=item,
                run_id=run_id,
            )
        return [item for item in results if item is not None]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(
                _answer_report_question_item,
                client=client,
                model=model,
                settings=settings,
                chunks=chunks,
                chunk_embeddings=chunk_embeddings,
                citations_context=citations_context,
                item=item,
                run_id=run_id,
            ): idx
            for idx, item in pending
        }
        for future in _progress_iter(as_completed(future_map), "Report questions", total=len(future_map)):
            idx = future_map[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                if _is_insufficient_quota_error(exc):
                    raise
                item = questions[idx]
                results[idx] = {
                    "id": item["id"],
                    "category": item["category"],
                    "question": item["question"],
                    "answer": f"ERROR: {exc}",
                    "question_tokens_estimate": _estimate_tokens(item["question"]),
                    "evidence_type": "unspecified",
                    "confidence": "low",
                    "confidence_score": 0.0,
                    "retrieval_method": "unknown",
                    "citation_anchors": [],
                    "quote_snippet": "",
                    "table_figure": None,
                    "data_source": None,
                    "assumption_flag": None,
                    "assumption_notes": None,
                    "related_questions": [],
                }
    return [item for item in results if item is not None]


def _answer_subquestion(
    *,
    client: OpenAI,
    model: str,
    settings,
    chunks: List[Dict[str, Any]] | List[str],
    chunk_embeddings: List[List[float]],
    citations_context: str,
    subq: str,
    run_id: str | None = None,
    question_id: str | None = None,
) -> Dict[str, str]:
    """Answer a single agentic subquestion using retrieved context.

    Args:
        client (OpenAI): Description.
        model (str): Description.
        settings (Any): Description.
        chunks (List[Dict[str, Any]] | List[str]): Description.
        chunk_embeddings (List[List[float]]): Description.
        citations_context (str): Description.
        subq (str): Description.
        run_id (str | None): Description.
        question_id (str | None): Description.

    Returns:
        Dict[str, str]: Description.
    """
    context = top_k_context(
        chunks,
        chunk_embeddings,
        query=subq,
        client=client,
        settings=settings,
        run_id=run_id,
        step="agentic_subquestion_retrieval",
        question_id=question_id,
    )
    if citations_context:
        context = f"{context}\n\n{citations_context}"
    answer = call_openai(
        client,
        model=model,
        instructions=RESEARCHER_QA_PROMPT,
        user_input=f"Context:\n{context}\n\nQuestion: {subq}",
        max_output_tokens=None,
        usage_context="agent_answer",
        run_id=run_id,
        step="agentic_subquestion_answer",
        question_id=question_id,
    ).strip()
    return {
        "question": subq,
        "answer": answer,
        "question_tokens_estimate": _estimate_tokens(subq),
    }


def run_workflow(
    *,
    papers_dir: Path,
    config_path: Optional[Path] = None,
    meta_db_url: Optional[str] = None,
    report_dir: Optional[Path] = None,
    state_db: Path = DEFAULT_STATE_DB,
    agentic: Optional[bool] = None,
    question: Optional[str] = None,
    agentic_model: Optional[str] = None,
    agentic_max_subquestions: Optional[int] = None,
    agentic_citations: Optional[bool] = None,
    agentic_citations_max_items: Optional[int] = None,
    report_question_set: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    trigger_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the multi-step workflow and persist state transitions.

    Args:
        papers_dir (Path): Description.
        config_path (Optional[Path]): Description.
        meta_db_url (Optional[str]): Description.
        report_dir (Optional[Path]): Description.
        state_db (Path): Description.
        agentic (Optional[bool]): Description.
        question (Optional[str]): Description.
        agentic_model (Optional[str]): Description.
        agentic_max_subquestions (Optional[int]): Description.
        agentic_citations (Optional[bool]): Description.
        agentic_citations_max_items (Optional[int]): Description.
        report_question_set (Optional[str]): Description.
        workstream_id (Optional[str]): Description.
        arm (Optional[str]): Description.
        parent_run_id (Optional[str]): Description.
        trigger_source (Optional[str]): Description.

    Returns:
        Dict[str, Any]: Description.

    Raises:
        Exception: Description.
    """
    settings = load_settings(config_path=config_path)
    run_id = uuid4().hex
    started_at = _utc_now()
    question_seed = (question or os.environ.get("WORKFLOW_QUESTION") or "").strip()
    if not question_seed:
        question_seed = "Summarize the paper's research question, methods, and key findings."
    report_questions_enabled_default = os.environ.get("WORKFLOW_REPORT_QUESTIONS", "1").strip() != "0"
    report_question_mode_seed = _normalize_report_question_set(
        report_question_set or os.environ.get("WORKFLOW_REPORT_QUESTIONS_SET"),
        report_questions_enabled_default,
    )
    resolved_workstream_id = (
        (workstream_id or os.environ.get("WORKSTREAM_ID") or os.environ.get("WORKFLOW_WORKSTREAM_ID") or "").strip()
        or None
    )
    resolved_arm = (arm or os.environ.get("WORKSTREAM_ARM") or os.environ.get("WORKFLOW_ARM") or "").strip() or None
    resolved_parent_run_id = (
        (parent_run_id or os.environ.get("WORKSTREAM_PARENT_RUN_ID") or "").strip() or None
    )
    resolved_trigger_source = (
        (trigger_source or os.environ.get("WORKFLOW_TRIGGER_SOURCE") or "").strip()
        or ("workflow_async" if os.environ.get("RQ_WORKER_ID") else "workflow_sync")
    )
    resolved_git_sha = (os.environ.get("GIT_SHA") or "").strip() or _git_value(["rev-parse", "HEAD"])
    resolved_git_branch = (os.environ.get("GIT_BRANCH") or "").strip() or _git_value(
        ["rev-parse", "--abbrev-ref", "HEAD"]
    )
    create_workflow_run(
        state_db,
        run_id=run_id,
        papers_dir=str(papers_dir),
        config_hash=settings.config_hash,
        status="running",
        started_at=started_at,
        workstream_id=resolved_workstream_id,
        arm=resolved_arm,
        parent_run_id=resolved_parent_run_id,
        trigger_source=resolved_trigger_source,
        git_sha=resolved_git_sha,
        git_branch=resolved_git_branch,
        config_effective=settings.config_effective or {},
        question=question_seed,
        report_question_set=report_question_mode_seed,
        metadata={"config_path": str(settings.config_path) if settings.config_path else None},
    )

    report_dir = report_dir or Path("reports")
    summary: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "papers_dir": str(papers_dir),
        "config": asdict(settings),
        "workstream_id": resolved_workstream_id,
        "arm": resolved_arm,
        "parent_run_id": resolved_parent_run_id,
        "trigger_source": resolved_trigger_source,
        "git_sha": resolved_git_sha,
        "git_branch": resolved_git_branch,
    }
    requested_db_url = meta_db_url or os.environ.get("DATABASE_URL")
    db_url, db_url_resolution = _resolve_meta_db_url(requested_db_url)
    summary["usage_store"] = {"database_url": bool(db_url)}
    summary["db_url_resolution"] = db_url_resolution
    step_reuse_enabled = _bool_env("WORKFLOW_REUSE_STEPS", True)
    papers_cache: List[Any] | None = None

    def _ensure_papers_loaded() -> List[Any]:
        """Ensure papers loaded.

        Returns:
            List[Any]: Description.
        """
        nonlocal papers_cache
        if papers_cache is None:
            papers_cache = load_papers(pdfs, progress=True, progress_desc="Ingesting papers")
        return papers_cache

    def _find_reusable_step(
        step_name: str,
        *,
        match_question: bool = False,
        match_report_question_set: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Find reusable step.

        Args:
            step_name (str): Description.
            match_question (bool): Description.
            match_report_question_set (bool): Description.

        Returns:
            Optional[Dict[str, Any]]: Description.
        """
        if not step_reuse_enabled:
            return None
        try:
            return find_similar_completed_step(
                state_db,
                step=step_name,
                exclude_run_id=run_id,
                config_hash=settings.config_hash,
                papers_dir=str(papers_dir),
                paper_set_hash=summary.get("paper_set_hash"),
                workstream_id=resolved_workstream_id,
                arm=resolved_arm,
                question=question_seed,
                report_question_set=report_question_mode_seed,
                match_question=match_question,
                match_report_question_set=match_report_question_set,
            )
        except Exception:
            return None

    # Step 0: Prep (corpus profiling)
    prep_start = _utc_now()
    record_step(state_db, run_id=run_id, step="prep", status="running", started_at=prep_start)
    pdfs = _resolve_paper_paths(Path(papers_dir))
    reused_prep = _find_reusable_step("prep")
    if reused_prep:
        prep_out = _with_reuse_marker(
            reused_prep.get("output") or {},
            source_run_id=str(reused_prep.get("run_id") or ""),
            source_finished_at=reused_prep.get("finished_at"),
        )
    else:
        prep_out = prep_corpus(pdfs, report_dir=report_dir, run_id=run_id)
    record_step(
        state_db,
        run_id=run_id,
        step="prep",
        status="completed" if prep_out.get("status") == "completed" else "failed",
        started_at=prep_start,
        finished_at=_utc_now(),
        output=prep_out,
        status_reason="reused_from_prior_run" if reused_prep else None,
    )
    summary["prep"] = prep_out
    paper_set_hash = None
    prep_stats = prep_out.get("stats")
    if isinstance(prep_stats, dict):
        paper_set_hash = str(prep_stats.get("corpus_hash") or "").strip() or None
    if paper_set_hash:
        summary["paper_set_hash"] = paper_set_hash
        create_workflow_run(
            state_db,
            run_id=run_id,
            papers_dir=str(papers_dir),
            config_hash=settings.config_hash,
            status="running",
            started_at=started_at,
            workstream_id=resolved_workstream_id,
            arm=resolved_arm,
            parent_run_id=resolved_parent_run_id,
            trigger_source=resolved_trigger_source,
            git_sha=resolved_git_sha,
            git_branch=resolved_git_branch,
            config_effective=settings.config_effective or {},
            paper_set_hash=paper_set_hash,
            question=question_seed,
            report_question_set=report_question_mode_seed,
            metadata={"config_path": str(settings.config_path) if settings.config_path else None},
        )

    validate_only = os.environ.get("PREP_VALIDATE_ONLY", "").strip() == "1"
    if prep_out.get("status") == "failed":
        summary["finished_at"] = _utc_now()
        report_start = _utc_now()
        record_step(state_db, run_id=run_id, step="report", status="running", started_at=report_start)
        return _finalize_workflow_report(
            report_dir=report_dir,
            run_id=run_id,
            summary=summary,
            state_db=state_db,
            report_started_at=report_start,
            db_url=db_url,
            workflow_status="failed",
        )

    if validate_only:
        summary["finished_at"] = _utc_now()
        report_start = _utc_now()
        record_step(state_db, run_id=run_id, step="report", status="running", started_at=report_start)
        return _finalize_workflow_report(
            report_dir=report_dir,
            run_id=run_id,
            summary=summary,
            state_db=state_db,
            report_started_at=report_start,
            db_url=db_url,
            workflow_status="completed",
        )

    # Step 1: Ingest
    ingest_start = _utc_now()
    record_step(state_db, run_id=run_id, step="ingest", status="running", started_at=ingest_start)
    reused_ingest = _find_reusable_step("ingest")
    if reused_ingest:
        ingest_out = _with_reuse_marker(
            reused_ingest.get("output") or {},
            source_run_id=str(reused_ingest.get("run_id") or ""),
            source_finished_at=reused_ingest.get("finished_at"),
        )
    else:
        papers = _ensure_papers_loaded()
        ingest_out = {"num_pdfs": len(pdfs), "num_papers": len(papers)}
    record_step(
        state_db,
        run_id=run_id,
        step="ingest",
        status="completed",
        started_at=ingest_start,
        finished_at=_utc_now(),
        output=ingest_out,
        status_reason="reused_from_prior_run" if reused_ingest else None,
    )
    summary["ingest"] = ingest_out

    # Step 2: Enrich
    enrich_start = _utc_now()
    record_step(state_db, run_id=run_id, step="enrich", status="running", started_at=enrich_start)
    reused_enrich = _find_reusable_step("enrich")
    if reused_enrich:
        enrich_out = _with_reuse_marker(
            reused_enrich.get("output") or {},
            source_run_id=str(reused_enrich.get("run_id") or ""),
            source_finished_at=reused_enrich.get("finished_at"),
        )
    else:
        papers = _ensure_papers_loaded()
        openalex_count = sum(1 for p in papers if getattr(p, "openalex", None))
        citec_count = sum(1 for p in papers if getattr(p, "citec", None))
        enrich_out = {"openalex": openalex_count, "citec": citec_count}
    record_step(
        state_db,
        run_id=run_id,
        step="enrich",
        status="completed",
        started_at=enrich_start,
        finished_at=_utc_now(),
        output=enrich_out,
        status_reason="reused_from_prior_run" if reused_enrich else None,
    )
    summary["enrich"] = enrich_out

    # Step 3: Econ data (optional)
    econ_start = _utc_now()
    record_step(state_db, run_id=run_id, step="econ_data", status="running", started_at=econ_start)
    reused_econ = _find_reusable_step("econ_data")
    if reused_econ:
        econ_out = _with_reuse_marker(
            reused_econ.get("output") or {},
            source_run_id=str(reused_econ.get("run_id") or ""),
            source_finished_at=reused_econ.get("finished_at"),
        )
    else:
        econ_out = {"status": "skipped"}
        series_env = os.environ.get("ECON_SERIES_IDS", "").strip()
        series_ids = [s.strip() for s in series_env.split(",") if s.strip()] if series_env else []
        if os.environ.get("FRED_API_KEY") or series_ids:
            if not series_ids:
                series_ids = ["GDPC1", "FEDFUNDS"]
            series_counts = {}
            for series_id in series_ids:
                obs = fetch_fred_series(series_id, limit=120)
                series_counts[series_id] = len(obs)
            econ_out = {"status": "fetched", "series_counts": series_counts}
    record_step(
        state_db,
        run_id=run_id,
        step="econ_data",
        status="completed",
        started_at=econ_start,
        finished_at=_utc_now(),
        output=econ_out,
        status_reason="reused_from_prior_run" if reused_econ else None,
    )
    summary["econ_data"] = econ_out

    # Step 4: Agentic workflow (optional)
    agentic_enabled = agentic if agentic is not None else os.environ.get("WORKFLOW_AGENTIC", "").strip() == "1"
    question = (question or os.environ.get("WORKFLOW_QUESTION") or "").strip()
    if not question:
        question = "Summarize the paper's research question, methods, and key findings."
    agentic_model = agentic_model or os.environ.get("WORKFLOW_AGENTIC_MODEL") or settings.chat_model
    try:
        max_subq = int(agentic_max_subquestions or os.environ.get("WORKFLOW_AGENTIC_MAX_SUBQUESTIONS", "3"))
    except Exception:
        max_subq = 3
    citations_enabled = agentic_citations
    if citations_enabled is None:
        citations_enabled = os.environ.get("WORKFLOW_AGENTIC_CITATIONS", "").strip() == "1"
    try:
        max_citations = int(agentic_citations_max_items or os.environ.get("WORKFLOW_AGENTIC_CITATIONS_MAX", "12"))
    except Exception:
        max_citations = 12
    agentic_start = _utc_now()
    record_step(state_db, run_id=run_id, step="agentic", status="running", started_at=agentic_start)
    agentic_out: Dict[str, Any] = {"status": "skipped"}
    agentic_quota_error: Exception | None = None
    reused_agentic = (
        _find_reusable_step("agentic", match_question=True, match_report_question_set=True)
        if agentic_enabled
        else None
    )
    if reused_agentic:
        agentic_out = _with_reuse_marker(
            reused_agentic.get("output") or {},
            source_run_id=str(reused_agentic.get("run_id") or ""),
            source_finished_at=reused_agentic.get("finished_at"),
        )
    elif agentic_enabled:
        papers = _ensure_papers_loaded()
        if not papers:
            agentic_out = {"status": "skipped", "reason": "no_papers"}
        else:
            try:
                client = OpenAI()
                target_paper = papers[0]
                citations_context = ""
                citations_preview: List[Dict[str, Any]] = []
                citations_error = None
                if citations_enabled:
                    try:
                        citations_full = llm_extract_citations(
                            paper_path=target_paper.path,
                            model=agentic_model,
                            api_key=os.environ.get("OPENAI_API_KEY"),
                        )
                        if isinstance(citations_full, list):
                            citations_preview = citations_full[:max_citations]
                            citations_context = _format_citations_context(citations_full, max_citations)
                    except Exception as exc:
                        if _is_insufficient_quota_error(exc):
                            raise
                        citations_error = str(exc)
                chunks = prepare_chunks_for_paper(target_paper, settings)
                chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
                chunk_embeddings = embed_texts(
                    client,
                    chunk_texts,
                    settings.embedding_model,
                    settings.batch_size,
                    run_id=run_id,
                    step="agentic_embeddings",
                    question_id="MAIN",
                )
                subquestions = _agentic_plan(
                    client,
                    question,
                    model=agentic_model,
                    max_items=max_subq,
                    run_id=run_id,
                )
                sub_answers: List[Dict[str, str]] = []
                for idx, subq in enumerate(
                    _progress_iter(subquestions, "Agentic sub-questions", total=len(subquestions)),
                    start=1,
                ):
                    sub_answers.append(
                        _answer_subquestion(
                            client=client,
                            model=agentic_model,
                            settings=settings,
                            chunks=chunks,
                            chunk_embeddings=chunk_embeddings,
                            citations_context=citations_context,
                            subq=subq,
                            run_id=run_id,
                            question_id=f"S{idx:02d}",
                        )
                    )
                report_questions_enabled = os.environ.get("WORKFLOW_REPORT_QUESTIONS", "1").strip() != "0"
                report_question_mode = _normalize_report_question_set(
                    report_question_set or os.environ.get("WORKFLOW_REPORT_QUESTIONS_SET"),
                    report_questions_enabled,
                )
                report_questions_enabled = report_question_mode != "none"
                report_questions: List[Dict[str, Any]] = []
                report_questions_error = None
                reusable_structured_questions: Dict[str, Dict[str, Any]] = {}
                if step_reuse_enabled and report_question_mode in {"structured", "both"}:
                    try:
                        reusable_structured_questions = find_similar_report_question_items(
                            state_db,
                            exclude_run_id=run_id,
                            config_hash=settings.config_hash,
                            papers_dir=str(papers_dir),
                            paper_set_hash=summary.get("paper_set_hash"),
                            workstream_id=resolved_workstream_id,
                            arm=resolved_arm,
                            question=question,
                            report_question_set=report_question_mode,
                            match_question=True,
                            match_report_question_set=False,
                        )
                    except Exception:
                        reusable_structured_questions = {}
                if report_question_mode in {"structured", "both"}:
                    try:
                        report_questions = _answer_report_questions(
                            client,
                            model=agentic_model,
                            settings=settings,
                            chunks=chunks,
                            chunk_embeddings=chunk_embeddings,
                            citations_context=citations_context,
                            run_id=run_id,
                            reusable_items_by_id=reusable_structured_questions,
                        )
                    except Exception as exc:
                        report_questions_error = str(exc)
                if report_question_mode in {"agentic", "both"}:
                    report_questions.extend(_report_questions_from_sub_answers(sub_answers))
                final_answer = _agentic_summarize(
                    client,
                    model=agentic_model,
                    question=question,
                    sub_answers=sub_answers,
                    run_id=run_id,
                )
                report_question_summary = _summarize_confidence_scores(report_questions)
                report_questions_reused_count = sum(
                    1
                    for item in report_questions
                    if isinstance(item, dict) and isinstance(item.get("_reused_from"), dict)
                )
                agentic_out = {
                    "status": "completed",
                    "question": question,
                    "question_tokens_estimate": _estimate_tokens(question),
                    "subquestions": subquestions,
                    "sub_answers": sub_answers,
                    "final_answer": final_answer,
                    "report_questions_enabled": report_questions_enabled,
                    "report_questions_set": report_question_mode,
                    "report_questions": report_questions,
                    "report_questions_reused_count": report_questions_reused_count,
                    "report_question_confidence": report_question_summary,
                    "report_questions_error": report_questions_error,
                    "citations_enabled": citations_enabled,
                    "citations_preview": citations_preview,
                    "citations_error": citations_error,
                }
            except Exception as exc:
                agentic_out = {"status": "failed", "error": str(exc)}
                if _is_insufficient_quota_error(exc):
                    agentic_quota_error = exc
    agentic_step_status = "failed" if agentic_out.get("status") == "failed" else "completed"
    record_step(
        state_db,
        run_id=run_id,
        step="agentic",
        status=agentic_step_status,
        started_at=agentic_start,
        finished_at=_utc_now(),
        output=agentic_out,
        status_reason="reused_from_prior_run" if reused_agentic else None,
    )
    summary["agentic"] = agentic_out
    if agentic_quota_error is not None:
        return _finalize_quota_termination(
            report_dir=report_dir,
            run_id=run_id,
            summary=summary,
            state_db=state_db,
            db_url=db_url,
            step="agentic",
            exc=agentic_quota_error,
        )

    # Step 5: Index (optional)
    index_start = _utc_now()
    record_step(state_db, run_id=run_id, step="index", status="running", started_at=index_start)
    reused_index = _find_reusable_step("index")
    db_ok = bool(db_url) and _can_connect_db(db_url)
    index_out: Dict[str, Any] = {"database_url": bool(db_url), "database_reachable": db_ok}
    index_quota_error: Exception | None = None
    if reused_index:
        index_out = _with_reuse_marker(
            reused_index.get("output") or {},
            source_run_id=str(reused_index.get("run_id") or ""),
            source_finished_at=reused_index.get("finished_at"),
        )
    elif db_ok and pdfs:
        try:
            build_index(
                settings,
                pdfs,
                index_path=Path("vectors-3072.index"),
                meta_db_url=db_url,
                workflow_run_id=run_id,
                workstream_id=resolved_workstream_id,
                arm=resolved_arm,
                paper_set_hash=summary.get("paper_set_hash"),
                index_build_reason="workflow_index_step",
            )
            index_out["status"] = "indexed"
        except Exception as exc:
            index_out["status"] = "failed"
            index_out["error"] = str(exc)
            if _is_insufficient_quota_error(exc):
                index_quota_error = exc
    else:
        index_out["status"] = "skipped"
        if db_url and not db_ok:
            index_out["reason"] = "db_unreachable"
    index_step_status = "failed" if index_out.get("status") == "failed" else "completed"
    record_step(
        state_db,
        run_id=run_id,
        step="index",
        status=index_step_status,
        started_at=index_start,
        finished_at=_utc_now(),
        output=index_out,
        status_reason="reused_from_prior_run" if reused_index else None,
    )
    summary["index"] = index_out
    if index_quota_error is not None:
        return _finalize_quota_termination(
            report_dir=report_dir,
            run_id=run_id,
            summary=summary,
            state_db=state_db,
            db_url=db_url,
            step="index",
            exc=index_quota_error,
        )

    # Step 6: Evaluate (lightweight stats)
    eval_start = _utc_now()
    record_step(state_db, run_id=run_id, step="evaluate", status="running", started_at=eval_start)
    reused_evaluate = _find_reusable_step("evaluate")
    if reused_evaluate:
        eval_out = _with_reuse_marker(
            reused_evaluate.get("output") or {},
            source_run_id=str(reused_evaluate.get("run_id") or ""),
            source_finished_at=reused_evaluate.get("finished_at"),
        )
    else:
        papers = _ensure_papers_loaded()
        chunk_counts = []
        for paper in papers:
            chunks = prepare_chunks_for_paper(paper, settings)
            chunk_counts.append(len(chunks))
        eval_out = {
            "avg_chunks_per_paper": (sum(chunk_counts) / len(chunk_counts)) if chunk_counts else 0,
            "max_chunks": max(chunk_counts) if chunk_counts else 0,
            "min_chunks": min(chunk_counts) if chunk_counts else 0,
        }
    record_step(
        state_db,
        run_id=run_id,
        step="evaluate",
        status="completed",
        started_at=eval_start,
        finished_at=_utc_now(),
        output=eval_out,
        status_reason="reused_from_prior_run" if reused_evaluate else None,
    )
    summary["evaluate"] = eval_out

    # Step 7: Report
    report_start = _utc_now()
    record_step(state_db, run_id=run_id, step="report", status="running", started_at=report_start)
    summary["finished_at"] = _utc_now()
    return _finalize_workflow_report(
        report_dir=report_dir,
        run_id=run_id,
        summary=summary,
        state_db=state_db,
        report_started_at=report_start,
        db_url=db_url,
        workflow_status="completed",
    )


def workflow_entrypoint(
    papers_dir: str,
    config_path: Optional[str] = None,
    meta_db_url: Optional[str] = None,
    agentic: Optional[bool] = None,
    question: Optional[str] = None,
    agentic_model: Optional[str] = None,
    agentic_citations: Optional[bool] = None,
    report_question_set: Optional[str] = None,
    workstream_id: Optional[str] = None,
    arm: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    trigger_source: Optional[str] = None,
) -> str:
    """Helper for queue execution. Returns run_id for logging.

    Args:
        papers_dir (str): Description.
        config_path (Optional[str]): Description.
        meta_db_url (Optional[str]): Description.
        agentic (Optional[bool]): Description.
        question (Optional[str]): Description.
        agentic_model (Optional[str]): Description.
        agentic_citations (Optional[bool]): Description.
        report_question_set (Optional[str]): Description.
        workstream_id (Optional[str]): Description.
        arm (Optional[str]): Description.
        parent_run_id (Optional[str]): Description.
        trigger_source (Optional[str]): Description.

    Returns:
        str: Description.
    """
    summary = run_workflow(
        papers_dir=Path(papers_dir),
        config_path=Path(config_path) if config_path else None,
        meta_db_url=meta_db_url,
        agentic=agentic,
        question=question,
        agentic_model=agentic_model,
        agentic_citations=agentic_citations,
        report_question_set=report_question_set,
        workstream_id=workstream_id,
        arm=arm,
        parent_run_id=parent_run_id,
        trigger_source=trigger_source,
    )
    return summary.get("run_id", "")
