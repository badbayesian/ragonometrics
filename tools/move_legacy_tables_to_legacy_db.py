#!/usr/bin/env python
"""Move legacy tables from the active database into a legacy database.

This tool copies table schema + data, verifies row-count parity, and can
optionally drop source tables after successful verification.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse


DEFAULT_LEGACY_TABLES: List[str] = [
    # Legacy workflow tables superseded by workflow.run_records.
    "workflow.artifacts",
    "workflow.report_questions",
    "workflow.workflow_reports",
    "workflow.workflow_runs",
    "workflow.workflow_steps",
    "workflow.workstream_runs",
    "workflow.workstreams",
    # Legacy public-schema mirrors from pre-unified schemas.
    "public.documents",
    "public.index_shards",
    "public.index_versions",
    "public.paper_metadata",
    "public.pipeline_runs",
    "public.request_failures",
    "public.vectors",
    "public.workflow_reports",
]

TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
DBNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]*$")


@dataclass(frozen=True)
class ToolMode:
    """Resolved Postgres tool execution mode."""

    use_docker: bool
    docker_service: str


def _err(message: str) -> int:
    """Print one error line and return exit code."""
    print(f"[error] {message}", file=sys.stderr)
    return 1


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        description=(
            "Copy legacy tables from source DB to legacy DB and optionally drop them from source "
            "after row-count verification."
        )
    )
    parser.add_argument(
        "--source-db-url",
        type=str,
        default=(os.environ.get("DATABASE_URL") or "").strip(),
        help="Source Postgres URL (default: DATABASE_URL).",
    )
    parser.add_argument(
        "--legacy-db-url",
        type=str,
        default=(os.environ.get("LEGACY_DATABASE_URL") or "").strip(),
        help="Target legacy Postgres URL. If omitted, derived from --source-db-url + --legacy-db-name.",
    )
    parser.add_argument(
        "--legacy-db-name",
        type=str,
        default="ragonometrics_legacy",
        help="Legacy database name when --legacy-db-url is omitted (default: ragonometrics_legacy).",
    )
    parser.add_argument(
        "--admin-db-url",
        type=str,
        default=(os.environ.get("ADMIN_DATABASE_URL") or "").strip(),
        help="Admin URL used to create legacy DB if missing (default: source URL with db name 'postgres').",
    )
    parser.add_argument(
        "--table",
        action="append",
        default=[],
        help="schema.table to move; repeatable. Defaults to built-in legacy table list.",
    )
    parser.add_argument(
        "--drop-source",
        action="store_true",
        help="Drop source tables after successful copy + row-count parity checks.",
    )
    parser.add_argument(
        "--create-legacy-db",
        action="store_true",
        help="Create legacy DB when missing (default: false).",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Skip tables not found in source DB.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and report only; no DB mutations.",
    )
    parser.add_argument(
        "--docker-service",
        type=str,
        default="postgres",
        help="Compose service name to run pg tools inside when local pg_dump/psql are unavailable.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/misc/legacy-table-move-report.json"),
        help="Write run report JSON here.",
    )
    return parser.parse_args()


def _quote_literal(value: str) -> str:
    """Return SQL single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _quote_ident(value: str) -> str:
    """Return SQL identifier quoting."""
    return '"' + str(value).replace('"', '""') + '"'


def _db_name_from_url(db_url: str) -> str:
    """Extract DB name from Postgres URL."""
    parsed = urlparse(db_url)
    name = str(parsed.path or "").lstrip("/")
    if not name:
        raise ValueError(f"Could not parse database name from URL: {db_url}")
    return name


def _with_db_name(db_url: str, db_name: str) -> str:
    """Return URL with replaced database name."""
    parsed = urlparse(db_url)
    return urlunparse((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.params, parsed.query, parsed.fragment))


def _normalize_tables(raw_tables: Sequence[str]) -> List[str]:
    """Normalize table list and validate schema.table format."""
    out: List[str] = []
    for item in raw_tables:
        text = str(item or "").strip().lower()
        if not text:
            continue
        if not TABLE_RE.match(text):
            raise ValueError(f"Invalid table identifier: {item!r} (expected schema.table)")
        if text not in out:
            out.append(text)
    return out


def _resolve_tool_mode(docker_service: str) -> ToolMode:
    """Resolve whether pg tools run locally or via Docker Compose."""
    has_local_pg = bool(shutil.which("pg_dump")) and bool(shutil.which("psql"))
    if has_local_pg:
        return ToolMode(use_docker=False, docker_service=docker_service)
    if not shutil.which("docker"):
        raise RuntimeError("Neither local pg_dump/psql nor docker were found in PATH.")
    return ToolMode(use_docker=True, docker_service=docker_service)


def _run_cmd(cmd: List[str], *, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run subprocess command and raise on failure."""
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )


def _psql_cmd(mode: ToolMode, db_url: str, extra: Sequence[str]) -> List[str]:
    """Build psql command for local or docker mode."""
    if mode.use_docker:
        return ["docker", "compose", "exec", "-T", mode.docker_service, "psql", db_url, *extra]
    return ["psql", db_url, *extra]


def _pg_dump_cmd(mode: ToolMode, db_url: str, extra: Sequence[str]) -> List[str]:
    """Build pg_dump command for local or docker mode."""
    if mode.use_docker:
        return ["docker", "compose", "exec", "-T", mode.docker_service, "pg_dump", f"--dbname={db_url}", *extra]
    return ["pg_dump", f"--dbname={db_url}", *extra]


def psql_query_scalar(mode: ToolMode, db_url: str, query: str) -> str:
    """Execute scalar query and return first output line (or empty string)."""
    cmd = _psql_cmd(mode, db_url, ["-v", "ON_ERROR_STOP=1", "-At", "-c", query])
    out = _run_cmd(cmd)
    return str(out.stdout or "").strip().splitlines()[0] if str(out.stdout or "").strip() else ""


def psql_exec(mode: ToolMode, db_url: str, sql_text: str) -> None:
    """Execute SQL text via stdin."""
    cmd = _psql_cmd(mode, db_url, ["-v", "ON_ERROR_STOP=1", "-f", "-"])
    _run_cmd(cmd, input_text=sql_text)


def table_exists(mode: ToolMode, db_url: str, table_name: str) -> bool:
    """Check whether one table exists."""
    return psql_query_scalar(mode, db_url, f"SELECT to_regclass({_quote_literal(table_name)}) IS NOT NULL;") == "t"


def table_row_count(mode: ToolMode, db_url: str, table_name: str) -> Optional[int]:
    """Return row count for one table, or None if table is missing."""
    if not table_exists(mode, db_url, table_name):
        return None
    query = f"SELECT COUNT(*)::bigint FROM {table_name};"
    val = psql_query_scalar(mode, db_url, query)
    return int(val or 0)


def ensure_database_exists(mode: ToolMode, *, admin_db_url: str, db_name: str, dry_run: bool) -> bool:
    """Ensure target database exists; return whether it was created."""
    exists = psql_query_scalar(
        mode,
        admin_db_url,
        f"SELECT 1 FROM pg_database WHERE datname = {_quote_literal(db_name)} LIMIT 1;",
    )
    if exists == "1":
        return False
    if dry_run:
        print(f"[dry-run] would create database {db_name}")
        return False
    sql_text = f"CREATE DATABASE {_quote_ident(db_name)};"
    psql_exec(mode, admin_db_url, sql_text)
    return True


def ensure_schemas(mode: ToolMode, *, db_url: str, tables: Sequence[str], dry_run: bool) -> None:
    """Create needed schemas in target database."""
    schemas = sorted({name.split(".", 1)[0] for name in tables})
    if not schemas:
        return
    sql_lines = [f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(schema)};" for schema in schemas]
    sql_text = "\n".join(sql_lines) + "\n"
    if dry_run:
        print("[dry-run] would ensure schemas:", ", ".join(schemas))
        return
    psql_exec(mode, db_url, sql_text)


def ensure_vector_extension_if_needed(mode: ToolMode, *, db_url: str, tables: Sequence[str], dry_run: bool) -> None:
    """Ensure vector extension exists if vectors table is being moved."""
    if "public.vectors" not in tables and "indexing.vectors" not in tables:
        return
    if dry_run:
        print("[dry-run] would ensure extension vector on target DB")
        return
    psql_exec(mode, db_url, "CREATE EXTENSION IF NOT EXISTS vector;\n")


def dump_tables_sql(
    mode: ToolMode,
    *,
    source_db_url: str,
    tables: Sequence[str],
    schema_only: bool,
) -> str:
    """Dump selected tables to a temp SQL file and return path."""
    kind_args: List[str] = ["--schema-only"] if schema_only else ["--data-only"]
    common_args = [
        "--no-owner",
        "--no-privileges",
    ]
    if schema_only:
        common_args.extend(["--clean", "--if-exists"])
    extra: List[str] = [*common_args, *kind_args]
    for table_name in tables:
        extra.extend(["--table", table_name])
    cmd = _pg_dump_cmd(mode, source_db_url, extra)
    with NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as fh:
        dump_path = fh.name
    try:
        with open(dump_path, "w", encoding="utf-8") as out_fh:
            subprocess.run(cmd, stdout=out_fh, stderr=subprocess.PIPE, text=True, check=True)
    except Exception:
        try:
            os.unlink(dump_path)
        except Exception:
            pass
        raise
    return dump_path


def apply_sql_file(mode: ToolMode, *, db_url: str, file_path: str) -> None:
    """Apply SQL file to target database."""
    cmd = _psql_cmd(mode, db_url, ["-v", "ON_ERROR_STOP=1", "-f", "-"])
    payload = Path(file_path).read_text(encoding="utf-8")
    _run_cmd(cmd, input_text=payload)


def drop_source_tables(mode: ToolMode, *, source_db_url: str, tables: Sequence[str], dry_run: bool) -> None:
    """Drop source tables with CASCADE."""
    sql_lines = [f"DROP TABLE IF EXISTS {table_name} CASCADE;" for table_name in tables]
    sql_text = "\n".join(sql_lines) + "\n"
    if dry_run:
        print("[dry-run] would drop source tables:", ", ".join(tables))
        return
    psql_exec(mode, source_db_url, sql_text)


def main() -> int:
    """Entry point."""
    args = parse_args()
    started_at = datetime.now(timezone.utc)
    raw_source = str(args.source_db_url or "").strip()
    if not raw_source:
        return _err("Missing --source-db-url (or DATABASE_URL).")
    if not DBNAME_RE.match(str(args.legacy_db_name or "").strip()):
        return _err("Invalid --legacy-db-name.")

    try:
        source_db_name = _db_name_from_url(raw_source)
    except Exception as exc:
        return _err(str(exc))
    legacy_db_url = str(args.legacy_db_url or "").strip() or _with_db_name(raw_source, str(args.legacy_db_name).strip())
    try:
        legacy_db_name = _db_name_from_url(legacy_db_url)
    except Exception as exc:
        return _err(str(exc))
    admin_db_url = str(args.admin_db_url or "").strip() or _with_db_name(raw_source, "postgres")

    try:
        tables = _normalize_tables(args.table or DEFAULT_LEGACY_TABLES)
    except ValueError as exc:
        return _err(str(exc))
    if not tables:
        return _err("No tables selected.")

    report: Dict[str, object] = {
        "ok": False,
        "started_at": started_at.isoformat(),
        "finished_at": "",
        "source_db_name": source_db_name,
        "legacy_db_name": legacy_db_name,
        "source_db_url": raw_source,
        "legacy_db_url": legacy_db_url,
        "tables_requested": tables,
        "tables_moved": [],
        "tables_skipped_missing": [],
        "source_counts_before": {},
        "target_counts_after": {},
        "dropped_source_tables": [],
        "created_legacy_database": False,
        "dry_run": bool(args.dry_run),
        "drop_source": bool(args.drop_source),
        "errors": [],
    }

    schema_dump_path = ""
    data_dump_path = ""
    try:
        mode = _resolve_tool_mode(str(args.docker_service or "postgres").strip() or "postgres")
        report["tool_mode"] = "docker-compose-exec" if mode.use_docker else "local-pg-tools"
        report["docker_service"] = mode.docker_service

        existing_tables: List[str] = []
        missing_tables: List[str] = []
        source_counts_before: Dict[str, int] = {}
        for table_name in tables:
            count = table_row_count(mode, raw_source, table_name)
            if count is None:
                missing_tables.append(table_name)
            else:
                existing_tables.append(table_name)
                source_counts_before[table_name] = int(count)

        report["source_counts_before"] = source_counts_before
        report["tables_skipped_missing"] = missing_tables

        if missing_tables and not bool(args.allow_missing):
            raise RuntimeError(
                "Missing source tables: "
                + ", ".join(missing_tables)
                + " (use --allow-missing to skip)."
            )
        if not existing_tables:
            raise RuntimeError("None of the selected tables exist in source DB.")

        if bool(args.create_legacy_db):
            created = ensure_database_exists(
                mode,
                admin_db_url=admin_db_url,
                db_name=legacy_db_name,
                dry_run=bool(args.dry_run),
            )
            report["created_legacy_database"] = bool(created)
        else:
            if psql_query_scalar(
                mode,
                admin_db_url,
                f"SELECT 1 FROM pg_database WHERE datname = {_quote_literal(legacy_db_name)} LIMIT 1;",
            ) != "1":
                raise RuntimeError(
                    f"Legacy database '{legacy_db_name}' does not exist. "
                    "Use --create-legacy-db or pre-create it."
                )

        ensure_schemas(mode, db_url=legacy_db_url, tables=existing_tables, dry_run=bool(args.dry_run))
        ensure_vector_extension_if_needed(mode, db_url=legacy_db_url, tables=existing_tables, dry_run=bool(args.dry_run))

        if bool(args.dry_run):
            report["tables_moved"] = existing_tables
            report["ok"] = True
            print("[dry-run] plan looks valid.")
        else:
            print(f"[step] dumping schema for {len(existing_tables)} table(s)")
            schema_dump_path = dump_tables_sql(
                mode,
                source_db_url=raw_source,
                tables=existing_tables,
                schema_only=True,
            )
            print("[step] applying schema to legacy DB")
            apply_sql_file(mode, db_url=legacy_db_url, file_path=schema_dump_path)

            print("[step] dumping data")
            data_dump_path = dump_tables_sql(
                mode,
                source_db_url=raw_source,
                tables=existing_tables,
                schema_only=False,
            )
            print("[step] applying data to legacy DB")
            apply_sql_file(mode, db_url=legacy_db_url, file_path=data_dump_path)

            target_counts_after: Dict[str, int] = {}
            mismatches: List[str] = []
            for table_name in existing_tables:
                src_count = int(source_counts_before.get(table_name, 0))
                tgt_count = table_row_count(mode, legacy_db_url, table_name)
                target_counts_after[table_name] = int(tgt_count or 0)
                if int(tgt_count or 0) != src_count:
                    mismatches.append(f"{table_name} source={src_count} target={int(tgt_count or 0)}")

            report["target_counts_after"] = target_counts_after
            if mismatches:
                raise RuntimeError("Row-count mismatches after copy: " + "; ".join(mismatches))

            report["tables_moved"] = existing_tables
            if bool(args.drop_source):
                print("[step] dropping source tables after parity checks")
                drop_source_tables(mode, source_db_url=raw_source, tables=existing_tables, dry_run=False)
                report["dropped_source_tables"] = existing_tables

            report["ok"] = True

    except Exception as exc:
        report["errors"] = [str(exc)]
        print(f"[error] {exc}", file=sys.stderr)
    finally:
        for dump_path in [schema_dump_path, data_dump_path]:
            if dump_path and os.path.exists(dump_path):
                try:
                    os.unlink(dump_path)
                except Exception:
                    pass
        finished_at = datetime.now(timezone.utc)
        report["finished_at"] = finished_at.isoformat()

        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[report] wrote {out_path}")

    return 0 if bool(report.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
