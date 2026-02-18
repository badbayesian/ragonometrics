"""Tests for DB connection helpers and Alembic revision normalization."""

import importlib.util
from pathlib import Path


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


db_connection = _load_mod("ragonometrics/db/connection.py", "ragonometrics.db.connection")


def _set_revision(value: str) -> None:
    conn = db_connection.connect("dummy", require_migrated=False)
    cur = conn.cursor()
    cur.execute("UPDATE alembic_version SET version_num = %s", (value,))
    conn.commit()
    db_connection._SCHEMA_READY_BY_DSN.clear()


def test_normalize_alembic_revision_aliases():
    assert db_connection.normalize_alembic_revision("0002_migrate_workflow_legacy") == "0002"
    assert db_connection.normalize_alembic_revision("0003_async_jobs") == "0003"
    assert db_connection.normalize_alembic_revision("0004_run_record_idempotency_usage") == "0004"
    assert db_connection.normalize_alembic_revision("0005_drop_legacy_openalex_cache") == "0005"
    assert db_connection.normalize_alembic_revision("0006_streamlit_auth_tables") == "0006"
    assert db_connection.normalize_alembic_revision("0004_extra_text") == "0004"
    assert db_connection.normalize_alembic_revision("0006_extra_text") == "0006"
    assert db_connection.normalize_alembic_revision("0004") == "0004"
    assert db_connection.normalize_alembic_revision("0006") == "0006"
    assert db_connection.normalize_alembic_revision(None) == ""


def test_normalize_alembic_version_marker_updates_db():
    _set_revision("0002_migrate_workflow_legacy")
    conn = db_connection.connect("dummy", require_migrated=False)
    try:
        raw, normalized, changed = db_connection.normalize_alembic_version_marker(conn)
        assert raw == "0002_migrate_workflow_legacy"
        assert normalized == "0002"
        assert changed is True
        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        row = cur.fetchone()
        assert row[0] == "0002"
    finally:
        _set_revision("0006")


def test_ensure_schema_ready_accepts_legacy_marker_alias():
    _set_revision("0005_drop_legacy_openalex_cache")
    conn = db_connection.connect("dummy", require_migrated=False)
    try:
        db_connection.ensure_schema_ready(conn, expected_revision="0005")
    finally:
        _set_revision("0006")
