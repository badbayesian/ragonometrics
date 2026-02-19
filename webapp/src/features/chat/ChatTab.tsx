import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import rehypeKatex from "rehype-katex";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import { api } from "../../shared/api";
import { ChatHistoryItem, ChatSuggestionPayload, CitationChunk, ProvenanceScore } from "../../shared/types";
import { Spinner } from "../../shared/Spinner";
import robotAvatar from "../../assets/robot-avatar.svg";
import css from "./ChatTab.module.css";
import "katex/dist/katex.min.css";

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
  prompt?: string;
  citations?: CitationChunk[];
  retrievalStats?: Record<string, unknown>;
  cacheHit?: boolean | null;
  cacheHitLayer?: string;
  cacheMissReason?: string;
  model?: string;
  isStreaming?: boolean;
  provenance?: ProvenanceScore | null;
};

type InsightKind = "provenance" | "freshness" | "cache-layer" | "model";

type ExpandedInsight = {
  messageId: string;
  kind: InsightKind;
};

type QueuedPrompt = {
  id: string;
  prompt: string;
  stream: boolean;
  addedAt: string;
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
  onOpenViewer: (payload: {
    page?: number | null;
    terms?: string[];
    excerpt?: string;
    startWord?: number | null;
    endWord?: number | null;
  }) => void;
};

function nowIso(): string {
  return new Date().toISOString();
}

function shortTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatProvenanceLabel(provenance: ProvenanceScore | null | undefined): string {
  if (!provenance) return "";
  return `Prov ${Math.round((Number(provenance.score || 0) * 1000) / 10)}% (${provenance.status})`;
}

function answerSourceLabel(message: ChatMessage): string {
  if (typeof message.cacheHit !== "boolean") return "Unknown";
  return message.cacheHit ? "Cache hit" : "Fresh";
}

function retrievalMethodLabel(message: ChatMessage): string {
  const stats = (message.retrievalStats || {}) as Record<string, unknown>;
  const method = String(stats.method || "").trim();
  return method || "unknown";
}

function provenanceStatusMeaning(status: string): string {
  const clean = String(status || "").trim().toLowerCase();
  if (clean === "high") return "High means the answer appears strongly grounded in cited evidence.";
  if (clean === "medium") return "Medium means the answer is partially grounded but has some uncertainty.";
  return "Low means limited evidence grounding or weak citation support.";
}

function insightHeading(kind: InsightKind): string {
  if (kind === "provenance") return "What Prov Means";
  if (kind === "freshness") return "What Fresh/Cache Means";
  if (kind === "cache-layer") return "What Cache Layer Means";
  return "What Model Means";
}

function insightExplanation(kind: InsightKind, message: ChatMessage): string {
  if (kind === "provenance") {
    const provenance = message.provenance;
    const score = Math.round((Number(provenance?.score || 0) * 1000) / 10);
    const status = String(provenance?.status || "low");
    const warningCount = Array.isArray(provenance?.warnings) ? provenance.warnings.length : 0;
    return `Provenance score estimates evidence grounding using citation coverage, lexical overlap, and anchor validity. This answer is ${score}% (${status}). ${provenanceStatusMeaning(status)}${warningCount > 0 ? ` ${warningCount} warning(s) were detected.` : ""}`;
  }
  if (kind === "freshness") {
    if (message.cacheHit === true) {
      return "Cache hit means this answer was reused from stored results for a matching query/context.";
    }
    if (message.cacheHit === false) {
      return "Fresh means this answer was generated in this request, not reused from cache.";
    }
    return "Freshness is unknown for this answer because cache metadata was not provided.";
  }
  if (kind === "cache-layer") {
    return `Cache layer indicates where a cache hit came from. This answer used layer '${String(message.cacheHitLayer || "unknown")}'.`;
  }
  return `Model shows which LLM generated this answer. This answer used '${String(message.model || "unknown")}'.`;
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
      prompt: String(row.query || ""),
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

function ChatMessageText(props: { text: string; isStreaming: boolean }) {
  const content = props.text || (props.isStreaming ? "..." : "");
  return (
    <ReactMarkdown
      className={css.messageText}
      remarkPlugins={[remarkMath, remarkBreaks]}
      rehypePlugins={[rehypeKatex]}
      skipHtml
    >
      {content}
    </ReactMarkdown>
  );
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
  const [queuedPrompts, setQueuedPrompts] = useState<QueuedPrompt[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [expandedMessageId, setExpandedMessageId] = useState<string | null>(null);
  const [expandedInsight, setExpandedInsight] = useState<ExpandedInsight | null>(null);
  const [isAsking, setIsAsking] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const scoredMessageIdsRef = useRef<Set<string>>(new Set());
  const queueButtonsDisabled = !props.paperId;

  useEffect(() => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [messages]);

  function enqueuePrompt(prompt: string, stream: boolean) {
    const trimmed = prompt.trim();
    if (!trimmed) return;
    const queued: QueuedPrompt = {
      id: `q-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      prompt: trimmed,
      stream,
      addedAt: nowIso(),
    };
    setQueuedPrompts((prev) => [...prev, queued]);
    props.onStatus(`Question queued (${stream ? "stream" : "ask"}).`);
  }

  function removeQueuedPrompt(id: string) {
    setQueuedPrompts((prev) => prev.filter((item) => item.id !== id));
  }

  useEffect(() => {
    if (!props.queuedQuestion) return;
    const incoming = props.queuedQuestion.trim();
    if (incoming) {
      enqueuePrompt(incoming, false);
    }
    props.onQuestionConsumed();
  }, [props.queuedQuestion]);

  useEffect(() => {
    async function bootstrap() {
      if (!props.paperId) return;
      setError("");
      setQuestion("");
      setQueuedPrompts([]);
      const [historyOut, suggestionsOut] = await Promise.all([
        api<ChatHistoryResponse>(`/api/v1/chat/history?paper_id=${encodeURIComponent(props.paperId)}&limit=80`),
        api<ChatSuggestionPayload>(`/api/v1/chat/suggestions?paper_id=${encodeURIComponent(props.paperId)}`),
      ]);
      if (historyOut.ok && historyOut.data?.rows) {
        setMessages(historyToMessages(historyOut.data.rows));
      } else {
        setMessages([]);
      }
      setExpandedMessageId(null);
      setExpandedInsight(null);
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
      prompt: userText,
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

  async function scoreProvenanceForMessage(message: ChatMessage) {
    if (!props.paperId || message.role !== "assistant") return;
    if (!message.text.trim()) return;
    const out = await api<ProvenanceScore>(
      "/api/v1/chat/provenance-score",
      {
        method: "POST",
        body: JSON.stringify({
          paper_id: props.paperId,
          question: String(message.prompt || ""),
          answer: message.text,
          citations: Array.isArray(message.citations) ? message.citations : [],
        }),
      },
      props.csrfToken
    );
    if (!out.ok || !out.data) {
      updateAssistantMessage(message.id, {
        provenance: {
          paper_id: props.paperId,
          question: String(message.prompt || ""),
          score: 0,
          status: "low",
          warnings: [{ code: "provenance_request_failed", message: out.error?.message || "Provenance scoring failed." }],
        },
      });
      return;
    }
    updateAssistantMessage(message.id, { provenance: out.data });
  }

  useEffect(() => {
    async function scorePendingMessages() {
      const pending = messages.filter(
        (item) =>
          item.role === "assistant" &&
          !item.isStreaming &&
          Boolean(item.text.trim()) &&
          !scoredMessageIdsRef.current.has(item.id)
      );
      for (const item of pending) {
        scoredMessageIdsRef.current.add(item.id);
        await scoreProvenanceForMessage(item);
      }
    }
    void scorePendingMessages();
  }, [messages, props.paperId]);

  async function runPrompt(prompt: string, stream: boolean) {
    const trimmedPrompt = prompt.trim();
    if (!props.paperId || !trimmedPrompt || isAsking) return;
    setIsAsking(true);
    setError("");
    props.onStatus(stream ? "Streaming answer..." : "Fetching answer...");

    const { assistantId } = addPendingTurn(trimmedPrompt);
    const payload = {
      paper_id: props.paperId,
      question: trimmedPrompt,
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

  function submitPrompt(stream: boolean) {
    const prompt = question.trim();
    if (!props.paperId || !prompt) return;
    setQuestion("");
    enqueuePrompt(prompt, stream);
  }

  useEffect(() => {
    if (!props.paperId || isAsking || queuedPrompts.length < 1) return;
    const [next, ...rest] = queuedPrompts;
    setQueuedPrompts(rest);
    void runPrompt(next.prompt, next.stream);
  }, [queuedPrompts, isAsking, props.paperId]);

  useEffect(() => {
    setExpandedMessageId((previous) => {
      if (!previous) return previous;
      return messages.some((item) => item.id === previous) ? previous : null;
    });
    setExpandedInsight((previous) => {
      if (!previous) return previous;
      return messages.some((item) => item.id === previous.messageId) ? previous : null;
    });
  }, [messages]);

  function toggleMessageDetails(messageId: string) {
    setExpandedMessageId((previous) => {
      const next = previous === messageId ? null : messageId;
      setExpandedInsight((info) => {
        if (!next) return null;
        if (!info) return null;
        return info.messageId === next ? info : null;
      });
      return next;
    });
  }

  function openInsight(event: MouseEvent, messageId: string, kind: InsightKind) {
    event.stopPropagation();
    setExpandedMessageId(messageId);
    setExpandedInsight((previous) => {
      if (previous && previous.messageId === messageId && previous.kind === kind) return null;
      return { messageId, kind };
    });
  }

  async function clearHistory() {
    if (!props.paperId || isAsking || queuedPrompts.length > 0) return;
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
          <button
            className={css.secondaryButton}
            onClick={() => void clearHistory()}
            disabled={isAsking || queuedPrompts.length > 0}
          >
            Clear History
          </button>
        </div>
      </header>

      {suggestions.length > 0 && (
        <div className={css.suggestions}>
          {suggestions.map((item) => (
            <button key={item} className={css.suggestionChip} onClick={() => setQuestion(item)}>
              {item}
            </button>
          ))}
        </div>
      )}

      {error && <div className={css.errorBanner}>{error}</div>}

      {queuedPrompts.length > 0 && (
        <section className={css.queuePanel}>
          <div className={css.queueHeader}>
            <strong>Queued questions ({queuedPrompts.length})</strong>
            <button className={css.secondaryButton} onClick={() => setQueuedPrompts([])}>
              Clear Queue
            </button>
          </div>
          <ul className={css.queueList}>
            {queuedPrompts.map((item, idx) => (
              <li key={item.id} className={css.queueItem}>
                <span className={css.queueIndex}>#{idx + 1}</span>
                <span className={css.queueMode}>{item.stream ? "Stream" : "Ask"}</span>
                <span className={css.queueText}>{item.prompt}</span>
                <span className={css.queueTime}>{shortTime(item.addedAt)}</span>
                <button className={css.queueRemove} onClick={() => removeQueuedPrompt(item.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className={css.timeline}>
        {messages.length === 0 ? (
          <div className={css.emptyState}>No messages yet. Start with a suggested question or ask your own.</div>
        ) : (
          messages.map((message) => {
            const isAssistant = message.role === "assistant";
            const isExpanded = isAssistant && expandedMessageId === message.id;
            const provenanceLabel = formatProvenanceLabel(message.provenance);
            const activeInsight = isExpanded && expandedInsight?.messageId === message.id ? expandedInsight.kind : null;
            return (
            <div key={message.id} className={`${css.messageRow} ${message.role === "user" ? css.userRow : css.assistantRow}`}>
              {isAssistant && (
                <img src={robotAvatar} alt="Assistant robot avatar" className={css.avatar} />
              )}
              <article
                data-testid={isAssistant ? "assistant-message-bubble" : undefined}
                className={`${css.messageBubble} ${message.role === "user" ? css.userBubble : css.assistantBubble} ${isAssistant ? css.assistantBubbleClickable : ""} ${isExpanded ? css.assistantBubbleExpanded : ""}`}
                role={isAssistant ? "button" : undefined}
                tabIndex={isAssistant ? 0 : undefined}
                aria-expanded={isAssistant ? isExpanded : undefined}
                onClick={
                  isAssistant
                    ? () => toggleMessageDetails(message.id)
                    : undefined
                }
                onKeyDown={
                  isAssistant
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          toggleMessageDetails(message.id);
                        }
                      }
                    : undefined
                }
              >
                <div className={css.messageMeta}>
                  <span>{message.role === "user" ? "You" : "Assistant"}</span>
                  <span>{shortTime(message.createdAt)}</span>
                </div>
                <ChatMessageText text={message.text} isStreaming={Boolean(message.isStreaming)} />
                {isAssistant && (
                  <div className={css.answerMeta}>
                    {typeof message.cacheHit === "boolean" && (
                      <button
                        type="button"
                        className={`${css.metaBadge} ${css.metaBadgeButton}`}
                        onClick={(event) => openInsight(event, message.id, "freshness")}
                      >
                        {message.cacheHit ? "Cache hit" : "Fresh"}
                      </button>
                    )}
                    {message.cacheHitLayer && (
                      <button
                        type="button"
                        className={`${css.metaBadge} ${css.metaBadgeButton}`}
                        onClick={(event) => openInsight(event, message.id, "cache-layer")}
                      >
                        layer={message.cacheHitLayer}
                      </button>
                    )}
                    {message.model && (
                      <button
                        type="button"
                        className={`${css.metaBadge} ${css.metaBadgeButton}`}
                        onClick={(event) => openInsight(event, message.id, "model")}
                      >
                        {message.model}
                      </button>
                    )}
                    {message.provenance && (
                      <button
                        type="button"
                        className={`${css.metaBadge} ${css.metaBadgeButton}`}
                        onClick={(event) => openInsight(event, message.id, "provenance")}
                      >
                        {provenanceLabel}
                      </button>
                    )}
                    {message.provenance && Array.isArray(message.provenance.warnings) && message.provenance.warnings.length > 0 && (
                      <span className={css.metaWarning}>{message.provenance.warnings.length} warning(s)</span>
                    )}
                    {message.isStreaming && <span className={css.streaming}>Streaming...</span>}
                    <span className={css.metaHint}>{isExpanded ? "Hide details" : "Click for details"}</span>
                  </div>
                )}
                {isAssistant && isExpanded && (
                  <div className={css.detailsPanel}>
                    {activeInsight && (
                      <div className={css.insightCallout}>
                        <strong>{insightHeading(activeInsight)}</strong>
                        <p className={css.insightText}>{insightExplanation(activeInsight, message)}</p>
                      </div>
                    )}
                    <div className={css.provenanceBlock}>
                      <strong>About this answer</strong>
                      <ul className={css.aboutList}>
                        <li>Response source: {answerSourceLabel(message)}</li>
                        <li>Retrieval method: {retrievalMethodLabel(message)}</li>
                        <li>Citation chunks: {Array.isArray(message.citations) ? message.citations.length : 0}</li>
                        {message.model && <li>Model: {message.model}</li>}
                        {message.cacheHitLayer && <li>Cache layer: {message.cacheHitLayer}</li>}
                        {message.cacheMissReason && <li>Cache miss reason: {message.cacheMissReason}</li>}
                        {message.provenance && <li>Provenance: {provenanceLabel}</li>}
                      </ul>
                    </div>
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
                              onClick={(event) => {
                                event.stopPropagation();
                                props.onOpenViewer({
                                  page: c.page,
                                  terms: citationTerms(c),
                                  excerpt: String(c.text || ""),
                                  startWord: typeof c.start_word === "number" ? c.start_word : null,
                                  endWord: typeof c.end_word === "number" ? c.end_word : null,
                                });
                              }}
                            >
                              Open in Paper Viewer
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span>No citation chunks returned.</span>
                    )}
                    {message.provenance && (
                      <div className={css.provenanceBlock}>
                        <strong>Provenance score:</strong>{" "}
                        {provenanceLabel.replace("Prov ", "")}
                        {Array.isArray(message.provenance.warnings) && message.provenance.warnings.length > 0 && (
                          <ul className={css.warningList}>
                            {message.provenance.warnings.map((warn, idx) => (
                              <li key={`${message.id}-warn-${idx}`}>
                                <code>{warn.code}</code>: {warn.message}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                    <pre className={css.stats}>{JSON.stringify(message.retrievalStats || {}, null, 2)}</pre>
                  </div>
                )}
              </article>
            </div>
          );
          })
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
        />
        <div className={css.composerActions}>
          <button
            className={css.primaryButton}
            onClick={() => submitPrompt(false)}
            disabled={queueButtonsDisabled}
          >
            Queue Ask
          </button>
          <button className={css.secondaryButton} onClick={() => submitPrompt(true)} disabled={queueButtonsDisabled}>
            Queue Stream
          </button>
          {isAsking && <Spinner label="Running..." small />}
          {isAsking && <span className={css.queueHint}>You can queue more questions while this is running.</span>}
        </div>
      </div>
    </section>
  );
}
