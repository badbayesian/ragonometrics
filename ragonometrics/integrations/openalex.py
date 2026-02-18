"""OpenAlex API integration and lightweight caching for paper metadata."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from ragonometrics.db.connection import connect

DEFAULT_CACHE_PATH = Path("postgres_openalex_cache")
DEFAULT_SELECT = ",".join(
    [
        "id",
        "display_name",
        "publication_year",
        "primary_topic",
        "topics",
        "concepts",
        "primary_location",
        "host_venue",
        "doi",
        "authorships",
        "cited_by_count",
        "referenced_works_count",
        "abstract_inverted_index",
    ]
)
_TITLE_OVERRIDE_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "rows": []}
_LOOKUP_CANDIDATE_LIMIT = 10
_MAX_TITLE_LOOKUP_VARIANTS = 8


def _database_url() -> str:
    """Database url.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for OpenAlex cache persistence.")
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
        days = int(os.environ.get("OPENALEX_CACHE_TTL_DAYS", "30"))
    except Exception:
        days = 30
    return max(days, 1) * 24 * 60 * 60


def _title_override_cache_ttl_seconds() -> int:
    """TTL for title-override rule cache in seconds.

    Returns:
        int: Cache TTL.
    """
    try:
        value = int(os.environ.get("OPENALEX_TITLE_OVERRIDE_CACHE_TTL_SECONDS", "300"))
    except Exception:
        value = 300
    return max(value, 1)


def make_cache_key(
    *,
    doi: Optional[str],
    title: Optional[str],
    author: Optional[str],
    year: Optional[int],
) -> str:
    """Make cache key.

    Args:
        doi (Optional[str]): Digital Object Identifier value.
        title (Optional[str]): Paper title text.
        author (Optional[str]): Author name text.
        year (Optional[int]): Publication year.

    Returns:
        str: Computed string result.
    """
    payload = f"{(doi or '').lower()}||{(title or '').lower()}||{(author or '').lower()}||{year or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached_metadata(db_path: Path, cache_key: str) -> Optional[Dict[str, Any]]:
    """Get cached metadata.

    Args:
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    # Deprecated by design: canonical metadata persistence is
    # `enrichment.paper_openalex_metadata`, while request-level caching is handled
    # in `enrichment.openalex_http_cache`.
    _ = (db_path, cache_key)
    return None


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
        db_path (Path): Path to the local SQLite state database.
        cache_key (str): Deterministic cache lookup key.
        work_id (Optional[str]): Input value for work id.
        query (Optional[str]): Input query text.
        response (Dict[str, Any]): Response payload returned by the upstream call.
    """
    # Deprecated by design: retained for call-site compatibility.
    _ = (db_path, cache_key, work_id, query, response)


def _normalize_cache_param_value(value: Any) -> Any:
    """Normalize param values into deterministic JSON-serializable shapes.

    Args:
        value (Any): Original parameter value.

    Returns:
        Any: Normalized value.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_normalize_cache_param_value(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_cache_param_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _normalize_cache_param_value(v) for k, v in value.items()}
    return str(value)


def _cacheable_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Filter request parameters for cache keying/storage.

    Args:
        params (Optional[Dict[str, Any]]): Raw request parameters.

    Returns:
        Dict[str, Any]: Cacheable parameter map.
    """
    out: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        key_text = str(key)
        if key_text in {"api_key", "mailto"}:
            continue
        if value is None:
            continue
        out[key_text] = _normalize_cache_param_value(value)
    return out


def _http_cache_key(url: str, params: Optional[Dict[str, Any]]) -> str:
    """Build stable cache key for HTTP-level OpenAlex responses.

    Args:
        url (str): Endpoint URL.
        params (Optional[Dict[str, Any]]): Query parameters.

    Returns:
        str: Cache key hash.
    """
    normalized_params = _cacheable_params(params)
    payload = {
        "url": str(url or "").strip(),
        "params": normalized_params,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get_cached_http_response(db_path: Path, *, url: str, params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Load one cached OpenAlex HTTP response.

    Args:
        db_path (Path): Cache database path placeholder.
        url (str): Request URL.
        params (Optional[Dict[str, Any]]): Query params.

    Returns:
        Optional[Dict[str, Any]]: ``{"status_code": int, "response": Any}`` or ``None``.
    """
    try:
        conn = _connect(db_path)
    except Exception:
        return None
    try:
        cur = conn.cursor()
        request_key = _http_cache_key(url, params)
        cur.execute(
            """
            SELECT fetched_at, status_code, response
            FROM enrichment.openalex_http_cache
            WHERE request_key = %s
            """,
            (request_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at, status_code, response = row
        fetched_epoch = int(fetched_at.timestamp()) if hasattr(fetched_at, "timestamp") else int(time.time())
        if time.time() - fetched_epoch > _cache_ttl_seconds():
            return None
        return {
            "status_code": int(status_code),
            "response": response,
        }
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached_http_response(
    db_path: Path,
    *,
    url: str,
    params: Optional[Dict[str, Any]],
    status_code: int,
    response: Any,
) -> None:
    """Persist one OpenAlex HTTP response in Postgres.

    Args:
        db_path (Path): Cache database path placeholder.
        url (str): Request URL.
        params (Optional[Dict[str, Any]]): Query params.
        status_code (int): HTTP status code.
        response (Any): JSON response payload.
    """
    try:
        conn = _connect(db_path)
    except Exception:
        return
    try:
        cur = conn.cursor()
        request_key = _http_cache_key(url, params)
        params_json = _cacheable_params(params)
        response_json = response if response is not None else {}
        cur.execute(
            """
            INSERT INTO enrichment.openalex_http_cache
            (request_key, url, params_json, status_code, response, fetched_at)
            VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, NOW())
            ON CONFLICT (request_key) DO UPDATE SET
                url = EXCLUDED.url,
                params_json = EXCLUDED.params_json,
                status_code = EXCLUDED.status_code,
                response = EXCLUDED.response,
                fetched_at = EXCLUDED.fetched_at
            """,
            (
                request_key,
                str(url or ""),
                json.dumps(params_json, ensure_ascii=False),
                int(status_code),
                json.dumps(response_json, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        return
    finally:
        conn.close()


def _request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Request json.

    Args:
        url (str): Input value for url.
        params (Optional[Dict[str, Any]]): Mapping containing params.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    max_retries = int(os.environ.get("OPENALEX_MAX_RETRIES", "2"))
    raw_params = dict(params or {})
    cache_disabled = os.environ.get("OPENALEX_HTTP_CACHE_DISABLE", "").strip() == "1"
    if not cache_disabled:
        cached = _get_cached_http_response(DEFAULT_CACHE_PATH, url=url, params=raw_params)
        if cached:
            status_code = int(cached.get("status_code") or 0)
            response = cached.get("response")
            if status_code == 404:
                return None
            if 200 <= status_code < 300:
                if isinstance(response, dict):
                    return response
                try:
                    parsed = json.loads(str(response))
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return None

    payload = dict(raw_params)
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if api_key and "api_key" not in payload:
        payload["api_key"] = api_key
    mailto = (os.environ.get("OPENALEX_MAILTO") or os.environ.get("OPENALEX_EMAIL") or "").strip()
    if mailto and "mailto" not in payload:
        payload["mailto"] = mailto
    headers = {"User-Agent": "Ragonometrics/0.1"}
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=payload, headers=headers, timeout=timeout)
            if resp.status_code == 404:
                if not cache_disabled:
                    _set_cached_http_response(
                        DEFAULT_CACHE_PATH,
                        url=url,
                        params=raw_params,
                        status_code=404,
                        response={},
                    )
                return None
            if resp.status_code == 429:
                raise requests.RequestException("rate_limited")
            resp.raise_for_status()
            data = resp.json()
            if not cache_disabled and isinstance(data, (dict, list)):
                _set_cached_http_response(
                    DEFAULT_CACHE_PATH,
                    url=url,
                    params=raw_params,
                    status_code=int(resp.status_code),
                    response=data,
                )
            if isinstance(data, dict):
                return data
            return None
        except requests.RequestException:
            if attempt >= max_retries:
                return None
            try:
                time.sleep(0.5 * (attempt + 1))
            except Exception:
                pass
    return None


def request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Public cached OpenAlex JSON request helper.

    Args:
        url (str): Endpoint URL.
        params (Optional[Dict[str, Any]]): Query params.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Parsed payload or ``None``.
    """
    return _request_json(url, params=params, timeout=timeout)


def fetch_work_by_doi(doi: str, select: str = DEFAULT_SELECT, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Fetch work by doi.

    Args:
        doi (str): Digital Object Identifier value.
        select (str): Input value for select.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    if not doi:
        return None
    doi_url = doi.strip()
    if not doi_url.lower().startswith("http"):
        doi_url = f"https://doi.org/{doi_url}"
    encoded = requests.utils.quote(doi_url, safe=":/")
    url = f"https://api.openalex.org/works/{encoded}"
    return _request_json(url, params={"select": select}, timeout=timeout)


def _normalize_openalex_work_id(value: Optional[str]) -> str:
    """Normalize an OpenAlex work reference into ``W...`` format.

    Args:
        value (Optional[str]): Work id or URL.

    Returns:
        str: Normalized work id, or empty string.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    if text.startswith("https://api.openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    elif text.startswith("https://openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    if text.lower().startswith("works/"):
        text = text.split("/", 1)[-1]
    if text.lower().startswith("w"):
        text = "W" + text[1:]
    if re.fullmatch(r"W\d+", text):
        return text
    return ""


def fetch_work_by_id(work_id: str, select: str = DEFAULT_SELECT, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Fetch one OpenAlex work by id.

    Args:
        work_id (str): Work id or URL.
        select (str): Comma-separated OpenAlex fields to request.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Work payload on success.
    """
    key = _normalize_openalex_work_id(work_id)
    if not key:
        return None
    url = f"https://api.openalex.org/works/{key}"
    data = _request_json(url, params={"select": select}, timeout=timeout)
    if not data:
        data = _request_json(url, params=None, timeout=timeout)
    return data if isinstance(data, dict) else None


def search_work(query: str, select: str = DEFAULT_SELECT, limit: int = 1, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Search work.

    Args:
        query (str): Input query text.
        select (str): Input value for select.
        limit (int): Maximum number of records to process.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
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
    text = text.replace("_", " ")
    text = text.replace("&ast;", "*")
    text = html.unescape(text)
    # Remove common filename suffix patterns such as:
    # " - Author et al. (2010)" or generic " - ... (YYYY)" tails.
    text = re.sub(r"\s*-\s*[^-]*\bet al\.?\s*\(\d{4}\)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-\s*[^-]*\(\d{4}\)\s*$", "", text, flags=re.IGNORECASE)
    # Remove raw file extension artifacts if present.
    text = re.sub(r"\.(pdf|indd|dvi)\b", "", text, flags=re.IGNORECASE)
    # Remove trailing footnote markers commonly present in extracted titles.
    text = re.sub(r"[\*\u2020\u2021]+$", "", text).strip()
    # Normalize whitespace and strip outer quotes.
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip("'")
    return text


def _normalize_author_for_search(author: Optional[str]) -> Optional[str]:
    """Normalize author input for OpenAlex search query construction.

    Args:
        author (Optional[str]): Raw author text.

    Returns:
        Optional[str]: Clean author string, or ``None`` for placeholders.
    """
    text = re.sub(r"\s+", " ", str(author or "").strip())
    if not text:
        return None
    lowered = text.lower()
    if re.fullmatch(r"-+", lowered):
        return None
    placeholder_key = re.sub(r"[^a-z0-9]+", "", lowered)
    if placeholder_key in {"unknown", "unknownauthor", "na", "none", "null"}:
        return None
    return text


def _build_title_lookup_variants(title: str) -> List[str]:
    """Build deterministic title variants for fallback OpenAlex lookup.

    Args:
        title (str): Raw title text.

    Returns:
        List[str]: Ordered, de-duplicated title variants.
    """
    base = _sanitize_title_for_lookup(title)
    if not base:
        return []

    out: List[str] = []
    seen = set()

    def _add_variant(value: str) -> None:
        cleaned = _sanitize_title_for_lookup(value)
        if not cleaned:
            return
        key = re.sub(r"\s+", " ", cleaned).strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(cleaned)

    def _swap_token(text: str, pattern: str, replacement: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            token = str(match.group(0) or "")
            if token.isupper():
                return replacement.upper()
            if token[:1].isupper() and token[1:].islower():
                return replacement.capitalize()
            return replacement

        return re.sub(pattern, _replace, text, flags=re.IGNORECASE)

    _add_variant(base)
    swap_rules = [
        (r"\bprojects\b", "projections"),
        (r"\bprojections\b", "projects"),
        (r"\bvar\b", "vars"),
        (r"\bvars\b", "var"),
    ]
    for pattern, replacement in swap_rules:
        snapshot = list(out)
        for candidate in snapshot:
            swapped = _swap_token(candidate, pattern, replacement)
            if swapped != candidate:
                _add_variant(swapped)
            if len(out) >= _MAX_TITLE_LOOKUP_VARIANTS:
                return out[:_MAX_TITLE_LOOKUP_VARIANTS]

    for candidate in list(out):
        punctuation_light = re.sub(r"[^A-Za-z0-9\s]+", " ", candidate)
        punctuation_light = re.sub(r"\s+", " ", punctuation_light).strip()
        if punctuation_light:
            _add_variant(punctuation_light)
        if len(out) >= _MAX_TITLE_LOOKUP_VARIANTS:
            break

    return out[:_MAX_TITLE_LOOKUP_VARIANTS]


def _title_key(title: str) -> str:
    """Normalize a title for fuzzy matching.

    Args:
        title (str): Raw title text.

    Returns:
        str: Lowercased alphanumeric title key.
    """
    text = _sanitize_title_for_lookup(title).lower()
    if not text:
        return ""
    text = re.sub(r"\bet\s+al\.?\b", " ", text)
    text = re.sub(r"\(\d{4}\)", " ", text)
    text = re.sub(r"\b\d{4}\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_title_override_rows(db_path: Path = DEFAULT_CACHE_PATH) -> List[Dict[str, str]]:
    """Load active title override rows from Postgres with TTL caching.

    Args:
        db_path (Path): Placeholder path for API compatibility.

    Returns:
        List[Dict[str, str]]: Override rows with ``title_pattern``, ``match_type``, and ``openalex_work_id``.
    """
    now = time.time()
    cached_rows = _TITLE_OVERRIDE_CACHE.get("rows") or []
    loaded_at = float(_TITLE_OVERRIDE_CACHE.get("loaded_at") or 0.0)
    if cached_rows and (now - loaded_at) < _title_override_cache_ttl_seconds():
        return list(cached_rows)

    try:
        conn = _connect(db_path)
    except Exception:
        return list(cached_rows)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT title_pattern, match_type, openalex_work_id
            FROM enrichment.openalex_title_overrides
            WHERE enabled = TRUE
            ORDER BY priority DESC, updated_at DESC, id DESC
            """
        )
        rows = cur.fetchall() or []
    except Exception:
        return list(cached_rows)
    finally:
        conn.close()

    parsed: List[Dict[str, str]] = []
    for title_pattern, match_type, openalex_work_id in rows:
        pattern_text = str(title_pattern or "").strip()
        match_type_text = str(match_type or "contains").strip().lower()
        work_id_text = str(openalex_work_id or "").strip()
        if not pattern_text or not work_id_text:
            continue
        if match_type_text not in {"contains", "exact"}:
            match_type_text = "contains"
        parsed.append(
            {
                "title_pattern": pattern_text,
                "match_type": match_type_text,
                "openalex_work_id": work_id_text,
            }
        )

    _TITLE_OVERRIDE_CACHE["rows"] = parsed
    _TITLE_OVERRIDE_CACHE["loaded_at"] = now
    return list(parsed)


def _title_override_work_id(title: Optional[str], *, cache_path: Path = DEFAULT_CACHE_PATH) -> str:
    """Return forced OpenAlex work id for titles configured in Postgres.

    Args:
        title (Optional[str]): Candidate title text.
        cache_path (Path): Placeholder path for API compatibility.

    Returns:
        str: OpenAlex work id in ``W...`` format, or empty string.
    """
    query_key = _title_key(str(title or ""))
    if not query_key:
        return ""
    rows = _load_title_override_rows(cache_path)
    for row in rows:
        fragment_key = _title_key(str(row.get("title_pattern") or ""))
        if not fragment_key:
            continue
        match_type = str(row.get("match_type") or "contains").strip().lower()
        matched = query_key == fragment_key if match_type == "exact" else (fragment_key in query_key)
        if not matched:
            continue
        normalized_work_id = _normalize_openalex_work_id(str(row.get("openalex_work_id") or ""))
        if normalized_work_id:
            return normalized_work_id
    return ""


def get_title_override_work_id(title: Optional[str], *, cache_path: Path = DEFAULT_CACHE_PATH) -> str:
    """Public helper to resolve a configured OpenAlex work-id override.

    Args:
        title (Optional[str]): Candidate paper title.
        cache_path (Path): Placeholder path for API compatibility.

    Returns:
        str: OpenAlex work id in ``W...`` format, or empty string.
    """
    return _title_override_work_id(title, cache_path=cache_path)


def _titles_match(query_title: Optional[str], candidate_title: Optional[str]) -> bool:
    """Check whether candidate title matches the requested title.

    Args:
        query_title (Optional[str]): Requested title.
        candidate_title (Optional[str]): Candidate OpenAlex title.

    Returns:
        bool: ``True`` when titles appear to refer to the same paper.
    """
    q = _title_key(str(query_title or ""))
    c = _title_key(str(candidate_title or ""))
    if not q or not c:
        return False
    if q == c:
        return True
    if len(q) > 12 and q in c:
        return True
    if len(c) > 12 and c in q:
        return True
    score = SequenceMatcher(a=q, b=c).ratio()
    if score >= 0.84:
        return True
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    if len(q_tokens) >= 4 and len(c_tokens) >= 4:
        overlap = len(q_tokens & c_tokens)
        if overlap / max(1, len(q_tokens)) >= 0.78:
            return True
    return False


def _author_last_names(text: Optional[str]) -> List[str]:
    """Extract likely author last names from free-form author text.

    Args:
        text (Optional[str]): Author string.

    Returns:
        List[str]: Lowercased last-name candidates.
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    cleaned = re.sub(r"\bet\s+al\.?\b", " ", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+and\s+", ",", cleaned, flags=re.IGNORECASE)
    parts = [p.strip() for p in re.split(r"[;,]", cleaned) if p.strip()]
    out: List[str] = []
    seen = set()
    for part in parts:
        tokens = [t for t in re.split(r"\s+", part) if t]
        if not tokens:
            continue
        last = re.sub(r"[^a-zA-Z-]", "", tokens[-1]).lower()
        if len(last) < 2:
            continue
        if last in {"unknown", "company"}:
            continue
        if last in seen:
            continue
        seen.add(last)
        out.append(last)
    return out


def _meta_author_last_names(meta: Optional[Dict[str, Any]]) -> List[str]:
    """Extract author last names from OpenAlex work metadata.

    Args:
        meta (Optional[Dict[str, Any]]): OpenAlex work payload.

    Returns:
        List[str]: Lowercased last names in authorships.
    """
    if not isinstance(meta, dict):
        return []
    out: List[str] = []
    seen = set()
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        display_name = str(author_obj.get("display_name") or "").strip()
        if not display_name:
            continue
        tokens = [t for t in re.split(r"\s+", display_name) if t]
        if not tokens:
            continue
        last = re.sub(r"[^a-zA-Z-]", "", tokens[-1]).lower()
        if len(last) < 2:
            continue
        if last in seen:
            continue
        seen.add(last)
        out.append(last)
    return out


def _is_plausible_match(
    *,
    title: Optional[str],
    author: Optional[str],
    year: Optional[int],
    meta: Optional[Dict[str, Any]],
) -> bool:
    """Validate whether an OpenAlex candidate plausibly matches the request.

    Args:
        title (Optional[str]): Requested title.
        author (Optional[str]): Requested author text.
        year (Optional[int]): Requested publication year.
        meta (Optional[Dict[str, Any]]): Candidate OpenAlex work.

    Returns:
        bool: ``True`` when title/author/year checks are consistent.
    """
    if not isinstance(meta, dict) or not meta:
        return False

    if title:
        candidate_title = str(meta.get("display_name") or meta.get("title") or "")
        if not _titles_match(title, candidate_title):
            return False

    if year is not None:
        candidate_year = meta.get("publication_year")
        if candidate_year is not None:
            try:
                if abs(int(candidate_year) - int(year)) > 1:
                    return False
            except Exception:
                pass

    requested_last = _author_last_names(author)
    if requested_last:
        candidate_last = _meta_author_last_names(meta)
        if candidate_last and not (set(requested_last) & set(candidate_last)):
            return False

    return True


def _search_work_results(
    query: str,
    *,
    limit: int,
    select: str,
    timeout: int,
) -> List[Dict[str, Any]]:
    """Search OpenAlex works and return candidate result rows.

    Args:
        query (str): Search query text.
        limit (int): Maximum number of rows to request.
        select (str): Comma-separated OpenAlex fields to request.
        timeout (int): Request timeout in seconds.

    Returns:
        List[Dict[str, Any]]: Candidate work payloads.
    """
    query_text = str(query or "").strip()
    if not query_text:
        return []
    max_items = max(1, min(int(limit), 50))
    url = "https://api.openalex.org/works"
    data = _request_json(
        url,
        params={"search": query_text, "per-page": max_items, "select": select},
        timeout=timeout,
    )
    if not data:
        data = _request_json(
            url,
            params={"search": query_text, "per-page": max_items},
            timeout=timeout,
        )
    if not isinstance(data, dict):
        return []
    items = data.get("results") or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _choose_best_plausible_candidate(
    *,
    candidates: List[Dict[str, Any]],
    requested_title: Optional[str],
    author: Optional[str],
    year: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Select the best plausible OpenAlex match from a candidate list.

    Args:
        candidates (List[Dict[str, Any]]): Candidate OpenAlex rows.
        requested_title (Optional[str]): Requested paper title.
        author (Optional[str]): Requested author text.
        year (Optional[int]): Requested publication year.

    Returns:
        Optional[Dict[str, Any]]: Best plausible candidate, if any.
    """
    plausible = [
        item
        for item in candidates
        if _is_plausible_match(title=requested_title, author=author, year=year, meta=item)
    ]
    if not plausible:
        return None
    requested_key = _title_key(str(requested_title or ""))

    def _score(item: Dict[str, Any]) -> tuple:
        candidate_title = str(item.get("display_name") or item.get("title") or "")
        candidate_key = _title_key(candidate_title)
        similarity = SequenceMatcher(a=requested_key, b=candidate_key).ratio() if requested_key and candidate_key else 0.0
        if year is None:
            year_delta = 0
        else:
            try:
                year_delta = abs(int(item.get("publication_year")) - int(year))
            except Exception:
                year_delta = 9999
        try:
            cited = int(item.get("cited_by_count") or 0)
        except Exception:
            cited = 0
        stable_key = str(item.get("id") or candidate_title or "")
        return (-similarity, year_delta, -cited, stable_key)

    plausible.sort(key=_score)
    return plausible[0]


def is_economics_work(meta: Optional[Dict[str, Any]]) -> bool:
    """Return whether an OpenAlex work payload appears to be economics.

    The check is intentionally permissive because OpenAlex topic assignment can
    place economics papers under adjacent fields (for example marketing). We
    accept any of the following as evidence:
    - a concept/topic/field/subfield containing ``economics``;
    - a venue/source display name containing ``economic``.

    Args:
        meta (Optional[Dict[str, Any]]): OpenAlex work payload.

    Returns:
        bool: ``True`` if economics evidence is present, else ``False``.
    """
    if not isinstance(meta, dict) or not meta:
        return False

    labels: List[str] = []

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if text:
            labels.append(text.lower())

    primary_topic = meta.get("primary_topic") or {}
    if isinstance(primary_topic, dict):
        _add(primary_topic.get("display_name"))
        field = primary_topic.get("field") or {}
        subfield = primary_topic.get("subfield") or {}
        domain = primary_topic.get("domain") or {}
        if isinstance(field, dict):
            _add(field.get("display_name"))
        if isinstance(subfield, dict):
            _add(subfield.get("display_name"))
        if isinstance(domain, dict):
            _add(domain.get("display_name"))

    for concept in meta.get("concepts") or []:
        if isinstance(concept, dict):
            _add(concept.get("display_name"))

    for topic in meta.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        _add(topic.get("display_name"))
        field = topic.get("field") or {}
        subfield = topic.get("subfield") or {}
        domain = topic.get("domain") or {}
        if isinstance(field, dict):
            _add(field.get("display_name"))
        if isinstance(subfield, dict):
            _add(subfield.get("display_name"))
        if isinstance(domain, dict):
            _add(domain.get("display_name"))

    primary_location = meta.get("primary_location") or {}
    if isinstance(primary_location, dict):
        source = primary_location.get("source") or {}
        if isinstance(source, dict):
            _add(source.get("display_name"))

    host_venue = meta.get("host_venue") or {}
    if isinstance(host_venue, dict):
        _add(host_venue.get("display_name"))

    if any(label == "economics" for label in labels):
        return True
    if any("economics" in label for label in labels):
        return True
    if any("economic" in label for label in labels):
        return True
    return False


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


def search_authors_by_name(
    name: str,
    *,
    limit: int = 5,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Search OpenAlex authors by display name.

    Args:
        name (str): Author name query.
        limit (int): Maximum number of authors to return.
        timeout (int): Request timeout in seconds.

    Returns:
        List[Dict[str, Any]]: Candidate author records.
    """
    text = str(name or "").strip()
    if not text:
        return []
    max_items = max(1, min(int(limit), 50))
    url = "https://api.openalex.org/authors"
    data = _request_json(
        url,
        params={
            "filter": f"display_name.search:{text}",
            "per-page": max_items,
            "sort": "works_count:desc",
        },
        timeout=timeout,
    )
    if not data:
        data = _request_json(
            url,
            params={
                "search": text,
                "per-page": max_items,
            },
            timeout=timeout,
        )
    if not data:
        return []
    results = data.get("results") or []
    if not isinstance(results, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            out.append(item)
        if len(out) >= max_items:
            break
    return out


def list_works_for_author(
    author_id: str,
    *,
    per_page: int = 50,
    max_pages: int = 3,
    select: str = DEFAULT_SELECT,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """List OpenAlex works for a specific author id.

    Args:
        author_id (str): OpenAlex author id (full URL or ``A...``).
        per_page (int): Page size.
        max_pages (int): Maximum pages to request.
        select (str): Comma-separated fields to request.
        timeout (int): Request timeout in seconds.

    Returns:
        List[Dict[str, Any]]: Works for the requested author.
    """
    raw_author_id = str(author_id or "").strip()
    if not raw_author_id:
        return []
    normalized_author_id = raw_author_id
    if not normalized_author_id.startswith("http"):
        normalized_author_id = f"https://openalex.org/{normalized_author_id}"

    size = max(1, min(int(per_page), 200))
    pages = max(1, min(int(max_pages), 20))
    works: List[Dict[str, Any]] = []
    url = "https://api.openalex.org/works"
    for page in range(1, pages + 1):
        data = _request_json(
            url,
            params={
                "filter": f"author.id:{normalized_author_id}",
                "per-page": size,
                "page": page,
                "select": select,
            },
            timeout=timeout,
        )
        if not data:
            data = _request_json(
                url,
                params={
                    "filter": f"author.id:{normalized_author_id}",
                    "per-page": size,
                    "page": page,
                },
                timeout=timeout,
            )
        if not data:
            break
        results = data.get("results") or []
        if not isinstance(results, list) or not results:
            break
        page_count = 0
        for item in results:
            if isinstance(item, dict):
                works.append(item)
                page_count += 1
        if page_count < size:
            break
    return works


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

    payload = _request_json(
        "https://api.openalex.org/works",
        params={
            "search": f'"{title_text}"',
            "per-page": 1,
            "api_key": api_key_text,
        },
        timeout=timeout,
    )
    if not payload:
        return []
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
        title (Optional[str]): Paper title text.
        author (Optional[str]): Author name text.
        year (Optional[int]): Publication year.
        doi (Optional[str]): Digital Object Identifier value.
        cache_path (Path): Path to the cache file.
        timeout (int): Timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Computed result, or `None` when unavailable.
    """
    if os.environ.get("OPENALEX_DISABLE", "").strip() == "1":
        return None

    author_for_search = _normalize_author_for_search(author)
    override_work_id = _title_override_work_id(title, cache_path=cache_path)
    cache_key = make_cache_key(doi=doi, title=title, author=author, year=year)
    cached = get_cached_metadata(cache_path, cache_key)
    if cached:
        if doi and _is_plausible_match(title=title, author=author_for_search, year=year, meta=cached):
            return cached
        if override_work_id:
            cached_id = _normalize_openalex_work_id(str(cached.get("id") or ""))
            if cached_id and cached_id == override_work_id:
                return cached
        if not override_work_id and _is_plausible_match(title=title, author=author_for_search, year=year, meta=cached):
            return cached

    data = None
    work_id = None
    used_title_override = False

    if doi:
        data = fetch_work_by_doi(doi, timeout=timeout)
        if data:
            work_id = data.get("id")

    if not data and override_work_id:
        forced = fetch_work_by_id(override_work_id, timeout=timeout)
        if isinstance(forced, dict) and forced:
            data = forced
            work_id = forced.get("id") or f"https://openalex.org/{override_work_id}"
            used_title_override = True

    if not data and title:
        # Prefer exact title lookup first; then broaden search with author/year.
        clean_title = _sanitize_title_for_lookup(title)
        lookup_title = clean_title or str(title or "").strip()
        if clean_title:
            candidate = search_work_by_title(clean_title, timeout=timeout)
            if _is_plausible_match(title=clean_title, author=author_for_search, year=year, meta=candidate):
                data = candidate
        if not data and clean_title:
            candidate = search_work_by_title_author_year(
                title=clean_title,
                author=author_for_search,
                year=year,
                timeout=timeout,
            )
            if _is_plausible_match(title=clean_title, author=author_for_search, year=year, meta=candidate):
                data = candidate
        if not data and clean_title and year is not None:
            candidate = search_work_by_title_author_year(
                title=clean_title,
                author=author_for_search,
                year=None,
                timeout=timeout,
            )
            if _is_plausible_match(title=clean_title, author=author_for_search, year=None, meta=candidate):
                data = candidate
        if not data and (clean_title or title):
            candidate = search_work_by_title_author_year(
                title=clean_title or title,
                author=author_for_search,
                year=year,
                timeout=timeout,
            )
            if _is_plausible_match(title=clean_title or title, author=author_for_search, year=year, meta=candidate):
                data = candidate
        if not data and lookup_title:
            variants = _build_title_lookup_variants(lookup_title)
            candidates: List[Dict[str, Any]] = []
            seen_candidates = set()
            for variant in variants:
                query_shapes: List[str] = [f'"{variant}"']
                if author_for_search and year is not None:
                    query_shapes.append(f"{variant} {author_for_search} {year}")
                if author_for_search:
                    query_shapes.append(f"{variant} {author_for_search}")
                if year is not None:
                    query_shapes.append(f"{variant} {year}")
                query_shapes.append(variant)
                for query in query_shapes:
                    results = _search_work_results(
                        query,
                        limit=_LOOKUP_CANDIDATE_LIMIT,
                        select=DEFAULT_SELECT,
                        timeout=timeout,
                    )
                    for meta in results:
                        normalized_id = _normalize_openalex_work_id(str(meta.get("id") or ""))
                        fallback_key = normalized_id or (
                            f"no-id::{_title_key(str(meta.get('display_name') or meta.get('title') or ''))}"
                            f"::{meta.get('publication_year')}::{meta.get('cited_by_count')}"
                        )
                        if fallback_key in seen_candidates:
                            continue
                        seen_candidates.add(fallback_key)
                        candidates.append(meta)
            chosen = _choose_best_plausible_candidate(
                candidates=candidates,
                requested_title=lookup_title,
                author=author_for_search,
                year=year,
            )
            if chosen:
                data = chosen
        work_id = data.get("id") if isinstance(data, dict) else None

    if data and (doi or used_title_override or _is_plausible_match(title=title, author=author_for_search, year=year, meta=data)):
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
        inv (Optional[Dict[str, Any]]): Mapping containing inv.

    Returns:
        str: Computed string result.
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
        meta (Dict[str, Any]): Additional metadata dictionary.

    Returns:
        Optional[str]: Computed result, or `None` when unavailable.
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
        meta (Optional[Dict[str, Any]]): Additional metadata dictionary.
        max_abstract_chars (int): Input value for max abstract chars.
        max_authors (int): Input value for max authors.

    Returns:
        str: Computed string result.
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
