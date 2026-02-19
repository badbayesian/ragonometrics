import { useEffect, useMemo, useState } from "react";
import { api, downloadBlob } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import { StructuredExportBundle, StructuredQuestion } from "../../shared/types";
import css from "./StructuredTab.module.css";

type Props = {
  csrfToken: string;
  paperId: string;
  paperName: string;
  model: string;
  onStatus: (text: string) => void;
  onAskInChat: (question: string) => void;
};

type AnswersMap = Record<string, { answer?: string; model?: string; created_at?: string; source?: string }>;

function normalizeQuestionKey(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function StructuredTab(props: Props) {
  const [questions, setQuestions] = useState<StructuredQuestion[]>([]);
  const [answers, setAnswers] = useState<AnswersMap>({});
  const [cacheScope, setCacheScope] = useState<"Selected model only" | "Any model">("Selected model only");
  const [category, setCategory] = useState("All");
  const [textFilter, setTextFilter] = useState("");
  const [exportFormat, setExportFormat] = useState<"compact" | "full">("compact");
  const [exportScope, setExportScope] = useState<"filtered" | "all">("all");
  const [selectedQuestionId, setSelectedQuestionId] = useState("");
  const [fullBundle, setFullBundle] = useState<StructuredExportBundle | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadQuestions() {
    const out = await api<{ questions: StructuredQuestion[] }>("/api/v1/structured/questions");
    if (out.ok && out.data?.questions) {
      setQuestions(out.data.questions);
      if (!selectedQuestionId && out.data.questions.length > 0) {
        setSelectedQuestionId(out.data.questions[0].id);
      }
    }
  }

  async function loadAnswers() {
    if (!props.paperId) return;
    const modelQuery =
      cacheScope === "Selected model only" && props.model
        ? `&model=${encodeURIComponent(props.model)}`
        : "";
    const out = await api<{ answers: AnswersMap }>(
      `/api/v1/structured/answers?paper_id=${encodeURIComponent(props.paperId)}${modelQuery}`
    );
    if (out.ok && out.data?.answers) {
      setAnswers(out.data.answers);
    }
  }

  useEffect(() => {
    void loadQuestions();
  }, []);

  useEffect(() => {
    void loadAnswers();
  }, [props.paperId, props.model, cacheScope]);

  const categories = useMemo(() => ["All", ...Array.from(new Set(questions.map((q) => q.category))).sort()], [questions]);
  const filteredQuestions = useMemo(() => {
    const filter = textFilter.trim().toLowerCase();
    return questions.filter((q) => {
      if (category !== "All" && q.category !== category) return false;
      if (filter && !q.question.toLowerCase().includes(filter)) return false;
      return true;
    });
  }, [questions, category, textFilter]);
  const exportQuestions = exportScope === "all" ? questions : filteredQuestions;

  const cachedCount = filteredQuestions.filter((q) => Boolean(answers[normalizeQuestionKey(q.question)])).length;
  const selectedQuestion = questions.find((q) => q.id === selectedQuestionId) || filteredQuestions[0];
  const selectedQuestionAnswer = selectedQuestion ? answers[normalizeQuestionKey(selectedQuestion.question)] : null;

  function exportQuestionIds(target: StructuredQuestion[]): string[] {
    return target.map((q) => q.id);
  }

  async function generateMissing(questionIds: string[]) {
    if (!props.paperId) return;
    setBusy(true);
    props.onStatus("Generating missing structured answers...");
    const out = await api<{ generated_count: number; skipped_cached_count: number }>(
      "/api/v1/structured/generate-missing",
      {
        method: "POST",
        body: JSON.stringify({
          paper_id: props.paperId,
          model: props.model || undefined,
          question_ids: questionIds,
        }),
      },
      props.csrfToken
    );
    if (!out.ok) {
      props.onStatus(out.error?.message || "Generate missing failed");
      setBusy(false);
      return;
    }
    props.onStatus(
      `Generated ${out.data?.generated_count || 0}, skipped ${out.data?.skipped_cached_count || 0}`
    );
    await loadAnswers();
    setBusy(false);
  }

  async function fetchExportBundle(format: "compact" | "full", questionIds: string[]) {
    const out = await api<StructuredExportBundle>(
      "/api/v1/structured/export",
      {
        method: "POST",
        body: JSON.stringify({
          paper_id: props.paperId,
          model: props.model || undefined,
          cache_scope: cacheScope,
          export_format: format,
          output: "json",
          question_ids: questionIds,
        }),
      },
      props.csrfToken
    );
    if (!out.ok || !out.data) {
      props.onStatus(out.error?.message || "Export failed");
      return null;
    }
    return out.data;
  }

  async function exportJson(format: "compact" | "full", questionIds: string[]) {
    setBusy(true);
    const bundle = await fetchExportBundle(format, questionIds);
    if (!bundle) {
      setBusy(false);
      return;
    }
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    downloadBlob(blob, `structured-workstream-${props.paperId}-${format}.json`);
    if (format === "full") setFullBundle(bundle);
    setBusy(false);
  }

  async function exportPdf(format: "compact" | "full", questionIds: string[]) {
    setBusy(true);
    const res = await fetch("/api/v1/structured/export", {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": props.csrfToken,
      },
      body: JSON.stringify({
        paper_id: props.paperId,
        model: props.model || undefined,
        cache_scope: cacheScope,
        export_format: format,
        output: "pdf",
        question_ids: questionIds,
      }),
    });
    if (!res.ok) {
      const maybeJson = res.headers.get("content-type")?.includes("application/json");
      const payload = maybeJson ? await res.json() : null;
      props.onStatus(
        payload?.error?.message || `PDF export failed (${res.status}).`
      );
      setBusy(false);
      return;
    }
    const blob = await res.blob();
    downloadBlob(blob, `structured-workstream-${props.paperId}-${format}.pdf`);
    setBusy(false);
  }

  async function regenerateMissingFullFields(questionIds: string[]) {
    setBusy(true);
    const bundle = await fetchExportBundle("full", questionIds);
    if (!bundle) {
      setBusy(false);
      return;
    }
    setFullBundle(bundle);
    const missing = (bundle.questions || []).filter((row) => {
      const fields = (row.structured_fields || {}) as Record<string, unknown>;
      const anchors = fields.citation_anchors;
      const hasAnchors = Array.isArray(anchors) && anchors.length > 0;
      const hasConfidence = fields.confidence_score !== undefined && fields.confidence_score !== null;
      const hasRetrievalMethod = Boolean(String(fields.retrieval_method || "").trim());
      return !(hasAnchors && hasConfidence && hasRetrievalMethod);
    });
    if (missing.length === 0) {
      props.onStatus("No missing full fields detected in selected scope.");
      setBusy(false);
      return;
    }
    await generateMissing(missing.map((row) => row.id));
    props.onStatus(`Regenerated ${missing.length} rows missing full structured fields.`);
    setBusy(false);
  }

  const selectedIds = exportQuestionIds(exportQuestions);

  return (
    <section className={css.card}>
      <h2>Structured Workstream</h2>
      <p>Selected paper: {props.paperName || "n/a"}</p>
      <div className={css.grid}>
        <div className={css.metric}>
          <div className={css.metricLabel}>Filtered Questions</div>
          <div className={css.metricValue}>{filteredQuestions.length}</div>
        </div>
        <div className={css.metric}>
          <div className={css.metricLabel}>Cached Answers</div>
          <div className={css.metricValue}>{cachedCount}</div>
        </div>
        <div className={css.metric}>
          <div className={css.metricLabel}>Uncached</div>
          <div className={css.metricValue}>{Math.max(0, filteredQuestions.length - cachedCount)}</div>
        </div>
      </div>

      <div className={css.row}>
        <label>
          Cache scope{" "}
          <select
            className={css.select}
            value={cacheScope}
            onChange={(e) => setCacheScope(e.target.value as "Selected model only" | "Any model")}
          >
            <option>Selected model only</option>
            <option>Any model</option>
          </select>
        </label>
        <label>
          Category{" "}
          <select className={css.select} value={category} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Filter{" "}
          <input className={css.input} value={textFilter} onChange={(e) => setTextFilter(e.target.value)} placeholder="Filter question text" />
        </label>
        <button className={css.button} onClick={() => void loadAnswers()}>
          {busy ? <Spinner label="Working" small /> : "Refresh Cache"}
        </button>
      </div>

      <div className={css.row}>
        <label>
          Export scope{" "}
          <select
            className={css.select}
            value={exportScope}
            onChange={(e) => setExportScope(e.target.value as "filtered" | "all")}
          >
            <option value="filtered">Filtered questions</option>
            <option value="all">All structured questions</option>
          </select>
        </label>
        <label>
          Export format{" "}
          <select
            className={css.select}
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as "compact" | "full")}
          >
            <option value="compact">Compact</option>
            <option value="full">Full</option>
          </select>
        </label>
        <button className={`${css.button} ${css.buttonPrimary}`} onClick={() => void generateMissing(selectedIds)} disabled={busy}>
          {busy ? <Spinner label="Generating" small /> : "Generate Missing (Export Scope)"}
        </button>
        <button className={css.button} onClick={() => void regenerateMissingFullFields(selectedIds)} disabled={busy}>
          {busy ? <Spinner label="Working" small /> : "Regenerate Missing Full Fields"}
        </button>
      </div>

      <div className={css.row}>
        <button className={css.button} onClick={() => void exportJson(exportFormat, selectedIds)} disabled={busy}>
          {busy ? <Spinner label="Exporting" small /> : `Export ${exportFormat === "full" ? "Full" : "Compact"} JSON`}
        </button>
        <button className={css.button} onClick={() => void exportPdf(exportFormat, selectedIds)} disabled={busy}>
          {busy ? <Spinner label="Exporting" small /> : `Export ${exportFormat === "full" ? "Full" : "Compact"} PDF`}
        </button>
      </div>

      <div className={css.tableWrap}>
        <table className={css.table}>
          <thead>
            <tr>
              <th>ID</th>
              <th>Category</th>
              <th>Question</th>
              <th>Cached</th>
            </tr>
          </thead>
          <tbody>
            {filteredQuestions.map((q) => {
              const hit = answers[normalizeQuestionKey(q.question)];
              return (
                <tr key={q.id} onClick={() => setSelectedQuestionId(q.id)}>
                  <td>{q.id}</td>
                  <td>{q.category}</td>
                  <td>{q.question}</td>
                  <td>{hit ? "yes" : "no"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selectedQuestion && (
        <div className={css.detail}>
          <strong>
            {selectedQuestion.id}: {selectedQuestion.question}
          </strong>
          <div className={css.row}>
            <button className={css.button} onClick={() => props.onAskInChat(selectedQuestion.question)}>
              Ask This in Chat
            </button>
            {!selectedQuestionAnswer && (
              <button
                className={`${css.button} ${css.buttonPrimary}`}
                onClick={() => void generateMissing([selectedQuestion.id])}
              >
                Generate and Cache Answer
              </button>
            )}
          </div>
          {selectedQuestionAnswer ? (
            <>
              <small>
                source={selectedQuestionAnswer.source || ""}; model={selectedQuestionAnswer.model || ""}; cached_at=
                {selectedQuestionAnswer.created_at || ""}
              </small>
              <p className={css.answer}>{selectedQuestionAnswer.answer || ""}</p>
            </>
          ) : (
            <p>No cached answer for this question with current scope.</p>
          )}
        </div>
      )}

      {fullBundle && (
        <details>
          <summary>Latest Full Export Summary</summary>
          <pre>{JSON.stringify(fullBundle.full_summary || fullBundle.summary, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
