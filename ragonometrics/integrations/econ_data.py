"""Economics data connectors (FRED, World Bank) for time-series ingestion."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests


FRED_BASE = "https://api.stlouisfed.org/fred"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"


def _request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> Optional[Any]:
    """Request json.

    Args:
        url (str): Description.
        params (Optional[Dict[str, Any]]): Description.
        timeout (int): Description.

    Returns:
        Optional[Any]: Description.

    Raises:
        Exception: Description.
    """
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
        series_id (str): Description.
        api_key (Optional[str]): Description.
        start_date (Optional[str]): Description.
        end_date (Optional[str]): Description.
        limit (Optional[int]): Description.

    Returns:
        List[Dict[str, Any]]: Description.
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
        indicator (str): Description.
        country (str): Description.
        start_year (Optional[int]): Description.
        end_year (Optional[int]): Description.
        per_page (int): Description.

    Returns:
        List[Dict[str, Any]]: Description.
    """
    params: Dict[str, Any] = {"format": "json", "per_page": per_page}
    if start_year:
        params["date"] = f"{start_year}:{end_year or ''}".strip(":")
    url = f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}"
    data = _request_json(url, params=params)
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    return data[1] or []
