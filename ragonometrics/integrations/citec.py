"""CitEc (RePEc) API integration and caching for citation metadata."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
import xml.etree.ElementTree as ET

import requests
from ragonometrics.db.connection import connect

DEFAULT_CACHE_PATH = Path("postgres_citec_cache")
DEFAULT_BASE_URL = os.environ.get("CITEC_API_BASE", "http://citec.repec.org/api").rstrip("/")


def _database_url() -> str:
    """Database url.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for CitEc cache persistence.")
    return db_url


def _connect(_db_path: Path):
    """Connect.

    Args:
        _db_path (Path): Path to the local SQLite state database.

    Returns:
        Any: Return value produced by the operation.
    """
    return connect(_database_url(), require_migrated=True)


def _cache_ttl_seconds() -> int:
    """Cache ttl seconds.

    Returns:
        int: Computed integer result.
    """
    try:
        days = int(os.environ.get("CITEC_CACHE_TTL_DAYS", "30"))
    except Exception:
        days = 30
    return max(days, 1) * 24 * 60 * 60


def make_cache_key(repec_handle: str) -> str:
    """Make cache key.

    Args:
        repec_handle (str): RePEc handle value.

    Returns:
        str: Computed string result.
    """
    return hashlib.sha256(repec_handle.encode("utf-8")).hexdigest()


def get_cached_metadata(db_path: Path, cache_key: str) -> Optional[Dict[str, Any]]:
    """Get cached metadata.

    Args:
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT fetched_at, response FROM enrichment.citec_cache WHERE cache_key = %s",
            (cache_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at, response = row
        fetched_epoch = int(fetched_at.timestamp()) if hasattr(fetched_at, "timestamp") else int(time.time())
        if time.time() - fetched_epoch > _cache_ttl_seconds():
            return None
        if isinstance(response, dict):
            return response
        try:
            return json.loads(str(response))
        except Exception:
            return None
    finally:
        conn.close()


def set_cached_metadata(db_path: Path, *, cache_key: str, repec_handle: str, response: Dict[str, Any]) -> None:
    """Set cached metadata.

    Args:
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.
        repec_handle (str): RePEc handle value.
        response (Dict[str, Any]): Response payload returned by the upstream call.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO enrichment.citec_cache
            (cache_key, repec_handle, response, fetched_at)
            VALUES (%s, %s, %s::jsonb, NOW())
            ON CONFLICT (cache_key) DO UPDATE SET
                repec_handle = EXCLUDED.repec_handle,
                response = EXCLUDED.response,
                fetched_at = EXCLUDED.fetched_at
            """,
            (
                cache_key,
                repec_handle,
                json.dumps(response, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _request_text(url: str, timeout: int = 10) -> Optional[str]:
    """Request text.

    Args:
        url (str): Input value for url.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[str]: Computed result, or `None` when unavailable.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    max_retries = int(os.environ.get("CITEC_MAX_RETRIES", "2"))
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Ragonometrics/0.1"})
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                raise requests.RequestException("rate_limited")
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            if attempt >= max_retries:
                return None
            try:
                time.sleep(0.5 * (attempt + 1))
            except Exception:
                pass
    return None


def parse_citec_plain(xml_text: str) -> Optional[Dict[str, Any]]:
    """Parse citec plain.

    Args:
        xml_text (str): Input value for xml text.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None

    if root.tag.lower().endswith("error") or root.find(".//errorString") is not None:
        return None

    data = root.find(".//citationData")
    if data is None:
        return None

    def _get_text(tag: str) -> Optional[str]:
        """Get text.

        Args:
            tag (str): Input value for tag.

        Returns:
            Optional[str]: Computed result, or `None` when unavailable.
        """
        node = data.find(tag)
        if node is None or node.text is None:
            return None
        return node.text.strip() or None

    out: Dict[str, Any] = {
        "repec_handle": data.get("id") or _get_text("id"),
        "date": _get_text("date"),
        "uri": _get_text("uri"),
        "cited_by": _get_text("citedBy"),
        "cites": _get_text("cites"),
    }

    for key in ("cited_by", "cites"):
        if out.get(key) is not None:
            try:
                out[key] = int(out[key])
            except Exception:
                pass
    return out


def fetch_citec_plain(
    repec_handle: str,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """Fetch citec plain.

    Args:
        repec_handle (str): RePEc handle value.
        cache_path (Path): Path to the cache file.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    if not repec_handle:
        return None
    if os.environ.get("CITEC_DISABLE", "").strip() == "1":
        return None

    cache_key = make_cache_key(repec_handle)
    cached = get_cached_metadata(cache_path, cache_key)
    if cached:
        return cached

    url = f"{DEFAULT_BASE_URL}/plain/{requests.utils.quote(repec_handle, safe='')}"
    raw = _request_text(url, timeout=timeout)
    if not raw:
        return None
    parsed = parse_citec_plain(raw)
    if parsed:
        set_cached_metadata(cache_path, cache_key=cache_key, repec_handle=repec_handle, response=parsed)
    return parsed


def format_citec_context(meta: Optional[Dict[str, Any]]) -> str:
    """Format citec context.

    Args:
        meta (Optional[Dict[str, Any]]): Additional metadata dictionary.

    Returns:
        str: Computed string result.
    """
    if not meta:
        return ""
    lines = ["CitEc Metadata:"]
    repec_handle = meta.get("repec_handle")
    if repec_handle:
        lines.append(f"RePEc Handle: {repec_handle}")
    cited_by = meta.get("cited_by")
    if cited_by is not None:
        lines.append(f"CitEc Cited By: {cited_by}")
    cites = meta.get("cites")
    if cites is not None:
        lines.append(f"CitEc Cites: {cites}")
    date = meta.get("date")
    if date:
        lines.append(f"CitEc Date: {date}")
    uri = meta.get("uri")
    if uri:
        lines.append(f"CitEc URL: {uri}")
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
