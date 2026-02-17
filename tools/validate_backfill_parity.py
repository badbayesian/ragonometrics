"""Validate sqlite-to-Postgres backfill parity with row-count checks."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from ragonometrics.db.connection import connect


def _sqlite_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if cur.fetchone() is None:
            return 0
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _pg_count(db_url: str, sql: str) -> int:
    conn = connect(db_url, require_migrated=True)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate sqlite backfill parity into Postgres.")
    parser.add_argument("--db-url", type=str, required=False, default=None)
    parser.add_argument("--workflow-state-sqlite", type=str, default="sqlite/ragonometrics_workflow_state.sqlite")
    parser.add_argument("--query-cache-sqlite", type=str, default="sqlite/ragonometrics_query_cache.sqlite")
    parser.add_argument("--token-usage-sqlite", type=str, default="sqlite/ragonometrics_token_usage.sqlite")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_url = args.db_url or __import__("os").environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("No DB URL provided. Use --db-url or DATABASE_URL.")

    sqlite_runs = _sqlite_count(Path(args.workflow_state_sqlite), "workflow_runs")
    sqlite_steps = _sqlite_count(Path(args.workflow_state_sqlite), "workflow_steps")
    sqlite_cache = _sqlite_count(Path(args.query_cache_sqlite), "query_cache")
    sqlite_usage = _sqlite_count(Path(args.token_usage_sqlite), "token_usage")

    pg_runs = _pg_count(
        db_url,
        """
        SELECT COUNT(*)
        FROM workflow.run_records
        WHERE record_kind = 'run' AND step = '' AND record_key = 'main'
        """,
    )
    pg_steps = _pg_count(
        db_url,
        "SELECT COUNT(*) FROM workflow.run_records WHERE record_kind = 'step'",
    )
    pg_cache = _pg_count(db_url, "SELECT COUNT(*) FROM retrieval.query_cache")
    pg_usage = _pg_count(db_url, "SELECT COUNT(*) FROM observability.token_usage")

    out = {
        "sqlite": {
            "workflow_runs": sqlite_runs,
            "workflow_steps": sqlite_steps,
            "query_cache": sqlite_cache,
            "token_usage": sqlite_usage,
        },
        "postgres": {
            "run_records_runs": pg_runs,
            "run_records_steps": pg_steps,
            "query_cache": pg_cache,
            "token_usage": pg_usage,
        },
        "parity": {
            "workflow_runs_ok": pg_runs >= sqlite_runs,
            "workflow_steps_ok": pg_steps >= sqlite_steps,
            "query_cache_ok": pg_cache >= sqlite_cache,
            "token_usage_ok": pg_usage >= sqlite_usage,
        },
    }
    print(json.dumps(out, indent=2))
    return 0 if all(out["parity"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

