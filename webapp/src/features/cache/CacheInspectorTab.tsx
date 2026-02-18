import { useMemo, useState } from "react";
import { api } from "../../shared/api";
import { ChatCacheInspectPayload, StructuredCacheInspectPayload } from "../../shared/types";
import { Spinner } from "../../shared/Spinner";
import css from "./CacheInspectorTab.module.css";

type Props = {
  paperId: string;
  model: string;
  onStatus: (text: string) => void;
};

export function CacheInspectorTab(props: Props) {
  const [question, setQuestion] = useState("What is the main research question of this paper?");
  const [chatInspect, setChatInspect] = useState<ChatCacheInspectPayload | null>(null);
  const [structuredInspect, setStructuredInspect] = useState<StructuredCacheInspectPayload | null>(null);
  const [loadingChat, setLoadingChat] = useState(false);
  const [loadingStructured, setLoadingStructured] = useState(false);
  const [error, setError] = useState("");

  const chatLayerLabel = useMemo(() => {
    if (!chatInspect) return "";
    if (chatInspect.selected_layer === "strict") return "Strict hit";
    if (chatInspect.selected_layer === "fallback") return "Normalized fallback hit";
    return `Miss (${chatInspect.cache_miss_reason || "strict_and_normalized_miss"})`;
  }, [chatInspect]);

  async function inspectChat() {
    if (!props.paperId || !question.trim()) return;
    setLoadingChat(true);
    setError("");
    const query = new URLSearchParams({
      paper_id: props.paperId,
      question: question.trim(),
    });
    if (props.model.trim()) {
      query.set("model", props.model.trim());
    }
    const out = await api<ChatCacheInspectPayload>(`/api/v1/cache/chat/inspect?${query.toString()}`);
    setLoadingChat(false);
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Chat cache inspection failed.";
      setError(message);
      props.onStatus(message);
      return;
    }
    setChatInspect(out.data);
    props.onStatus("Chat cache inspection loaded.");
  }

  async function inspectStructured() {
    if (!props.paperId) return;
    setLoadingStructured(true);
    setError("");
    const query = new URLSearchParams({ paper_id: props.paperId });
    if (props.model.trim()) {
      query.set("model", props.model.trim());
    }
    const out = await api<StructuredCacheInspectPayload>(`/api/v1/cache/structured/inspect?${query.toString()}`);
    setLoadingStructured(false);
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Structured cache inspection failed.";
      setError(message);
      props.onStatus(message);
      return;
    }
    setStructuredInspect(out.data);
    props.onStatus("Structured cache inspection loaded.");
  }

  return (
    <section className={css.card}>
      <header>
        <h2>Cache Inspector</h2>
        <p className={css.caption}>Inspect strict/fallback cache behavior and structured coverage for the selected paper.</p>
      </header>

      {error && <div className={css.error}>{error}</div>}

      <div className={css.row}>
        <label className={css.label}>
          Chat question
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} className={css.textarea} />
        </label>
      </div>

      <div className={css.actions}>
        <button className={css.button} onClick={() => void inspectChat()} disabled={loadingChat || !question.trim()}>
          {loadingChat ? <Spinner label="Inspecting..." small /> : "Inspect Chat Cache"}
        </button>
        <button className={css.button} onClick={() => void inspectStructured()} disabled={loadingStructured}>
          {loadingStructured ? <Spinner label="Inspecting..." small /> : "Inspect Structured Cache"}
        </button>
      </div>

      {chatInspect && (
        <section className={css.block}>
          <h3>Chat Cache</h3>
          <div className={css.kvGrid}>
            <div>
              <strong>Result</strong>
              <div>{chatLayerLabel}</div>
            </div>
            <div>
              <strong>Model</strong>
              <div>{chatInspect.model || "n/a"}</div>
            </div>
            <div>
              <strong>Strict hit</strong>
              <div>{chatInspect.strict_hit ? "yes" : "no"}</div>
            </div>
            <div>
              <strong>Fallback hit</strong>
              <div>{chatInspect.fallback_hit ? "yes" : "no"}</div>
            </div>
          </div>
          <details className={css.details}>
            <summary>Cache internals</summary>
            <pre>{JSON.stringify(chatInspect, null, 2)}</pre>
          </details>
        </section>
      )}

      {structuredInspect && (
        <section className={css.block}>
          <h3>Structured Cache</h3>
          <div className={css.kvGrid}>
            <div>
              <strong>Coverage</strong>
              <div>{Math.round((structuredInspect.coverage_ratio || 0) * 1000) / 10}%</div>
            </div>
            <div>
              <strong>Cached</strong>
              <div>
                {structuredInspect.cached_questions}/{structuredInspect.total_questions}
              </div>
            </div>
            <div>
              <strong>Missing</strong>
              <div>{structuredInspect.missing_questions}</div>
            </div>
            <div>
              <strong>Model</strong>
              <div>{structuredInspect.model || "any"}</div>
            </div>
          </div>
          <details className={css.details}>
            <summary>Missing question IDs</summary>
            <div>{structuredInspect.missing_question_ids.join(", ") || "None"}</div>
          </details>
        </section>
      )}
    </section>
  );
}

