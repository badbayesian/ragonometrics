"""Economics data connectors (FRED, World Bank) for time-series ingestion."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests


FRED_BASE = "https://api.stlouisfed.org/fred"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"


def _request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Any]:
    max_retries = int(os.environ.get("ECON_API_MAX_RETRIES", "2"))
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "Ragonometrics/0.1"})
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                raise requests.RequestException("rate_limited")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt >= max_retries:
                return None
            time.sleep(0.5 * (attempt + 1))
    return None


def fetch_fred_series(
    series_id: str,
    *,
    api_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch FRED series observations.

    Args:
        series_id: FRED series id (e.g., "GDPC1").
        api_key: FRED API key (or set FRED_API_KEY).
        start_date: Optional start date (YYYY-MM-DD).
        end_date: Optional end date (YYYY-MM-DD).
        limit: Optional maximum observations.

    Returns:
        List of observation dicts with "date" and "value".
    """
    key = api_key or os.environ.get("FRED_API_KEY")
    params: Dict[str, Any] = {"series_id": series_id, "file_type": "json"}
    if key:
        params["api_key"] = key
    if start_date:
        params["observation_start"] = start_date
    if end_date:
        params["observation_end"] = end_date
    if limit:
        params["limit"] = limit
    data = _request_json(f"{FRED_BASE}/series/observations", params=params)
    if not data or "observations" not in data:
        return []
    return data["observations"]


def fetch_world_bank_indicator(
    indicator: str,
    *,
    country: str = "USA",
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    per_page: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch World Bank indicator data for a country.

    Args:
        indicator: Indicator id (e.g., "NY.GDP.MKTP.CD").
        country: Country code (default "USA").
        start_year: Optional start year.
        end_year: Optional end year.
        per_page: Page size for the API.

    Returns:
        List of indicator observations.
    """
    params: Dict[str, Any] = {"format": "json", "per_page": per_page}
    if start_year:
        params["date"] = f"{start_year}:{end_year or ''}".strip(":")
    url = f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}"
    data = _request_json(url, params=params)
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    return data[1] or []
