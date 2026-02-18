import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../shared/api";
import { ChatHistoryItem, ChatSuggestionPayload, CitationChunk } from "../../shared/types";
import { Spinner } from "../../shared/Spinner";
import robotAvatar from "../../assets/robot-avatar.svg";
import css from "./ChatTab.module.css";

type ChatTurnData = {
  answer?: string;
  citations?: CitationChunk[];
  retrieval_stats?: Record<string, unknown>;
  cache_hit?: boolean;
  cache_hit_layer?: string;
  cache_miss_reason?: string;
  model?: string;
};

type ChatHistoryResponse = {
  rows: ChatHistoryItem[];
  count: number;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  createdAt: string;
  citations?: CitationChunk[];
  retrievalStats?: Record<string, unknown>;
  cacheHit?: boolean | null;
  cacheHitLayer?: string;
  cacheMissReason?: string;
  model?: string;
  isStreaming?: boolean;
};

type Props = {
  csrfToken: string;
  paperId: string;
  paperName: string;
  paperPath: string;
  model: string;
  queuedQuestion: string;
  onQuestionConsumed: () => void;
  onStatus: (text: string) => void;
  onOpenViewer: (payload: { page?: number | null; terms?: string[]; excerpt?: string }) => void;
};

function nowIso(): string {
  return new Date().toISOString();
}

function shortTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function historyToMessages(rows: ChatHistoryItem[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (let idx = 0; idx < rows.length; idx += 1) {
    const row = rows[idx] || {};
    const created = String(row.created_at || nowIso());
    const key = String(row.created_at || `${idx}`);
    out.push({
      id: `u-${key}-${idx}`,
      role: "user",
      text: String(row.query || ""),
      createdAt: created,
    });
    out.push({
      id: `a-${key}-${idx}`,
      role: "assistant",
      text: String(row.answer || ""),
      createdAt: created,
      citations: Array.isArray(row.citations) ? row.citations : [],
      retrievalStats: (row.retrieval_stats as Record<string, unknown>) || {},
      cacheHit: typeof row.cache_hit === "boolean" ? row.cache_hit : null,
      cacheHitLayer: String(row.cache_hit_layer || ""),
      cacheMissReason: String(row.cache_miss_reason || ""),
      model: row.model,
    });
  }
  return out;
}

function messagesToHistory(messages: ChatMessage[], paperPath: string): ChatHistoryItem[] {
  const out: ChatHistoryItem[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const current = messages[i];
    if (current.role !== "user") continue;
    const next = messages[i + 1];
    if (!next || next.role !== "assistant") continue;
    if (!current.text.trim() || !next.text.trim()) continue;
    out.push({ query: current.text, answer: next.text, paper_path: paperPath });
  }
  return out.slice(-8);
}

export function ChatTab(props: Props) {
  const [variationMode, setVariationMode] = useState(false);
  const [question, setQuestion] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isAsking, setIsAsking] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages]);

  useEffect(() => {
    if (!props.queuedQuestion) return;
    setQuestion(props.queuedQuestion);
    props.onQuestionConsumed();
  }, [props.queuedQuestion, props.onQuestionConsumed]);

  useEffect(() => {
    async function bootstrap() {
      if (!props.paperId) return;
      setError("");
      const [historyOut, suggestionsOut] = await Promise.all([
        api<ChatHistoryResponse>(`/api/v1/chat/history?paper_id=${encodeURIComponent(props.paperId)}&limit=80`),
        api<ChatSuggestionPayload>(`/api/v1/chat/suggestions?paper_id=${encodeURIComponent(props.paperId)}`),
      ]);
      if (historyOut.ok && historyOut.data?.rows) {
        setMessages(historyToMessages(historyOut.data.rows));
      } else {
        setMessages([]);
      }
      if (suggestionsOut.ok && Array.isArray(suggestionsOut.data?.questions)) {
        setSuggestions(suggestionsOut.data?.questions || []);
      } else {
        setSuggestions([]);
      }
      if (!historyOut.ok && !suggestionsOut.ok) {
        const message = historyOut.error?.message || suggestionsOut.error?.message || "Unable to load chat context.";
        setError(message);
        props.onStatus(message);
      }
    }
    void bootstrap();
  }, [props.paperId]);

  const historyPayload = useMemo(() => messagesToHistory(messages, props.paperPath), [messages, props.paperPath]);

  function addPendingTurn(userText: string): { userId: string; assistantId: string; createdAt: string } {
    const createdAt = nowIso();
    const userId = `u-live-${createdAt}-${Math.random().toString(16).slice(2)}`;
    const assistantId = `a-live-${createdAt}-${Math.random().toString(16).slice(2)}`;
    const userMsg: ChatMessage = { id: userId, role: "user", text: userText, createdAt };
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      createdAt,
      isStreaming: true,
      citations: [],
      retrievalStats: {},
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    return { userId, assistantId, createdAt };
  }

  function updateAssistantMessage(assistantId: string, patch: Partial<ChatMessage>) {
    setMessages((prev) =>
      prev.map((row) => {
        if (row.id !== assistantId) return row;
        return { ...row, ...patch };
      })
    );
  }

  function citationTerms(citation: CitationChunk): string[] {
    const text = String(citation.text || "").trim();
    if (!text) return [];
    return text
      .split(/\s+/)
      .map((item) => item.replace(/[^a-zA-Z0-9]/g, "").trim())
      .filter((item) => item.length >= 4)
      .slice(0, 6);
  }

  async function askChat(stream: boolean) {
    const prompt = question.trim();
    if (!props.paperId || !prompt || isAsking) return;
    setQuestion("");
    setIsAsking(true);
    setError("");
    props.onStatus(stream ? "Streaming answer..." : "Fetching answer...");

    const { assistantId } = addPendingTurn(prompt);
    const payload = {
      paper_id: props.paperId,
      question: prompt,
      model: props.model || undefined,
      history: historyPayload,
      variation_mode: variationMode,
    };

    if (!stream) {
      const out = await api<ChatTurnData>("/api/v1/chat/turn", { method: "POST", body: JSON.stringify(payload) }, props.csrfToken);
      if (!out.ok || !out.data) {
        const message = out.error?.message || "Chat failed.";
        setError(message);
        updateAssistantMessage(assistantId, { text: message, isStreaming: false });
        props.onStatus(message);
        setIsAsking(false);
        return;
      }
      updateAssistantMessage(assistantId, {
        text: String(out.data.answer || ""),
        citations: Array.isArray(out.data.citations) ? out.data.citations : [],
        retrievalStats: (out.data.retrieval_stats as Record<string, unknown>) || {},
        cacheHit: typeof out.data.cache_hit === "boolean" ? out.data.cache_hit : null,
        cacheHitLayer: String(out.data.cache_hit_layer || ""),
        cacheMissReason: String(out.data.cache_miss_reason || ""),
        model: String(out.data.model || props.model || ""),
        isStreaming: false,
      });
      props.onStatus("Ready");
      setIsAsking(false);
      return;
    }

    try {
      const res = await fetch("/api/v1/chat/turn-stream", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": props.csrfToken },
        body: JSON.stringify(payload),
      });
      if (!res.ok || !res.body) {
        const bodyPreview = ((await res.text()) || "").replace(/\s+/g, " ").trim().slice(0, 240);
        const message = `Stream failed (${res.status})${bodyPreview ? `: ${bodyPreview}` : ""}`;
        setError(message);
        updateAssistantMessage(assistantId, { text: message, isStreaming: false });
        props.onStatus(message);
        setIsAsking(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalAnswer = "";
      let finalCitations: CitationChunk[] = [];
      let finalStats: Record<string, unknown> = {};
      let finalModel = "";
      let finalCacheHit: boolean | null = null;
      let finalCacheHitLayer = "";
      let finalCacheMissReason = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const parsed = JSON.parse(line) as Record<string, unknown>;
            const event = String(parsed.event || "");
            if (event === "delta") {
              finalAnswer = String(parsed.text || finalAnswer || "");
              updateAssistantMessage(assistantId, { text: finalAnswer, isStreaming: true });
            } else if (event === "done") {
              finalAnswer = String(parsed.answer || finalAnswer || "");
              finalCitations = Array.isArray(parsed.citations) ? (parsed.citations as CitationChunk[]) : finalCitations;
              finalStats = (parsed.retrieval_stats as Record<string, unknown>) || finalStats;
              finalModel = String(parsed.model || finalModel || props.model || "");
              finalCacheHit = typeof parsed.cache_hit === "boolean" ? (parsed.cache_hit as boolean) : finalCacheHit;
              finalCacheHitLayer = String(parsed.cache_hit_layer || finalCacheHitLayer || "");
              finalCacheMissReason = String(parsed.cache_miss_reason || finalCacheMissReason || "");
              updateAssistantMessage(assistantId, {
                text: finalAnswer,
                citations: finalCitations,
                retrievalStats: finalStats,
                model: finalModel,
                cacheHit: finalCacheHit,
                cacheHitLayer: finalCacheHitLayer,
                cacheMissReason: finalCacheMissReason,
                isStreaming: false,
              });
            } else if (event === "error") {
              const message = String(parsed.message || parsed.code || "Chat failed");
              setError(message);
              updateAssistantMessage(assistantId, { text: message, isStreaming: false });
              props.onStatus(message);
            }
          } catch {
            // Ignore malformed stream row.
          }
        }
      }
      props.onStatus("Ready");
    } catch (exc) {
      const message = `Stream request failed: ${String(exc || "unknown error")}`;
      setError(message);
      updateAssistantMessage(assistantId, { text: message, isStreaming: false });
      props.onStatus(message);
    }

    setIsAsking(false);
  }

  async function clearHistory() {
    if (!props.paperId || isAsking) return;
    setError("");
    const out = await api<{ deleted_count: number }>(
      `/api/v1/chat/history?paper_id=${encodeURIComponent(props.paperId)}`,
      { method: "DELETE" },
      props.csrfToken
    );
    if (!out.ok) {
      const message = out.error?.message || "Failed to clear chat history.";
      setError(message);
      props.onStatus(message);
      return;
    }
    setMessages([]);
    props.onStatus(`Cleared ${out.data?.deleted_count || 0} history rows.`);
  }

  return (
    <section className={css.card}>
      <header className={css.headerRow}>
        <div>
          <h2>Chat</h2>
          <p className={css.paperLabel}>Selected paper: {props.paperName || "n/a"}</p>
        </div>
        <div className={css.headerActions}>
          <label className={css.toggleRow}>
            <input type="checkbox" checked={variationMode} onChange={(e) => setVariationMode(e.target.checked)} />
            Variation mode
          </label>
          <button className={css.secondaryButton} onClick={() => void clearHistory()} disabled={isAsking}>
            Clear History
          </button>
        </div>
      </header>

      {suggestions.length > 0 && (
        <div className={css.suggestions}>
          {suggestions.map((item) => (
            <button key={item} className={css.suggestionChip} onClick={() => setQuestion(item)} disabled={isAsking}>
              {item}
            </button>
          ))}
        </div>
      )}

      {error && <div className={css.errorBanner}>{error}</div>}

      <div className={css.timeline}>
        {messages.length === 0 ? (
          <div className={css.emptyState}>No messages yet. Start with a suggested question or ask your own.</div>
        ) : (
          messages.map((message) => (
            <div key={message.id} className={`${css.messageRow} ${message.role === "user" ? css.userRow : css.assistantRow}`}>
              {message.role === "assistant" && (
                <img src={robotAvatar} alt="Assistant robot avatar" className={css.avatar} />
              )}
              <article className={`${css.messageBubble} ${message.role === "user" ? css.userBubble : css.assistantBubble}`}>
                <div className={css.messageMeta}>
                  <span>{message.role === "user" ? "You" : "Assistant"}</span>
                  <span>{shortTime(message.createdAt)}</span>
                </div>
                <p className={css.messageText}>{message.text || (message.isStreaming ? "..." : "")}</p>
                {message.role === "assistant" && (
                  <div className={css.answerMeta}>
                    {typeof message.cacheHit === "boolean" && (
                      <span className={css.metaBadge}>{message.cacheHit ? "Cache hit" : "Fresh"}</span>
                    )}
                    {message.cacheHitLayer && <span className={css.metaBadge}>layer={message.cacheHitLayer}</span>}
                    {message.model && <span className={css.metaBadge}>{message.model}</span>}
                    {message.isStreaming && <span className={css.streaming}>Streaming...</span>}
                  </div>
                )}
                {message.role === "assistant" && (
                  <details className={css.details}>
                    <summary>Evidence</summary>
                    {Array.isArray(message.citations) && message.citations.length > 0 ? (
                      <ul className={css.citationList}>
                        {message.citations.slice(0, 3).map((c, idx) => (
                          <li key={`${message.id}-citation-${idx}`}>
                            <div>
                              page {c.page ?? "?"}, words {c.start_word ?? "?"}-{c.end_word ?? "?"}
                              {c.section ? `, section ${c.section}` : ""}
                            </div>
                            {c.text && <p className={css.citationText}>{String(c.text).slice(0, 220)}</p>}
                            <button
                              className={css.citationLink}
                              onClick={() =>
                                props.onOpenViewer({
                                  page: c.page,
                                  terms: citationTerms(c),
                                  excerpt: String(c.text || ""),
                                })
                              }
                            >
                              Open in Paper Viewer
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span>No citation chunks returned.</span>
                    )}
                    <pre className={css.stats}>{JSON.stringify(message.retrievalStats || {}, null, 2)}</pre>
                  </details>
                )}
              </article>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className={css.composer}>
        <textarea
          className={css.textarea}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={4}
          placeholder="Ask a question about this paper"
          disabled={isAsking}
        />
        <div className={css.composerActions}>
          <button
            className={css.primaryButton}
            onClick={() => void askChat(false)}
            disabled={isAsking || !question.trim()}
          >
            {isAsking ? <Spinner label="Asking..." small /> : "Ask"}
          </button>
          <button className={css.secondaryButton} onClick={() => void askChat(true)} disabled={isAsking || !question.trim()}>
            {isAsking ? <Spinner label="Streaming..." small /> : "Ask (Stream)"}
          </button>
        </div>
      </div>
    </section>
  );
}
