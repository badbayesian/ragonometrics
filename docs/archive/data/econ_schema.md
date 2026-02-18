# Economics Data Schema

This schema documents how to persist time-series data and model outputs for
financial/econometric workflows.

Time Series Table (Postgres)
----------------------------
```
CREATE TABLE IF NOT EXISTS econ_timeseries (
    series_id TEXT,
    source TEXT,
    date DATE,
    value DOUBLE PRECISION,
    unit TEXT,
    created_at TIMESTAMP,
    PRIMARY KEY (series_id, date)
);
```

High-Frequency Extension
------------------------
For tick or intraday data, use a timestamp-based schema:
```
CREATE TABLE IF NOT EXISTS econ_ticks (
    series_id TEXT,
    source TEXT,
    ts TIMESTAMPTZ,
    value DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    created_at TIMESTAMPTZ,
    PRIMARY KEY (series_id, ts)
);
```

Model Report Table (Postgres)
-----------------------------
```
CREATE TABLE IF NOT EXISTS econ_model_reports (
    report_id TEXT PRIMARY KEY,
    model_name TEXT,
    dataset_id TEXT,
    metrics_json TEXT,
    params_json TEXT,
    created_at TIMESTAMP
);
```

Sample Report (JSON)
--------------------
```json
{
  "series": {
    "gdp_series": "GDPC1",
    "rate_series": "FEDFUNDS"
  },
  "observations": 180,
  "gdp_growth_avg": 0.0062,
  "rate_delta_avg": 0.0011,
  "corr_gdp_growth_rate_delta": -0.21,
  "notes": "Computed from FRED data with a log-growth transform."
}
```

Related Code
------------
- [`ragonometrics/integrations/econ_data.py`](https://github.com/badbayesian/ragonometrics/blob/main/ragonometrics/integrations/econ_data.py)
- [`tools/econ_workflow.py`](https://github.com/badbayesian/ragonometrics/blob/main/tools/econ_workflow.py)
