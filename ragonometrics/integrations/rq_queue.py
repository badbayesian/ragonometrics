"""Postgres-backed async queue for workflow/index jobs.

This module replaces Redis/RQ usage with a queue table in Postgres
(`workflow.async_jobs`) and a lightweight polling worker.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ragonometrics.db.connection import connect, ensure_schema_ready

from ragonometrics.core.main import load_settings
from ragonometrics.indexing.indexer import build_index
from ragonometrics.pipeline.workflow import workflow_entrypoint


DEFAULT_QUEUE_NAME = "default"
DEFAULT_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class EnqueuedJob:
    """Simple enqueue response compatible with prior `.id` usage."""

    id: str
    job_type: str
    status: str


def _resolve_db_url(db_url: str | None) -> str:
    """Resolve db url.

    Args:
        db_url (str | None): Postgres connection URL.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    raw = (db_url or "").strip()
    if raw.startswith("redis://"):
        # Backward compatibility for old call sites that passed `redis_url`.
        raw = ""
    value = (raw or os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for Postgres-backed async queue.")
    return value


def _connect(db_url: str):
    """Connect.

    Args:
        db_url (str): Postgres connection URL.

    Returns:
        Any: Return value produced by the operation.
    """
    return connect(db_url, require_migrated=True)


def ensure_async_jobs_table(conn) -> None:
    """Ensure async jobs table.

    Args:
        conn (Any): Open database connection.
    """
    ensure_schema_ready(conn)


def _enqueue_job(
    *,
    db_url: str | None,
    queue_name: str,
    job_type: str,
    payload: Dict[str, Any],
    max_attempts: int = 3,
    retry_delay_seconds: int = 10,
) -> EnqueuedJob:
    """Enqueue job.

    Args:
        db_url (str | None): Postgres connection URL.
        queue_name (str): Queue name used for background processing.
        job_type (str): Asynchronous job type.
        payload (Dict[str, Any]): Payload data to persist or transmit.
        max_attempts (int): Input value for max attempts.
        retry_delay_seconds (int): Input value for retry delay seconds.

    Returns:
        EnqueuedJob: Result produced by the operation.
    """
    resolved_db_url = _resolve_db_url(db_url)
    job_id = uuid.uuid4().hex
    with _connect(resolved_db_url) as conn:
        ensure_async_jobs_table(conn)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO workflow.async_jobs
            (
                job_id, queue_name, job_type, status, payload_json,
                max_attempts, retry_delay_seconds, available_at, created_at, updated_at
            )
            VALUES
            (
                %s, %s, %s, 'queued', %s::jsonb,
                %s, %s, NOW(), NOW(), NOW()
            )
            """,
            (
                job_id,
                queue_name,
                job_type,
                json.dumps(payload, ensure_ascii=False),
                int(max_attempts),
                int(retry_delay_seconds),
            ),
        )
        conn.commit()
    return EnqueuedJob(id=job_id, job_type=job_type, status="queued")


def enqueue_index(
    papers: List[Path],
    db_url: str | None = None,
    *,
    config_path: Path | None = None,
    meta_db_url: str | None = None,
    index_path: Path | None = None,
    queue_name: str = DEFAULT_QUEUE_NAME,
):
    """Enqueue an indexing job in Postgres.

    Args:
        papers (List[Path]): Path to papers.
        db_url (str | None): Postgres connection URL.
        config_path (Path | None): Path to the configuration file.
        meta_db_url (str | None): Postgres metadata database URL.
        index_path (Path | None): Path to the vector index file.
        queue_name (str): Queue name used for background processing.

    Returns:
        Any: Return value produced by the operation.
    """

    payload = {
        "paper_paths": [str(p) for p in papers],
        "config_path": str(config_path) if config_path else None,
        "meta_db_url": meta_db_url,
        "index_path": str(index_path or Path("vectors-3072.index")),
    }
    return _enqueue_job(
        db_url=db_url,
        queue_name=queue_name,
        job_type="index",
        payload=payload,
    )


def enqueue_workflow(
    papers_dir: Path,
    db_url: str | None = None,
    config_path: Path | None = None,
    meta_db_url: str | None = None,
    *,
    agentic: bool | None = None,
    question: str | None = None,
    agentic_model: str | None = None,
    agentic_citations: bool | None = None,
    report_question_set: str | None = None,
    workstream_id: str | None = None,
    arm: str | None = None,
    parent_run_id: str | None = None,
    trigger_source: str | None = None,
    queue_name: str = DEFAULT_QUEUE_NAME,
):
    """Enqueue a multi-step workflow run in Postgres.

    Args:
        papers_dir (Path): Directory containing input paper files.
        db_url (str | None): Postgres connection URL.
        config_path (Path | None): Path to the configuration file.
        meta_db_url (str | None): Postgres metadata database URL.
        agentic (bool | None): Whether to enable agentic.
        question (str | None): Question text to answer.
        agentic_model (str | None): Model name used for the agentic workflow stage.
        agentic_citations (bool | None): Whether to enable agentic citations.
        report_question_set (str | None): Structured question set selector.
        workstream_id (str | None): Logical workstream identifier for grouping related runs.
        arm (str | None): Experiment arm label for this run.
        parent_run_id (str | None): Run identifier of the parent run, when applicable.
        trigger_source (str | None): Source that triggered the run.
        queue_name (str): Queue name used for background processing.

    Returns:
        Any: Return value produced by the operation.
    """

    payload = {
        "papers_dir": str(papers_dir),
        "config_path": str(config_path) if config_path else None,
        "meta_db_url": meta_db_url,
        "agentic": agentic,
        "question": question,
        "agentic_model": agentic_model,
        "agentic_citations": agentic_citations,
        "report_question_set": report_question_set,
        "workstream_id": workstream_id,
        "arm": arm,
        "parent_run_id": parent_run_id,
        "trigger_source": trigger_source,
    }
    return _enqueue_job(
        db_url=db_url,
        queue_name=queue_name,
        job_type="workflow",
        payload=payload,
    )


def enqueue_openalex_network_refresh(
    *,
    db_url: str | None = None,
    cache_key: str,
    center_work_id: str,
    n_hops: int,
    max_references: int,
    max_citing: int,
    max_nodes: int,
    algo_version: str,
    queue_name: str = DEFAULT_QUEUE_NAME,
) -> EnqueuedJob:
    """Enqueue async refresh for one cached OpenAlex citation graph."""
    payload = {
        "cache_key": str(cache_key or "").strip(),
        "center_work_id": str(center_work_id or "").strip(),
        "n_hops": int(n_hops),
        "max_references": int(max_references),
        "max_citing": int(max_citing),
        "max_nodes": int(max_nodes),
        "algo_version": str(algo_version or "").strip(),
    }
    return _enqueue_job(
        db_url=db_url,
        queue_name=queue_name,
        job_type="openalex_network_refresh",
        payload=payload,
    )


def _claim_next_job(conn, *, queue_name: str, worker_id: str) -> Optional[Dict[str, Any]]:
    """Claim next job.

    Args:
        conn (Any): Open database connection.
        queue_name (str): Queue name used for background processing.
        worker_id (str): Worker identifier handling the job.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    cur = conn.cursor()
    cur.execute(
        """
        WITH candidate AS (
            SELECT id
            FROM workflow.async_jobs
            WHERE queue_name = %s
              AND status IN ('queued', 'retry')
              AND available_at <= NOW()
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE workflow.async_jobs j
        SET status = 'running',
            attempt_count = j.attempt_count + 1,
            worker_id = %s,
            locked_at = NOW(),
            started_at = COALESCE(j.started_at, NOW()),
            updated_at = NOW()
        FROM candidate
        WHERE j.id = candidate.id
        RETURNING
            j.id,
            j.job_id,
            j.job_type,
            j.payload_json,
            j.attempt_count,
            j.max_attempts,
            j.retry_delay_seconds
        """,
        (queue_name, worker_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "job_id": row[1],
        "job_type": row[2],
        "payload_json": row[3] or {},
        "attempt_count": int(row[4] or 0),
        "max_attempts": int(row[5] or 1),
        "retry_delay_seconds": int(row[6] or 10),
    }


def _mark_completed(conn, *, job_id: str, result: Dict[str, Any]) -> None:
    """Mark completed.

    Args:
        conn (Any): Open database connection.
        job_id (str): Asynchronous job identifier.
        result (Dict[str, Any]): Mapping containing result.
    """
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE workflow.async_jobs
        SET status = 'completed',
            result_json = %s::jsonb,
            error_text = NULL,
            finished_at = NOW(),
            locked_at = NULL,
            updated_at = NOW()
        WHERE job_id = %s
        """,
        (json.dumps(result, ensure_ascii=False), job_id),
    )


def _mark_failed(conn, *, job: Dict[str, Any], error_text: str) -> None:
    """Mark failed.

    Args:
        conn (Any): Open database connection.
        job (Dict[str, Any]): Mapping containing job.
        error_text (str): Input value for error text.
    """
    attempts = int(job["attempt_count"])
    max_attempts = int(job["max_attempts"])
    retry_delay = max(1, int(job["retry_delay_seconds"]))
    trimmed_error = (error_text or "")[-8000:]
    cur = conn.cursor()
    if attempts < max_attempts:
        cur.execute(
            """
            UPDATE workflow.async_jobs
            SET status = 'retry',
                error_text = %s,
                available_at = NOW() + make_interval(secs => %s),
                locked_at = NULL,
                updated_at = NOW()
            WHERE job_id = %s
            """,
            (trimmed_error, retry_delay * attempts, job["job_id"]),
        )
    else:
        cur.execute(
            """
            UPDATE workflow.async_jobs
            SET status = 'failed',
                error_text = %s,
                finished_at = NOW(),
                locked_at = NULL,
                updated_at = NOW()
            WHERE job_id = %s
            """,
            (trimmed_error, job["job_id"]),
        )


def _execute_job(job: Dict[str, Any], *, default_meta_db_url: str | None = None) -> Dict[str, Any]:
    """Execute job.

    Args:
        job (Dict[str, Any]): Mapping containing job.
        default_meta_db_url (str | None): Input value for default meta db url.

    Returns:
        Dict[str, Any]: Dictionary containing the computed result payload.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    payload = job.get("payload_json") or {}
    job_type = str(job.get("job_type") or "").strip()

    if job_type == "workflow":
        meta_db_url = payload.get("meta_db_url") or default_meta_db_url
        run_id = workflow_entrypoint(
            papers_dir=str(payload.get("papers_dir") or ""),
            config_path=payload.get("config_path"),
            meta_db_url=meta_db_url,
            agentic=payload.get("agentic"),
            question=payload.get("question"),
            agentic_model=payload.get("agentic_model"),
            agentic_citations=payload.get("agentic_citations"),
            report_question_set=payload.get("report_question_set"),
            workstream_id=payload.get("workstream_id"),
            arm=payload.get("arm"),
            parent_run_id=payload.get("parent_run_id"),
            trigger_source=payload.get("trigger_source"),
        )
        return {"run_id": run_id}

    if job_type == "index":
        config_path_raw = payload.get("config_path")
        settings = load_settings(Path(config_path_raw) if config_path_raw else None)
        paper_paths = [Path(p) for p in (payload.get("paper_paths") or [])]
        index_path = Path(payload.get("index_path") or "vectors-3072.index")
        build_index(
            settings=settings,
            paper_paths=paper_paths,
            index_path=index_path,
            meta_db_url=payload.get("meta_db_url"),
        )
        return {
            "papers_indexed": len(paper_paths),
            "index_path": str(index_path),
        }

    if job_type == "openalex_network_refresh":
        from ragonometrics.services import citation_network as citation_network_service

        try:
            return citation_network_service.refresh_cached_citation_graph(
                payload=payload,
                db_url=default_meta_db_url,
            )
        except Exception:
            citation_network_service.mark_cached_citation_refresh_failure(
                cache_key=str(payload.get("cache_key") or "").strip(),
                db_url=default_meta_db_url,
            )
            raise

    raise RuntimeError(f"Unsupported job_type '{job_type}'.")


def run_worker(
    *,
    db_url: str | None = None,
    queue_name: str = DEFAULT_QUEUE_NAME,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    once: bool = False,
    max_jobs: int = 0,
    worker_id: str | None = None,
) -> int:
    """Run polling worker loop for Postgres-backed jobs.

    Args:
        db_url (str | None): Postgres connection URL.
        queue_name (str): Queue name used for background processing.
        poll_seconds (float): Input value for poll seconds.
        once (bool): Whether to enable once.
        max_jobs (int): Input value for max jobs.
        worker_id (str | None): Worker identifier handling the job.

    Returns:
        int: Computed integer result.
    """

    resolved_db_url = _resolve_db_url(db_url)
    effective_worker_id = worker_id or socket.gethostname()
    effective_poll = max(0.1, float(poll_seconds))
    processed = 0

    while True:
        job: Optional[Dict[str, Any]] = None
        with _connect(resolved_db_url) as conn:
            ensure_async_jobs_table(conn)
            job = _claim_next_job(conn, queue_name=queue_name, worker_id=effective_worker_id)
            conn.commit()

        if not job:
            if once:
                return 0
            if max_jobs > 0 and processed >= max_jobs:
                return 0
            time.sleep(effective_poll)
            continue

        try:
            result = _execute_job(job, default_meta_db_url=resolved_db_url)
            with _connect(resolved_db_url) as conn:
                _mark_completed(conn, job_id=job["job_id"], result=result)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            details = f"{exc}\n{traceback.format_exc()}"
            with _connect(resolved_db_url) as conn:
                _mark_failed(conn, job=job, error_text=details)
                conn.commit()

        processed += 1
        if once:
            return 0
        if max_jobs > 0 and processed >= max_jobs:
            return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build parser.

    Returns:
        argparse.ArgumentParser: Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Postgres-backed async queue worker for Ragonometrics."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    worker = sub.add_parser("worker", help="Run queue worker loop.")
    worker.add_argument("--db-url", type=str, default=None, help="Postgres URL (defaults to DATABASE_URL).")
    worker.add_argument("--queue-name", type=str, default=DEFAULT_QUEUE_NAME)
    worker.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    worker.add_argument("--once", action="store_true", help="Process at most one available job then exit.")
    worker.add_argument("--max-jobs", type=int, default=0, help="Process at most N jobs (0 = no limit).")
    worker.add_argument("--worker-id", type=str, default=None)

    return parser


def main() -> int:
    """Main.

    Returns:
        int: Computed integer result.
    """
    parser = _build_parser()
    args = parser.parse_args()
    if args.cmd == "worker":
        return run_worker(
            db_url=args.db_url,
            queue_name=args.queue_name,
            poll_seconds=args.poll_seconds,
            once=bool(args.once),
            max_jobs=int(args.max_jobs or 0),
            worker_id=args.worker_id,
        )
    parser.error(f"Unsupported command: {args.cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
