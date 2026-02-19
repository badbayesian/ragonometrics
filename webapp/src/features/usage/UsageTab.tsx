import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import css from "./UsageTab.module.css";

type UsageSummary = {
  calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
};

type UsageByModelRow = {
  model?: string;
  calls?: number;
  total_tokens?: number;
};

type UsageRecentRow = {
  created_at?: string;
  model?: string;
  operation?: string;
  step?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  session_id?: string;
};

type Props = {
  onStatus: (text: string) => void;
};

function fmtNumber(value: unknown): string {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return "0";
  return num.toLocaleString();
}

export function UsageTab(props: Props) {
  const [sessionOnly, setSessionOnly] = useState(false);
  const [recentLimit, setRecentLimit] = useState(200);
  const [summary, setSummary] = useState<UsageSummary>({});
  const [byModel, setByModel] = useState<UsageByModelRow[]>([]);
  const [recent, setRecent] = useState<UsageRecentRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function refreshUsage() {
    const flag = sessionOnly ? "1" : "0";
    setLoading(true);
    setError("");
    const [sum, models, recentRows] = await Promise.all([
      api<UsageSummary>(`/api/v1/usage/summary?session_only=${flag}`),
      api<{ rows: UsageByModelRow[] }>(`/api/v1/usage/by-model?session_only=${flag}`),
      api<{ rows: UsageRecentRow[] }>(`/api/v1/usage/recent?session_only=${flag}&limit=${encodeURIComponent(String(recentLimit))}`),
    ]);

    if (!sum.ok || !models.ok || !recentRows.ok) {
      const message = sum.error?.message || models.error?.message || recentRows.error?.message || "Usage request failed.";
      setError(message);
      props.onStatus(message);
      setLoading(false);
      return;
    }

    setSummary(sum.data || {});
    setByModel(Array.isArray(models.data?.rows) ? models.data?.rows : []);
    setRecent(Array.isArray(recentRows.data?.rows) ? recentRows.data?.rows : []);
    props.onStatus("Usage refreshed");
    setLoading(false);
  }

  useEffect(() => {
    void refreshUsage();
  }, [sessionOnly]);

  const cards = useMemo(
    () => [
      { label: "Calls", value: fmtNumber(summary.calls) },
      { label: "Input Tokens", value: fmtNumber(summary.input_tokens) },
      { label: "Output Tokens", value: fmtNumber(summary.output_tokens) },
      { label: "Total Tokens", value: fmtNumber(summary.total_tokens) },
    ],
    [summary]
  );

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>Usage</h2>
          <p className={css.caption}>Aggregates are scoped to your account across sessions by default.</p>
        </div>
        <div className={css.controls}>
          <label>
            <input type="checkbox" checked={sessionOnly} onChange={(e) => setSessionOnly(e.target.checked)} /> Current session only
          </label>
          <label>
            Recent limit{" "}
            <input
              className={css.input}
              type="number"
              min={50}
              max={1000}
              step={50}
              value={recentLimit}
              onChange={(e) => setRecentLimit(Number(e.target.value || 200))}
            />
          </label>
          <button className={css.button} onClick={() => void refreshUsage()} disabled={loading}>
            {loading ? <Spinner label="Loading" small /> : "Refresh"}
          </button>
        </div>
      </header>

      {error && <div className={css.error}>{error}</div>}

      {loading && <Spinner label="Refreshing usage..." />}

      <div className={css.metrics}>
        {cards.map((item) => (
          <article key={item.label} className={css.metricCard}>
            <div className={css.metricLabel}>{item.label}</div>
            <div className={css.metricValue}>{item.value}</div>
          </article>
        ))}
      </div>

      <div className={css.tables}>
        <section className={css.tableCard}>
          <h3>By Model</h3>
          {byModel.length === 0 ? (
            <p className={css.empty}>No rows for this scope.</p>
          ) : (
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Total Tokens</th>
                </tr>
              </thead>
              <tbody>
                {byModel.map((row, idx) => (
                  <tr key={`${row.model || "model"}-${idx}`}>
                    <td>{row.model || "n/a"}</td>
                    <td>{fmtNumber(row.calls)}</td>
                    <td>{fmtNumber(row.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className={css.tableCard}>
          <h3>Recent Usage</h3>
          {recent.length === 0 ? (
            <p className={css.empty}>No recent rows for this scope.</p>
          ) : (
            <table className={css.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Model</th>
                  <th>Operation</th>
                  <th>Total Tokens</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row, idx) => (
                  <tr key={`${row.created_at || "time"}-${idx}`}>
                    <td>{String(row.created_at || "").replace("T", " ").slice(0, 19)}</td>
                    <td>{row.model || "n/a"}</td>
                    <td>{row.operation || row.step || "n/a"}</td>
                    <td>{fmtNumber(row.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </section>
  );
}
