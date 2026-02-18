"""Unified workflow record store helpers.

This module defines the consolidated `workflow.run_records` table that replaces
separate workflow run/step/report/question/artifact tables.
"""

from __future__ import annotations

from ragonometrics.db.connection import ensure_schema_ready


def ensure_run_records_table(conn) -> None:
    """Validate that workflow ledger schema has already been migrated.

    Args:
        conn (Any): Open database connection.
    """
    ensure_schema_ready(conn)
