"""Fixed-window request rate limiting persisted in Postgres."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from ragonometrics.db.connection import pooled_connection


def _window_start(now: datetime, window_seconds: int) -> datetime:
    """Internal helper for window start."""
    epoch = int(now.timestamp())
    floored = epoch - (epoch % max(1, int(window_seconds)))
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def check_rate_limit(
    db_url: str,
    *,
    subject_key: str,
    route: str,
    limit_count: int,
    window_seconds: int,
) -> Dict[str, int | bool | str]:
    """Consume one token from a fixed window and return allowance status."""
    safe_subject = str(subject_key or "").strip()[:256] or "anonymous"
    safe_route = str(route or "").strip()[:128] or "unknown"
    allowed_limit = max(1, int(limit_count))
    window = max(1, int(window_seconds))
    now = datetime.now(timezone.utc)
    start = _window_start(now, window)
    end = start + timedelta(seconds=window)
    if not db_url:
        return {"allowed": True, "count": 0, "limit": allowed_limit, "window_seconds": window}
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO auth.request_rate_limits
                    (subject_key, route, window_start, window_seconds, request_count, updated_at)
                VALUES
                    (%s, %s, %s, %s, 1, NOW())
                ON CONFLICT (subject_key, route, window_start)
                DO UPDATE SET
                    request_count = auth.request_rate_limits.request_count + 1,
                    updated_at = NOW()
                RETURNING request_count
                """,
                (safe_subject, safe_route, start, window),
            )
            row = cur.fetchone()
            conn.commit()
            count = int((row or [0])[0] or 0)
            return {
                "allowed": bool(count <= allowed_limit),
                "count": count,
                "limit": allowed_limit,
                "window_seconds": window,
                "window_ends_at": end.isoformat(),
            }
    except Exception:
        return {"allowed": True, "count": 0, "limit": allowed_limit, "window_seconds": window}

