#!/usr/bin/env python
"""Wait until Postgres async queue is drained."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DB_MODULE = None
DB_MODULE_NAME = ""
try:
    import psycopg as _db_module  # type: ignore

    DB_MODULE = _db_module
    DB_MODULE_NAME = "psycopg"
except ModuleNotFoundError:
    try:
        import psycopg2 as _db_module  # type: ignore

        DB_MODULE = _db_module
        DB_MODULE_NAME = "psycopg2"
    except ModuleNotFoundError:
        DB_MODULE = None
        DB_MODULE_NAME = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll workflow.async_jobs until queued/retry/running jobs reach zero."
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=os.environ.get("DATABASE_URL"),
        help="Postgres URL (defaults to DATABASE_URL).",
    )
    parser.add_argument(
        "--queue-name",
        type=str,
        default="default",
        help="Queue name filter (default: default).",
    )
    parser.add_argument(
        "--job-type",
        type=str,
        default="workflow",
        choices=("workflow", "index"),
        help="Job type filter (default: workflow).",
    )
    parser.add_argument(
        "--workstream-id",
        type=str,
        default=None,
        help="Exact workstream_id filter (payload_json->>'workstream_id').",
    )
    parser.add_argument(
        "--workstream-prefix",
        type=str,
        default=None,
        help="Prefix filter for workstream_id (payload_json->>'workstream_id' LIKE '<prefix>%%').",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Stop waiting after N seconds (0 = no timeout).",
    )
    parser.add_argument(
        "--show-failures-limit",
        type=int,
        default=10,
        help="Show up to N failed jobs when queue drains (default: 10).",
    )
    parser.add_argument(
        "--fail-on-failed",
        action="store_true",
        help="Return non-zero if drained queue contains failed jobs.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress periodic status lines.",
    )
    return parser.parse_args()


def _build_where(args: argparse.Namespace) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if args.queue_name:
        clauses.append("queue_name = %s")
        params.append(args.queue_name)
    if args.job_type:
        clauses.append("job_type = %s")
        params.append(args.job_type)
    if args.workstream_id:
        clauses.append("COALESCE(payload_json->>'workstream_id', '') = %s")
        params.append(args.workstream_id)
    if args.workstream_prefix:
        clauses.append("COALESCE(payload_json->>'workstream_id', '') LIKE %s")
        params.append(f"{args.workstream_prefix}%")
    if not clauses:
        return "TRUE", []
    return " AND ".join(clauses), params


def fetch_counts(conn: Any, args: argparse.Namespace) -> Dict[str, int]:
    where_sql, params = _build_where(args)
    query = f"""
        SELECT
            COUNT(*) FILTER (WHERE status = 'queued')  AS queued,
            COUNT(*) FILTER (WHERE status = 'retry')   AS retry,
            COUNT(*) FILTER (WHERE status = 'running') AS running,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed
        FROM workflow.async_jobs
        WHERE {where_sql}
    """
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    queued = int(row[0] or 0)
    retry = int(row[1] or 0)
    running = int(row[2] or 0)
    completed = int(row[3] or 0)
    failed = int(row[4] or 0)
    return {
        "queued": queued,
        "retry": retry,
        "running": running,
        "completed": completed,
        "failed": failed,
        "remaining": queued + retry + running,
    }


def fetch_failures(
    conn: Any, args: argparse.Namespace, limit: int
) -> List[Tuple[str, str, str, str, str]]:
    if limit <= 0:
        return []
    where_sql, params = _build_where(args)
    query = f"""
        SELECT
            job_id,
            COALESCE(payload_json->>'papers_dir', '') AS papers_dir,
            COALESCE(payload_json->>'workstream_id', '') AS workstream_id,
            COALESCE(finished_at::text, '') AS finished_at,
            LEFT(COALESCE(error_text, ''), 240) AS error_preview
        FROM workflow.async_jobs
        WHERE status = 'failed' AND ({where_sql})
        ORDER BY finished_at DESC NULLS LAST, updated_at DESC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(query, [*params, int(limit)])
        return list(cur.fetchall())


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> int:
    args = parse_args()
    if DB_MODULE is None:
        print(
            "[error] Missing database driver. Install one of: `pip install psycopg[binary]` or `pip install psycopg2-binary`.",
            file=sys.stderr,
        )
        return 1
    db_url = (args.db_url or "").strip()
    if not db_url:
        print("[error] --db-url is required (or set DATABASE_URL).", file=sys.stderr)
        return 1
    poll_seconds = max(0.2, float(args.poll_seconds or 5.0))
    timeout_seconds = max(0, int(args.timeout_seconds or 0))
    started = time.monotonic()

    try:
        with DB_MODULE.connect(db_url) as conn:
            while True:
                counts = fetch_counts(conn, args)
                if not args.quiet:
                    print(
                        f"[{_stamp()}] driver={DB_MODULE_NAME} remaining={counts['remaining']} "
                        f"(queued={counts['queued']} retry={counts['retry']} running={counts['running']}) "
                        f"completed={counts['completed']} failed={counts['failed']}"
                    )

                if counts["remaining"] == 0:
                    print("[done] queue drained.")
                    if counts["failed"] > 0 and args.show_failures_limit > 0:
                        failures = fetch_failures(conn, args, args.show_failures_limit)
                        if failures:
                            print(f"[failed] showing up to {args.show_failures_limit} failed job(s):")
                            for job_id, papers_dir, workstream_id, finished_at, err in failures:
                                print(
                                    f"  job_id={job_id} workstream_id={workstream_id} "
                                    f"paper={papers_dir} finished_at={finished_at}"
                                )
                                if err:
                                    print(f"    error: {err}")
                    if args.fail_on_failed and counts["failed"] > 0:
                        return 2
                    return 0

                elapsed = time.monotonic() - started
                if timeout_seconds > 0 and elapsed >= timeout_seconds:
                    print(f"[timeout] queue not drained after {timeout_seconds}s.", file=sys.stderr)
                    return 1
                time.sleep(poll_seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] failed while polling queue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
