import { useEffect, useMemo, useState } from "react";
import { api, downloadBlob } from "../../shared/api";
import { CompareRun, PaperRow, SimilarPaperSuggestion } from "../../shared/types";
import { Spinner } from "../../shared/Spinner";
import css from "./CompareTab.module.css";

type Props = {
  csrfToken: string;
  paperId: string;
  model: string;
  onStatus: (text: string) => void;
};

type SuggestionsPayload = {
  seed_paper_id: string;
  seed_paper?: PaperRow;
  count: number;
  rows: SimilarPaperSuggestion[];
};

type RunsPayload = {
  rows: CompareRun[];
  count: number;
  limit: number;
  offset: number;
};

function parseQuestions(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of String(text || "").split("\n")) {
    const trimmed = line.trim().replace(/\s+/g, " ");
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
  }
  return out;
}

export function CompareTab(props: Props) {
  const [suggestions, setSuggestions] = useState<SimilarPaperSuggestion[]>([]);
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);
  const [questionText, setQuestionText] = useState(
    "What is the main research question?\nWhat identification strategy is used?\nWhat are key limitations?"
  );
  const [runs, setRuns] = useState<CompareRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [activeRun, setActiveRun] = useState<CompareRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function loadSuggestions() {
    if (!props.paperId) return;
    const out = await api<SuggestionsPayload>(
      `/api/v1/compare/similar-papers?paper_id=${encodeURIComponent(props.paperId)}&limit=20`
    );
    if (!out.ok || !out.data) {
      setSuggestions([]);
      return;
    }
    setSuggestions(out.data.rows || []);
  }

  async function loadRuns() {
    const out = await api<RunsPayload>("/api/v1/compare/runs?limit=50&offset=0");
    if (!out.ok || !out.data) {
      setRuns([]);
      return;
    }
    setRuns(out.data.rows || []);
  }

  async function loadRun(comparisonId: string) {
    if (!comparisonId) return;
    setBusy(true);
    const out = await api<CompareRun>(`/api/v1/compare/runs/${encodeURIComponent(comparisonId)}`);
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Failed to load comparison run.";
      setError(message);
      props.onStatus(message);
      setBusy(false);
      return;
    }
    setActiveRun(out.data);
    setSelectedRunId(String(out.data.comparison_id || ""));
    setBusy(false);
  }

  useEffect(() => {
    if (!props.paperId) return;
    setSelectedPaperIds([props.paperId]);
    setError("");
    void loadSuggestions();
    void loadRuns();
  }, [props.paperId]);

  const selectedPaperCount = selectedPaperIds.length;
  const parsedQuestions = useMemo(() => parseQuestions(questionText), [questionText]);

  function togglePaper(paperId: string) {
    setSelectedPaperIds((prev) => {
      const has = prev.includes(paperId);
      if (has) return prev.filter((item) => item !== paperId);
      if (prev.length >= 10) return prev;
      return [...prev, paperId];
    });
  }

  async function buildMatrix() {
    setError("");
    if (selectedPaperIds.length < 2) {
      setError("Select at least 2 papers.");
      return;
    }
    if (parsedQuestions.length < 1) {
      setError("Add at least one question.");
      return;
    }
    setBusy(true);
    props.onStatus("Building comparison matrix from cache...");
    const out = await api<CompareRun>(
      "/api/v1/compare/runs",
      {
        method: "POST",
        body: JSON.stringify({
          seed_paper_id: props.paperId,
          paper_ids: selectedPaperIds,
          questions: parsedQuestions,
          model: props.model || undefined,
        }),
      },
      props.csrfToken
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Failed to build comparison matrix.";
      setError(message);
      props.onStatus(message);
      setBusy(false);
      return;
    }
    setActiveRun(out.data);
    setSelectedRunId(String(out.data.comparison_id || ""));
    await loadRuns();
    props.onStatus("Comparison matrix ready.");
    setBusy(false);
  }

  async function fillMissing() {
    if (!selectedRunId) return;
    setBusy(true);
    props.onStatus("Filling missing comparison cells...");
    const out = await api<CompareRun>(
      `/api/v1/compare/runs/${encodeURIComponent(selectedRunId)}/fill-missing`,
      { method: "POST", body: JSON.stringify({}) },
      props.csrfToken
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Fill missing failed.";
      setError(message);
      props.onStatus(message);
      setBusy(false);
      return;
    }
    setActiveRun(out.data);
    await loadRuns();
    props.onStatus("Fill missing completed.");
    setBusy(false);
  }

  async function exportRun(format: "json" | "csv") {
    if (!selectedRunId) return;
    setBusy(true);
    const out = await api<{ format: string; filename: string; payload?: Record<string, unknown>; content?: string }>(
      `/api/v1/compare/runs/${encodeURIComponent(selectedRunId)}/export`,
      { method: "POST", body: JSON.stringify({ format }) },
      props.csrfToken
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Export failed.";
      setError(message);
      props.onStatus(message);
      setBusy(false);
      return;
    }
    if (format === "json") {
      const blob = new Blob([JSON.stringify(out.data.payload || {}, null, 2)], { type: "application/json" });
      downloadBlob(blob, out.data.filename || "comparison.json");
    } else {
      const blob = new Blob([String(out.data.content || "")], { type: "text/csv;charset=utf-8" });
      downloadBlob(blob, out.data.filename || "comparison.csv");
    }
    props.onStatus(`Exported ${format.toUpperCase()}.`);
    setBusy(false);
  }

  const paperTitleById = useMemo(() => {
    const out: Record<string, string> = {};
    for (const paper of activeRun?.papers || []) {
      out[String(paper.paper_id || "")] = String(paper.display_title || paper.title || paper.name || paper.paper_id || "");
    }
    return out;
  }, [activeRun]);

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>Compare Papers</h2>
          <p className={css.caption}>Build a cache-first cross-paper matrix for custom questions.</p>
        </div>
        <div className={css.headerActions}>
          <button className={css.button} onClick={() => void loadSuggestions()} disabled={busy}>
            Refresh Suggestions
          </button>
          <button className={css.button} onClick={() => void loadRuns()} disabled={busy}>
            Refresh Runs
          </button>
        </div>
      </header>

      {error && <div className={css.error}>{error}</div>}

      <div className={css.panel}>
        <h3>Topic-Assisted Paper Selection</h3>
        <p className={css.caption}>Selected: {selectedPaperCount}/10 papers</p>
        <div className={css.selectionGrid}>
          <label className={css.checkboxRow}>
            <input
              type="checkbox"
              checked={selectedPaperIds.includes(props.paperId)}
              onChange={() => togglePaper(props.paperId)}
              disabled={busy}
            />
            Seed paper
          </label>
          {suggestions.map((item) => (
            <label key={item.paper_id} className={css.checkboxRow}>
              <input
                type="checkbox"
                checked={selectedPaperIds.includes(item.paper_id)}
                onChange={() => togglePaper(item.paper_id)}
                disabled={busy || (!selectedPaperIds.includes(item.paper_id) && selectedPaperIds.length >= 10)}
              />
              <span>
                {item.title} <small>(score {Number(item.score || 0).toFixed(3)})</small>
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className={css.panel}>
        <h3>Custom Questions</h3>
        <textarea
          className={css.textarea}
          rows={5}
          value={questionText}
          onChange={(e) => setQuestionText(e.target.value)}
          disabled={busy}
          placeholder="One question per line"
        />
        <div className={css.row}>
          <small>{parsedQuestions.length} normalized question(s)</small>
          <button className={css.buttonPrimary} onClick={() => void buildMatrix()} disabled={busy}>
            {busy ? <Spinner label="Building" small /> : "Build Matrix (Cache Only)"}
          </button>
        </div>
      </div>

      <div className={css.panel}>
        <h3>Saved Runs</h3>
        <div className={css.row}>
          <select
            className={css.select}
            value={selectedRunId}
            onChange={(e) => {
              const value = e.target.value;
              setSelectedRunId(value);
              void loadRun(value);
            }}
          >
            <option value="">Select comparison run</option>
            {runs.map((run) => (
              <option key={run.comparison_id} value={run.comparison_id}>
                {run.name} ({run.comparison_id.slice(0, 8)})
              </option>
            ))}
          </select>
          <button className={css.button} onClick={() => void fillMissing()} disabled={busy || !selectedRunId}>
            Fill Missing
          </button>
          <button className={css.button} onClick={() => void exportRun("json")} disabled={busy || !selectedRunId}>
            Export JSON
          </button>
          <button className={css.button} onClick={() => void exportRun("csv")} disabled={busy || !selectedRunId}>
            Export CSV
          </button>
        </div>
      </div>

      {!activeRun ? (
        <div className={css.empty}>Build or select a comparison run to view matrix results.</div>
      ) : (
        <div className={css.panel}>
          <h3>{activeRun.name}</h3>
          <p className={css.caption}>
            status={activeRun.status}; cells total={activeRun.summary?.total_cells || 0}, cached={activeRun.summary?.cached_cells || 0},
            generated={activeRun.summary?.generated_cells || 0}, missing={activeRun.summary?.missing_cells || 0},
            failed={activeRun.summary?.failed_cells || 0}
          </p>
          <div className={css.matrixWrap}>
            <table className={css.matrix}>
              <thead>
                <tr>
                  <th>Question</th>
                  {(activeRun.papers || []).map((paper) => (
                    <th key={paper.paper_id}>{paper.display_title || paper.title || paper.name || paper.paper_id}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(activeRun.matrix || []).map((row) => (
                  <tr key={row.question_id}>
                    <td className={css.questionCol}>
                      <strong>{row.question_id}</strong>
                      <div>{row.question_text}</div>
                    </td>
                    {row.cells.map((cell) => (
                      <td key={`${row.question_id}-${cell.paper_id}`}>
                        <div className={css.cell}>
                          <span className={`${css.badge} ${css[`status_${cell.cell_status}`] || ""}`}>{cell.cell_status}</span>
                          <details>
                            <summary>{paperTitleById[cell.paper_id] || cell.paper_id}</summary>
                            <p>{cell.answer || cell.error_text || "No answer yet."}</p>
                            {cell.structured_fields && (
                              <pre className={css.fields}>{JSON.stringify(cell.structured_fields, null, 2)}</pre>
                            )}
                          </details>
                        </div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
