"""Usage telemetry service wrappers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ragonometrics.pipeline.token_usage import get_recent_usage, get_usage_by_model, get_usage_summary


def usage_summary(*, session_id: Optional[str] = None, request_id: Optional[str] = None, since: Optional[str] = None) -> Dict[str, Any]:
    """Return one usage summary payload."""
    summary = get_usage_summary(session_id=session_id, request_id=request_id, since=since)
    return {
        "calls": int(summary.calls),
        "input_tokens": int(summary.input_tokens),
        "output_tokens": int(summary.output_tokens),
        "total_tokens": int(summary.total_tokens),
    }


def usage_by_model(*, session_id: Optional[str] = None, request_id: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return usage grouped by model."""
    return get_usage_by_model(session_id=session_id, request_id=request_id, since=since)


def recent_usage(*, limit: int = 200, session_id: Optional[str] = None, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return recent usage rows."""
    return get_recent_usage(limit=int(limit), session_id=session_id, request_id=request_id)

