"""Add auth email/reset support, paper notes, and normalized query cache keys."""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "0009"
down_revision = "0008"
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
    _execute_sql_file("009_web_ux_auth_upgrades.sql")


def downgrade() -> None:
    # Explicitly non-destructive: downgrade intentionally left empty.
    pass
