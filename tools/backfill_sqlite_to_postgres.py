"""Backfill legacy sqlite workflow/cache/usage data into Postgres."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from ragonometrics.db.connection import connect
from ragonometrics.pipeline.report_store import store_workflow_reports_from_dir


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def _safe_json(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "{}"
        try:
            json.loads(text)
            return text
        except Exception:
            return json.dumps({"value": text}, ensure_ascii=False)
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def backfill_query_cache(sqlite_path: Path, *, db_url: str) -> int:
    if not sqlite_path.exists():
        return 0
    src = sqlite3.connect(str(sqlite_path))
    try:
        if not _sqlite_table_exists(src, "query_cache"):
            return 0
        src_cur = src.cursor()
        src_cur.execute(
            """
            SELECT cache_key, query, paper_path, model, context_hash, answer, created_at
            FROM query_cache
            """
        )
        rows = src_cur.fetchall()
    finally:
        src.close()
    if not rows:
        return 0

    conn = connect(db_url, require_migrated=True)
    try:
        cur = conn.cursor()
        for cache_key, query, paper_path, model, context_hash, answer, created_at in rows:
            cur.execute(
                """
                INSERT INTO retrieval.query_cache
                (cache_key, query, paper_path, model, context_hash, answer, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()))
                ON CONFLICT (cache_key) DO UPDATE SET
                    query = EXCLUDED.query,
                    paper_path = EXCLUDED.paper_path,
                    model = EXCLUDED.model,
                    context_hash = EXCLUDED.context_hash,
                    answer = EXCLUDED.answer,
                    created_at = EXCLUDED.created_at
                """,
                (cache_key, query, paper_path, model, context_hash, answer, created_at),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def backfill_token_usage(sqlite_path: Path, *, db_url: str) -> int:
    if not sqlite_path.exists():
        return 0
    src = sqlite3.connect(str(sqlite_path))
    try:
        if not _sqlite_table_exists(src, "token_usage"):
            return 0
        src_cur = src.cursor()
        src_cur.execute(
            """
            SELECT
                created_at, model, operation, step, question_id,
                input_tokens, output_tokens, total_tokens,
                session_id, request_id, provider_request_id, latency_ms, cache_hit,
                cost_usd_input, cost_usd_output, cost_usd_total,
                run_id, meta
            FROM token_usage
            """
        )
        rows = src_cur.fetchall()
    finally:
        src.close()
    if not rows:
        return 0

    conn = connect(db_url, require_migrated=True)
    try:
        cur = conn.cursor()
        for row in rows:
            cur.execute(
                """
                INSERT INTO observability.token_usage
                (
                    created_at, model, operation, step, question_id,
                    input_tokens, output_tokens, total_tokens,
                    session_id, request_id, provider_request_id,
                    latency_ms, cache_hit,
                    cost_usd_input, cost_usd_output, cost_usd_total,
                    run_id, meta
                )
                VALUES
                (
                    COALESCE(%s, NOW()), %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s::jsonb
                )
                """,
                (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                    row[15],
                    row[16],
                    _safe_json(row[17]),
                ),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def backfill_workflow_state(sqlite_path: Path, *, db_url: str) -> dict[str, int]:
    stats = {"runs": 0, "steps": 0}
    if not sqlite_path.exists():
        return stats
    src = sqlite3.connect(str(sqlite_path))
    try:
        conn = connect(db_url, require_migrated=True)
        try:
            cur = conn.cursor()
            if _sqlite_table_exists(src, "workflow_runs"):
                src_cur = src.cursor()
                src_cur.execute(
                    """
                    SELECT run_id, status, papers_dir, config_hash, started_at, finished_at, metadata
                    FROM workflow_runs
                    """
                )
                for run_id, status, papers_dir, config_hash, started_at, finished_at, metadata in src_cur.fetchall():
                    cur.execute(
                        """
                        INSERT INTO workflow.run_records
                        (
                            run_id, record_kind, step, record_key, status, papers_dir, config_hash,
                            started_at, finished_at, created_at, updated_at, payload_json, metadata_json
                        )
                        VALUES
                        (
                            %s, 'run', '', 'main', %s, %s, %s,
                            %s, %s, NOW(), NOW(), %s::jsonb, %s::jsonb
                        )
                        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                            status = COALESCE(EXCLUDED.status, workflow.run_records.status),
                            finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
                            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                            updated_at = NOW()
                        """,
                        (
                            run_id,
                            status,
                            papers_dir,
                            config_hash,
                            started_at,
                            finished_at,
                            json.dumps({"source": "sqlite_workflow_runs"}, ensure_ascii=False),
                            _safe_json(metadata),
                        ),
                    )
                    stats["runs"] += 1
            if _sqlite_table_exists(src, "workflow_steps"):
                src_cur = src.cursor()
                src_cur.execute(
                    """
                    SELECT run_id, step, status, started_at, finished_at, output, metadata
                    FROM workflow_steps
                    """
                )
                for run_id, step, status, started_at, finished_at, output, metadata in src_cur.fetchall():
                    cur.execute(
                        """
                        INSERT INTO workflow.run_records
                        (
                            run_id, record_kind, step, record_key, status, started_at, finished_at,
                            created_at, updated_at, output_json, metadata_json
                        )
                        VALUES
                        (
                            %s, 'step', COALESCE(%s, ''), 'main', %s, %s, %s,
                            NOW(), NOW(), %s::jsonb, %s::jsonb
                        )
                        ON CONFLICT (run_id, record_kind, step, record_key) DO UPDATE SET
                            status = EXCLUDED.status,
                            finished_at = COALESCE(EXCLUDED.finished_at, workflow.run_records.finished_at),
                            output_json = CASE
                                WHEN EXCLUDED.output_json = '{}'::jsonb THEN workflow.run_records.output_json
                                ELSE EXCLUDED.output_json
                            END,
                            metadata_json = workflow.run_records.metadata_json || EXCLUDED.metadata_json,
                            updated_at = NOW()
                        """,
                        (
                            run_id,
                            step,
                            status,
                            started_at,
                            finished_at,
                            _safe_json(output),
                            _safe_json(metadata),
                        ),
                    )
                    stats["steps"] += 1
            conn.commit()
        finally:
            conn.close()
    finally:
        src.close()
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill sqlite + report artifacts into Postgres.")
    parser.add_argument("--db-url", type=str, required=False, default=None)
    parser.add_argument("--workflow-state-sqlite", type=str, default="sqlite/ragonometrics_workflow_state.sqlite")
    parser.add_argument("--query-cache-sqlite", type=str, default="sqlite/ragonometrics_query_cache.sqlite")
    parser.add_argument("--token-usage-sqlite", type=str, default="sqlite/ragonometrics_token_usage.sqlite")
    parser.add_argument("--reports-dir", type=str, default="reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_url = args.db_url or __import__("os").environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("No DB URL provided. Use --db-url or DATABASE_URL.")

    workflow_stats = backfill_workflow_state(Path(args.workflow_state_sqlite), db_url=db_url)
    cache_count = backfill_query_cache(Path(args.query_cache_sqlite), db_url=db_url)
    usage_count = backfill_token_usage(Path(args.token_usage_sqlite), db_url=db_url)
    report_stats = store_workflow_reports_from_dir(
        reports_dir=Path(args.reports_dir),
        db_url=db_url,
        recursive=True,
        limit=0,
    )
    print(
        json.dumps(
            {
                "workflow_runs_backfilled": workflow_stats["runs"],
                "workflow_steps_backfilled": workflow_stats["steps"],
                "query_cache_backfilled": cache_count,
                "token_usage_backfilled": usage_count,
                "report_files_scanned": report_stats.get("total", 0),
                "report_files_stored": report_stats.get("stored", 0),
                "report_files_skipped": report_stats.get("skipped", 0),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

