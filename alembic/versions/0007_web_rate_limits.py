"""Add Postgres request rate limit table for web auth/chat endpoints."""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _execute_sql_file(filename: str) -> None:
    sql_path = Path(__file__).resolve().parents[2] / "deploy" / "sql" / filename
    sql_text = sql_path.read_text(encoding="utf-8")
    bind = op.get_bind()
    raw_conn = bind.connection
    with raw_conn.cursor() as cur:
        cur.execute(sql_text)


def upgrade() -> None:
    _execute_sql_file("007_web_rate_limits.sql")


def downgrade() -> None:
    # Explicitly non-destructive: downgrade intentionally left empty.
    pass

