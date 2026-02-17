"""Add/refresh async jobs queue schema."""

from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "0003"
down_revision = "0002"
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
    _execute_sql_file("003_add_async_jobs_queue.sql")


def downgrade() -> None:
    # Explicitly non-destructive: downgrade is intentionally not implemented.
    pass
