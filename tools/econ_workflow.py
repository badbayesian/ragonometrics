"""Example economics workflow: fetch time series, compute growth, and emit a report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import List, Tuple

import numpy as np

from ragonometrics.integrations.econ_data import fetch_fred_series


def _clean_series(observations) -> List[Tuple[str, float]]:
    out = []
    for obs in observations:
        val = obs.get("value")
        if val in (None, ".", ""):
            continue
        try:
            fval = float(val)
        except Exception:
            continue
        out.append((obs.get("date"), fval))
    return out


def _growth(values: List[float]) -> List[float]:
    growth = []
    for i in range(1, len(values)):
        if values[i - 1] == 0:
            growth.append(0.0)
        else:
            growth.append(math.log(values[i] / values[i - 1]))
    return growth


def _sample_data():
    dates = [f"2024-Q{i}" for i in range(1, 9)]
    gdp = [100.0, 101.2, 102.1, 103.4, 104.0, 104.7, 105.5, 106.2]
    rate = [2.0, 2.2, 2.1, 2.4, 2.5, 2.7, 2.6, 2.8]
    return dates, gdp, rate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gdp-series", default="GDPC1", help="FRED series for GDP")
    ap.add_argument("--rate-series", default="FEDFUNDS", help="FRED series for interest rates")
    ap.add_argument("--out", default="reports/econ-report.json", help="Output report path")
    args = ap.parse_args()

    gdp_obs = fetch_fred_series(args.gdp_series, limit=200)
    rate_obs = fetch_fred_series(args.rate_series, limit=200)

    gdp = _clean_series(gdp_obs)
    rate = _clean_series(rate_obs)

    if not gdp or not rate:
        dates, gdp_vals, rate_vals = _sample_data()
    else:
        # align by date intersection
        gdp_map = {d: v for d, v in gdp}
        rate_map = {d: v for d, v in rate}
        shared_dates = [d for d in gdp_map.keys() if d in rate_map]
        shared_dates.sort()
        dates = shared_dates
        gdp_vals = [gdp_map[d] for d in dates]
        rate_vals = [rate_map[d] for d in dates]

    gdp_growth = _growth(gdp_vals)
    rate_delta = np.diff(rate_vals) if len(rate_vals) > 1 else np.array([])
    min_len = min(len(gdp_growth), len(rate_delta))
    if min_len > 0:
        corr = float(np.corrcoef(gdp_growth[:min_len], rate_delta[:min_len])[0, 1])
    else:
        corr = 0.0

    report = {
        "series": {
            "gdp_series": args.gdp_series,
            "rate_series": args.rate_series,
        },
        "observations": len(dates),
        "gdp_growth_avg": float(np.mean(gdp_growth)) if gdp_growth else 0.0,
        "rate_delta_avg": float(np.mean(rate_delta)) if len(rate_delta) else 0.0,
        "corr_gdp_growth_rate_delta": corr,
        "notes": "If API calls fail, the report falls back to sample data.",
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote report to {out_path}")


if __name__ == "__main__":
    main()
