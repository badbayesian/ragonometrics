"""OpenAlex API integration and lightweight caching for paper metadata."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
import psycopg2

DEFAULT_CACHE_PATH = Path("postgres_openalex_cache")
DEFAULT_SELECT = ",".join(
    [
        "id",
        "display_name",
        "publication_year",
        "primary_location",
        "host_venue",
        "doi",
        "authorships",
        "cited_by_count",
        "referenced_works_count",
        "abstract_inverted_index",
    ]
)


def _database_url() -> str:
    """Database url.

    Returns:
        str: Description.

    Raises:
        Exception: Description.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for OpenAlex cache persistence.")
    return db_url


def _connect(_db_path: Path) -> psycopg2.extensions.connection:
    """Connect.

    Args:
        _db_path (Path): Description.

    Returns:
        psycopg2.extensions.connection: Description.
    """
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS enrichment")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment.openalex_cache (
            cache_key TEXT PRIMARY KEY,
            work_id TEXT,
            query TEXT,
            response JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS enrichment_openalex_cache_fetched_at_idx
        ON enrichment.openalex_cache(fetched_at DESC)
        """
    )
    conn.commit()
    return conn


def _cache_ttl_seconds() -> int:
    """Cache ttl seconds.

    Returns:
        int: Description.
    """
    try:
        days = int(os.environ.get("OPENALEX_CACHE_TTL_DAYS", "30"))
    except Exception:
        days = 30
    return max(days, 1) * 24 * 60 * 60


def make_cache_key(
    *,
    doi: Optional[str],
    title: Optional[str],
    author: Optional[str],
    year: Optional[int],
) -> str:
    """Make cache key.

    Args:
        doi (Optional[str]): Description.
        title (Optional[str]): Description.
        author (Optional[str]): Description.
        year (Optional[int]): Description.

    Returns:
        str: Description.
    """
    payload = f"{(doi or '').lower()}||{(title or '').lower()}||{(author or '').lower()}||{year or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached_metadata(db_path: Path, cache_key: str) -> Optional[Dict[str, Any]]:
    """Get cached metadata.

    Args:
        db_path (Path): Description.
        cache_key (str): Description.

    Returns:
        Optional[Dict[str, Any]]: Description.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT fetched_at, response FROM enrichment.openalex_cache WHERE cache_key = %s",
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


def set_cached_metadata(
    db_path: Path,
    *,
    cache_key: str,
    work_id: Optional[str],
    query: Optional[str],
    response: Dict[str, Any],
) -> None:
    """Set cached metadata.

    Args:
        db_path (Path): Description.
        cache_key (str): Description.
        work_id (Optional[str]): Description.
        query (Optional[str]): Description.
        response (Dict[str, Any]): Description.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO enrichment.openalex_cache
            (cache_key, work_id, query, response, fetched_at)
            VALUES (%s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT (cache_key) DO UPDATE SET
                work_id = EXCLUDED.work_id,
                query = EXCLUDED.query,
                response = EXCLUDED.response,
                fetched_at = EXCLUDED.fetched_at
            """,
            (
                cache_key,
                work_id,
                query,
                json.dumps(response, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Request json.

    Args:
        url (str): Description.
        params (Optional[Dict[str, Any]]): Description.
        timeout (int): Description.

    Returns:
        Optional[Dict[str, Any]]: Description.

    Raises:
        Exception: Description.
    """
    max_retries = int(os.environ.get("OPENALEX_MAX_RETRIES", "2"))
    payload = dict(params or {})
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key:
        payload["api_key"] = api_key
    mailto = (os.environ.get("OPENALEX_MAILTO") or os.environ.get("OPENALEX_EMAIL") or "").strip()
    if mailto:
        payload["mailto"] = mailto
    headers = {"User-Agent": "Ragonometrics/0.1"}
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=payload, headers=headers, timeout=timeout)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                raise requests.RequestException("rate_limited")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt >= max_retries:
                return None
            try:
                time.sleep(0.5 * (attempt + 1))
            except Exception:
                pass
    return None


def fetch_work_by_doi(doi: str, select: str = DEFAULT_SELECT, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Fetch work by doi.

    Args:
        doi (str): Description.
        select (str): Description.
        timeout (int): Description.

    Returns:
        Optional[Dict[str, Any]]: Description.
    """
    if not doi:
        return None
    doi_url = doi.strip()
    if not doi_url.lower().startswith("http"):
        doi_url = f"https://doi.org/{doi_url}"
    encoded = requests.utils.quote(doi_url, safe=":/")
    url = f"https://api.openalex.org/works/{encoded}"
    return _request_json(url, params={"select": select}, timeout=timeout)


def search_work(query: str, select: str = DEFAULT_SELECT, limit: int = 1, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Search work.

    Args:
        query (str): Description.
        select (str): Description.
        limit (int): Description.
        timeout (int): Description.

    Returns:
        Optional[Dict[str, Any]]: Description.
    """
    if not query:
        return None
    url = "https://api.openalex.org/works"
    data = _request_json(
        url,
        params={"search": query, "per-page": limit, "select": select},
        timeout=timeout,
    )
    # Some OpenAlex search requests reject broader `select` sets with HTTP 400.
    # Retry without `select` to preserve lookup robustness.
    if not data:
        data = _request_json(
            url,
            params={"search": query, "per-page": limit},
            timeout=timeout,
        )
    if not data:
        return None
    items = data.get("results") or []
    if not items:
        return None
    if isinstance(items, list):
        return items[0]
    return None


def _sanitize_title_for_lookup(title: str) -> str:
    """Sanitize extracted title text before sending it to OpenAlex search.

    Args:
        title (str): Raw title text.

    Returns:
        str: Normalized title text for OpenAlex lookup.
    """
    text = str(title or "").strip()
    if not text:
        return ""
    text = text.replace("&ast;", "*")
    text = html.unescape(text)
    # Remove trailing footnote markers commonly present in extracted titles.
    text = re.sub(r"[\*\u2020\u2021]+$", "", text).strip()
    # Normalize whitespace and strip outer quotes.
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip("'")
    return text


def search_work_by_title(
    title: str,
    select: str = DEFAULT_SELECT,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """Search OpenAlex by exact title using quoted query text.

    Args:
        title (str): Paper title.
        select (str): Comma-separated OpenAlex fields to request.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: First matched OpenAlex work, or ``None``.
    """
    title_text = _sanitize_title_for_lookup(title)
    if not title_text:
        return None
    query = f'"{title_text}"'
    url = "https://api.openalex.org/works"
    data = _request_json(
        url,
        params={"search": query, "per-page": 1, "select": select},
        timeout=timeout,
    )
    if not data:
        data = _request_json(
            url,
            params={"search": query, "per-page": 1},
            timeout=timeout,
        )
    if not data:
        return None
    items = data.get("results") or []
    if not items:
        return None
    if isinstance(items, list):
        return items[0]
    return None


def search_work_by_title_author_year(
    *,
    title: str,
    author: Optional[str] = None,
    year: Optional[int] = None,
    select: str = DEFAULT_SELECT,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """Search OpenAlex works using title + optional author + optional year.

    This helper implements the explicit request shape:
    ``GET /works?search=<title + author + year>&per-page=1&select=<fields>``.

    Args:
        title (str): Paper title.
        author (Optional[str]): Author string to append to the search query.
        year (Optional[int]): Publication year to append to the search query.
        select (str): Comma-separated OpenAlex fields to request.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: First matched OpenAlex work, or ``None``.
    """
    title_text = _sanitize_title_for_lookup(title)
    if not title_text:
        return None
    query_parts = [title_text]
    author_text = str(author or "").strip()
    if author_text:
        query_parts.append(author_text)
    if year is not None:
        query_parts.append(str(year))
    query = " ".join(query_parts)
    url = "https://api.openalex.org/works"
    data = _request_json(
        url,
        params={"search": query, "per-page": 1, "select": select},
        timeout=timeout,
    )
    if not data:
        data = _request_json(
            url,
            params={"search": query, "per-page": 1},
            timeout=timeout,
        )
    if not data:
        return None
    items = data.get("results") or []
    if not items:
        return None
    if isinstance(items, list):
        return items[0]
    return None


def openalex_work_by_title(title: str, api_key: str, timeout: int = 30) -> List[Dict[str, Any]]:
    """Look up OpenAlex works by title using the direct ``/works`` search endpoint.

    Args:
        title (str): Paper title to search.
        api_key (str): OpenAlex API key.
        timeout (int): Request timeout in seconds.

    Returns:
        List[Dict[str, Any]]: Result list from the OpenAlex response payload.

    Raises:
        RuntimeError: If title or API key is empty.
        requests.HTTPError: If OpenAlex returns an HTTP error response.
    """
    title_text = _sanitize_title_for_lookup(title)
    api_key_text = str(api_key or "").strip()
    if not title_text:
        raise RuntimeError("title is required")
    if not api_key_text:
        raise RuntimeError("api_key is required")

    q = quote(f'"{title_text}"')
    url = f"https://api.openalex.org/works?search={q}&per-page=1"
    resp = requests.get(url, params={"api_key": api_key_text}, timeout=timeout)
    resp.raise_for_status()
    payload_raw = resp.json()
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def fetch_openalex_metadata(
    *,
    title: Optional[str],
    author: Optional[str],
    year: Optional[int] = None,
    doi: Optional[str] = None,
    cache_path: Path = DEFAULT_CACHE_PATH,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """Fetch OpenAlex metadata for a paper, using DOI when possible.

    Args:
        title (Optional[str]): Description.
        author (Optional[str]): Description.
        year (Optional[int]): Description.
        doi (Optional[str]): Description.
        cache_path (Path): Description.
        timeout (int): Description.

    Returns:
        Optional[Dict[str, Any]]: Description.
    """
    if os.environ.get("OPENALEX_DISABLE", "").strip() == "1":
        return None

    cache_key = make_cache_key(doi=doi, title=title, author=author, year=year)
    cached = get_cached_metadata(cache_path, cache_key)
    if cached:
        return cached

    data = None
    work_id = None

    if doi:
        data = fetch_work_by_doi(doi, timeout=timeout)
        if data:
            work_id = data.get("id")

    if not data and title:
        # Prefer exact title lookup first; then broaden search with author/year.
        clean_title = _sanitize_title_for_lookup(title)
        if clean_title:
            data = search_work_by_title(clean_title, timeout=timeout)
        if not data and clean_title:
            data = search_work_by_title_author_year(
                title=clean_title,
                author=author,
                year=year,
                timeout=timeout,
            )
        if not data and clean_title and year is not None:
            data = search_work_by_title_author_year(
                title=clean_title,
                author=author,
                year=None,
                timeout=timeout,
            )
        if not data:
            data = search_work_by_title_author_year(
                title=clean_title or title,
                author=author,
                year=year,
                timeout=timeout,
            )
        work_id = data.get("id") if isinstance(data, dict) else None

    if data:
        set_cached_metadata(
            cache_path,
            cache_key=cache_key,
            work_id=work_id,
            query=title or "",
            response=data,
        )
    return data


def _abstract_from_inverted_index(inv: Optional[Dict[str, Any]]) -> str:
    """Abstract from inverted index.

    Args:
        inv (Optional[Dict[str, Any]]): Description.

    Returns:
        str: Description.
    """
    if not inv or not isinstance(inv, dict):
        return ""
    positions = []
    for vals in inv.values():
        if isinstance(vals, list):
            positions.extend([v for v in vals if isinstance(v, int)])
    if not positions:
        return ""
    max_pos = max(positions)
    words: List[str] = [""] * (max_pos + 1)
    for token, offsets in inv.items():
        if not isinstance(offsets, list):
            continue
        for pos in offsets:
            if isinstance(pos, int) and 0 <= pos <= max_pos:
                words[pos] = token
    return " ".join([w for w in words if w])


def _get_venue(meta: Dict[str, Any]) -> Optional[str]:
    """Get venue.

    Args:
        meta (Dict[str, Any]): Description.

    Returns:
        Optional[str]: Description.
    """
    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = source.get("display_name")
    if venue:
        return venue
    host = meta.get("host_venue") or {}
    return host.get("display_name")


def format_openalex_context(
    meta: Optional[Dict[str, Any]],
    *,
    max_abstract_chars: int = 1200,
    max_authors: int = 8,
) -> str:
    """Format OpenAlex metadata into a compact context block.

    Args:
        meta (Optional[Dict[str, Any]]): Description.
        max_abstract_chars (int): Description.
        max_authors (int): Description.

    Returns:
        str: Description.
    """
    if not meta:
        return ""
    lines = ["OpenAlex Metadata:"]

    title = meta.get("display_name") or meta.get("title")
    if title:
        lines.append(f"Title: {title}")

    authors = meta.get("authorships") or []
    names: List[str] = []
    for author in authors:
        if isinstance(author, dict):
            author_obj = author.get("author") or {}
            name = author_obj.get("display_name")
            if name:
                names.append(name)
    if names:
        suffix = " et al." if len(names) > max_authors else ""
        lines.append(f"Authors: {', '.join(names[:max_authors])}{suffix}")

    year = meta.get("publication_year")
    if year:
        lines.append(f"Year: {year}")

    venue = _get_venue(meta)
    if venue:
        lines.append(f"Venue: {venue}")

    doi = meta.get("doi")
    if doi:
        lines.append(f"DOI: {doi}")

    url = meta.get("id")
    landing = (meta.get("primary_location") or {}).get("landing_page_url")
    if landing:
        lines.append(f"URL: {landing}")
    elif url:
        lines.append(f"URL: {url}")

    citation_count = meta.get("cited_by_count")
    if citation_count is not None:
        lines.append(f"Citation Count: {citation_count}")

    reference_count = meta.get("referenced_works_count")
    if reference_count is None and isinstance(meta.get("referenced_works"), list):
        reference_count = len(meta.get("referenced_works"))
    if reference_count is not None:
        lines.append(f"Reference Count: {reference_count}")

    abstract = _abstract_from_inverted_index(meta.get("abstract_inverted_index"))
    if abstract:
        if len(abstract) > max_abstract_chars:
            abstract = abstract[: max_abstract_chars - 3].rstrip() + "..."
        lines.append(f"Abstract: {abstract}")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)
