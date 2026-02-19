import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import { WorkflowInternalRow, WorkflowRunRow, WorkflowStepRow } from "../../shared/types";
import css from "./WorkflowCacheTab.module.css";

type WorkflowRunsPayload = {
  paper_id: string;
  runs: WorkflowRunRow[];
  count: number;
  selected_run_id?: string;
};

type WorkflowStepsPayload = {
  paper_id: string;
  run?: WorkflowRunRow;
  steps: WorkflowStepRow[];
  count: number;
  internals?: WorkflowInternalRow[];
  internals_count?: number;
  include_internals?: boolean;
  usage_by_step?: Record<string, unknown>;
};

type Props = {
  paperId: string;
  onStatus: (text: string) => void;
};

const CANONICAL_ORDER = ["prep", "ingest", "enrich", "econ_data", "agentic", "index", "evaluate", "report"];

function shortTime(text: string): string {
  const value = String(text || "").trim();
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

function stepSort(a: WorkflowStepRow, b: WorkflowStepRow): number {
  const ia = CANONICAL_ORDER.indexOf(String(a.step || ""));
  const ib = CANONICAL_ORDER.indexOf(String(b.step || ""));
  const oa = ia >= 0 ? ia : 999;
  const ob = ib >= 0 ? ib : 999;
  if (oa !== ob) return oa - ob;
  return String(a.started_at || a.created_at || "").localeCompare(String(b.started_at || b.created_at || ""));
}

export function WorkflowCacheTab(props: Props) {
  const [runs, setRuns] = useState<WorkflowRunRow[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [steps, setSteps] = useState<WorkflowStepRow[]>([]);
  const [internals, setInternals] = useState<WorkflowInternalRow[]>([]);
  const [usageByStep, setUsageByStep] = useState<Record<string, unknown>>({});
  const [includeInternals, setIncludeInternals] = useState(true);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingSteps, setLoadingSteps] = useState(false);
  const [error, setError] = useState("");

  const selectedRun = useMemo(
    () => runs.find((item) => String(item.run_id || "") === String(selectedRunId || "")) || null,
    [runs, selectedRunId]
  );
  const sortedSteps = useMemo(() => [...steps].sort(stepSort), [steps]);

  async function loadRuns() {
    if (!props.paperId) return;
    setLoadingRuns(true);
    setError("");
    const out = await api<WorkflowRunsPayload>(
      `/api/v1/workflow/runs?paper_id=${encodeURIComponent(props.paperId)}&limit=100`
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Failed to load workflow runs.";
      setRuns([]);
      setSelectedRunId("");
      setSteps([]);
      setInternals([]);
      setUsageByStep({});
      setError(message);
      props.onStatus(message);
      setLoadingRuns(false);
      return;
    }
    const rows = Array.isArray(out.data.runs) ? out.data.runs : [];
    setRuns(rows);
    const nextRun = String(out.data.selected_run_id || "");
    setSelectedRunId((prev) => {
      if (prev && rows.some((item) => item.run_id === prev)) return prev;
      return nextRun || String(rows[0]?.run_id || "");
    });
    setLoadingRuns(false);
  }

  async function loadSteps(runId: string) {
    if (!props.paperId || !runId) return;
    setLoadingSteps(true);
    setError("");
    const out = await api<WorkflowStepsPayload>(
      `/api/v1/workflow/runs/${encodeURIComponent(runId)}/steps?paper_id=${encodeURIComponent(
        props.paperId
      )}&include_internals=${includeInternals ? "1" : "0"}`
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Failed to load workflow steps.";
      setSteps([]);
      setInternals([]);
      setUsageByStep({});
      setError(message);
      props.onStatus(message);
      setLoadingSteps(false);
      return;
    }
    setSteps(Array.isArray(out.data.steps) ? out.data.steps : []);
    setInternals(Array.isArray(out.data.internals) ? out.data.internals : []);
    setUsageByStep((out.data.usage_by_step as Record<string, unknown>) || {});
    props.onStatus("Workflow cache loaded.");
    setLoadingSteps(false);
  }

  useEffect(() => {
    void loadRuns();
  }, [props.paperId]);

  useEffect(() => {
    if (!selectedRunId) {
      setSteps([]);
      setInternals([]);
      setUsageByStep({});
      return;
    }
    void loadSteps(selectedRunId);
  }, [selectedRunId, includeInternals]);

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>Workflow Cache</h2>
          <p className={css.caption}>Cached workflow stages from `workflow.run_records` for the selected paper.</p>
        </div>
        <div className={css.controls}>
          <label className={css.toggle}>
            <input
              type="checkbox"
              checked={includeInternals}
              onChange={(e) => setIncludeInternals(e.target.checked)}
            />{" "}
            Include agentic internals
          </label>
          <button className={css.button} onClick={() => void loadRuns()} disabled={loadingRuns || loadingSteps}>
            {loadingRuns ? <Spinner label="Refreshing" small /> : "Refresh Runs"}
          </button>
        </div>
      </header>

      {error && <div className={css.error}>{error}</div>}

      {(loadingRuns || loadingSteps) && (
        <div className={css.spinnerWrap}>
          <Spinner label={loadingRuns ? "Loading runs..." : "Loading steps..."} />
        </div>
      )}

      <div className={css.runPicker}>
        <label>
          Cached run
          <select
            className={css.select}
            value={selectedRunId}
            onChange={(e) => setSelectedRunId(e.target.value)}
            disabled={runs.length === 0}
          >
            {runs.map((item) => (
              <option value={item.run_id} key={item.run_id}>
                {item.run_id} ({item.status || "unknown"})
              </option>
            ))}
          </select>
        </label>
      </div>

      {runs.length === 0 && !loadingRuns && <div className={css.empty}>No cached workflow runs found for this paper.</div>}

      {selectedRun && (
        <article className={css.runSummary}>
          <div>
            <strong>Run:</strong> {selectedRun.run_id}
          </div>
          <div>
            <strong>Status:</strong> {selectedRun.status || "unknown"}
          </div>
          <div>
            <strong>Matched by:</strong> {selectedRun.matched_by || "n/a"}
          </div>
          <div>
            <strong>Started:</strong> {shortTime(selectedRun.started_at)}
          </div>
          <div>
            <strong>Finished:</strong> {shortTime(selectedRun.finished_at)}
          </div>
          <div>
            <strong>Workstream:</strong> {selectedRun.workstream_id || "n/a"}
          </div>
          <div>
            <strong>Arm:</strong> {selectedRun.arm || "n/a"}
          </div>
        </article>
      )}

      {sortedSteps.length > 0 && (
        <section className={css.timeline}>
          {sortedSteps.map((step) => (
            <article className={css.stepCard} key={`${step.step}-${step.started_at}-${step.updated_at}`}>
              <div className={css.stepHeader}>
                <h3>{step.step || "unknown step"}</h3>
                <span className={`${css.badge} ${css[`status_${String(step.status || "").toLowerCase()}`] || ""}`}>
                  {step.status || "unknown"}
                </span>
              </div>
              <div className={css.stepMeta}>
                <span>started: {shortTime(step.started_at)}</span>
                <span>finished: {shortTime(step.finished_at)}</span>
                {step.reuse_source_run_id && <span>reused from: {step.reuse_source_run_id}</span>}
              </div>
              <details>
                <summary>Step output</summary>
                <pre className={css.json}>{prettyJson(step.output || {})}</pre>
              </details>
              <details>
                <summary>Step metadata</summary>
                <pre className={css.json}>{prettyJson(step.metadata || {})}</pre>
              </details>
            </article>
          ))}
        </section>
      )}

      {includeInternals && (
        <section className={css.internals}>
          <h3>Agentic Internals</h3>
          {internals.length === 0 ? (
            <p className={css.empty}>No derived internals for this run.</p>
          ) : (
            <div className={css.internalGrid}>
              {internals.map((item) => (
                <article className={css.internalCard} key={item.internal_step}>
                  <div className={css.internalHeader}>
                    <strong>{item.label || item.internal_step}</strong>
                    <span className={`${css.badge} ${css[`status_${String(item.status || "").toLowerCase()}`] || ""}`}>
                      {item.status || "unknown"}
                    </span>
                  </div>
                  <p className={css.internalDetail}>{item.detail}</p>
                  <details>
                    <summary>Summary</summary>
                    <pre className={css.json}>{prettyJson(item.summary || {})}</pre>
                  </details>
                  <details>
                    <summary>Usage</summary>
                    <pre className={css.json}>{prettyJson(item.usage || {})}</pre>
                  </details>
                  {Array.isArray(item.sample) && item.sample.length > 0 && (
                    <details>
                      <summary>Sample</summary>
                      <pre className={css.json}>{prettyJson(item.sample)}</pre>
                    </details>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      )}

      {Object.keys(usageByStep || {}).length > 0 && (
        <details>
          <summary>Usage by step</summary>
          <pre className={css.json}>{prettyJson(usageByStep)}</pre>
        </details>
      )}
    </section>
  );
}
