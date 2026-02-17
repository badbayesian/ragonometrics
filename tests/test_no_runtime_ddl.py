"""Guardrail tests for migration-owned schema (no runtime DDL in hot paths)."""

from pathlib import Path


HOT_PATH_MODULES = [
    "ragonometrics/pipeline/query_cache.py",
    "ragonometrics/pipeline/token_usage.py",
    "ragonometrics/pipeline/state.py",
    "ragonometrics/pipeline/run_records.py",
    "ragonometrics/integrations/openalex.py",
    "ragonometrics/integrations/citec.py",
    "ragonometrics/integrations/rq_queue.py",
    "ragonometrics/indexing/metadata.py",
    "ragonometrics/integrations/openalex_store.py",
]

DDL_PATTERNS = [
    "CREATE TABLE IF NOT EXISTS",
    "CREATE INDEX IF NOT EXISTS",
    "ALTER TABLE",
    "CREATE SCHEMA IF NOT EXISTS",
]


def test_hot_path_modules_do_not_own_schema_ddl() -> None:
    for module_path in HOT_PATH_MODULES:
        text = Path(module_path).read_text(encoding="utf-8")
        for pattern in DDL_PATTERNS:
            assert pattern not in text, f"Found runtime DDL '{pattern}' in {module_path}"

