import { FormEvent, useEffect, useMemo, useState } from "react";
import { LoginView } from "./features/auth/LoginView";
import { ChatTab } from "./features/chat/ChatTab";
import { CacheInspectorTab } from "./features/cache/CacheInspectorTab";
import { CompareTab } from "./features/compare/CompareTab";
import { HowToModal } from "./features/help/HowToModal";
import { CitationNetworkTab } from "./features/network/CitationNetworkTab";
import { OpenAlexTab } from "./features/openalex/OpenAlexTab";
import { StructuredTab } from "./features/structured/StructuredTab";
import { UsageTab } from "./features/usage/UsageTab";
import { PaperViewerTab } from "./features/viewer/PaperViewerTab";
import { WorkflowCacheTab } from "./features/workflow/WorkflowCacheTab";
import { api } from "./shared/api";
import { PaperRow, PersonaRow, ProjectContextRow, ProjectRow, TabKey } from "./shared/types";
import css from "./App.module.css";

function paperOptionLabel(row: PaperRow | null | undefined): string {
  return String(row?.display_title || row?.title || row?.name || "").trim();
}

function normalizeText(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

function normalizeFinderLabel(value: unknown): string {
  const text = String(value || "")
    .replace(/\.pdf$/i, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  return text.replace(/\s+/g, " ");
}

function dedupePapersById(rows: PaperRow[]): PaperRow[] {
  const unique = new Map<string, PaperRow>();
  for (const row of rows) {
    const paperId = String(row.paper_id || "").trim();
    if (!paperId) continue;
    if (!unique.has(paperId)) {
      unique.set(paperId, row);
    }
  }
  return Array.from(unique.values());
}

function dedupePapersByFinderLabel(rows: PaperRow[]): PaperRow[] {
  const unique = new Map<string, PaperRow>();
  for (const row of rows) {
    const key = normalizeFinderLabel(paperOptionLabel(row)) || `paper-id:${String(row.paper_id || "").trim()}`;
    if (!key) continue;
    if (!unique.has(key)) {
      unique.set(key, row);
    }
  }
  return Array.from(unique.values());
}

function envFlagEnabled(value: string | undefined): boolean {
  const raw = String(value || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function App() {
  const registrationEnabled = envFlagEnabled(import.meta.env.VITE_WEB_REGISTRATION_ENABLED);
  const [user, setUser] = useState<string>("");
  const [csrfToken, setCsrfToken] = useState<string>("");
  const [loginUser, setLoginUser] = useState<string>("");
  const [loginPass, setLoginPass] = useState<string>("");
  const [registerUsername, setRegisterUsername] = useState<string>("");
  const [registerEmail, setRegisterEmail] = useState<string>("");
  const [registerPassword, setRegisterPassword] = useState<string>("");
  const [forgotIdentifier, setForgotIdentifier] = useState<string>("");
  const [resetToken, setResetToken] = useState<string>("");
  const [resetPassword, setResetPassword] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [tab, setTab] = useState<TabKey>("chat");
  const [queuedQuestion, setQueuedQuestion] = useState<string>("");
  const [chatScopeMode, setChatScopeMode] = useState<"single" | "multi">("single");
  const [multiPaperIds, setMultiPaperIds] = useState<string[]>([]);
  const [multiConversationId, setMultiConversationId] = useState<string>("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [debugMode, setDebugMode] = useState(false);
  const [viewerHighlight, setViewerHighlight] = useState<{
    page?: number | null;
    terms?: string[];
    excerpt?: string;
    startWord?: number | null;
    endWord?: number | null;
  } | null>(null);

  const [papers, setPapers] = useState<PaperRow[]>([]);
  const [paperId, setPaperId] = useState<string>("");
  const [paperSearch, setPaperSearch] = useState<string>("");
  const [authorFilter, setAuthorFilter] = useState<string>("");
  const [venueFilter, setVenueFilter] = useState<string>("");
  const [yearFilter, setYearFilter] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [personas, setPersonas] = useState<PersonaRow[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string>("");
  const [currentPersonaId, setCurrentPersonaId] = useState<string>("");

  const selectedPaper = useMemo(() => papers.find((p) => p.paper_id === paperId), [papers, paperId]);
  const selectedPaperTitle = String(selectedPaper?.display_title || selectedPaper?.title || selectedPaper?.name || "").trim();
  const selectedPaperAuthors = String(selectedPaper?.display_authors || "").trim();
  const selectedPaperAuthorItems = Array.isArray(selectedPaper?.author_items) ? selectedPaper?.author_items || [] : [];
  const selectedPaperAbstract = String(selectedPaper?.display_abstract || selectedPaper?.abstract || "").trim();
  const finderBasePapers = useMemo(() => dedupePapersByFinderLabel(dedupePapersById(papers)), [papers]);
  const papersByFilters = useMemo(() => {
    const authorNeedle = normalizeText(authorFilter);
    const venueNeedle = normalizeText(venueFilter);
    const yearNeedle = normalizeText(yearFilter);
    return finderBasePapers.filter((row) => {
      if (authorNeedle) {
        const displayAuthors = normalizeText(row.display_authors);
        const itemAuthors = Array.isArray(row.author_items)
          ? row.author_items.map((item) => normalizeText(item?.name)).filter(Boolean).join(" ")
          : "";
        const authorsBlob = `${displayAuthors} ${itemAuthors}`.trim();
        if (!authorsBlob.includes(authorNeedle)) {
          return false;
        }
      }
      if (venueNeedle) {
        const venueBlob = `${normalizeText(row.venue)} ${normalizeText(row.doi)}`.trim();
        if (!venueBlob.includes(venueNeedle)) {
          return false;
        }
      }
      if (yearNeedle) {
        const yearText = row.publication_year == null ? "" : String(row.publication_year).trim().toLowerCase();
        if (!yearText.includes(yearNeedle)) {
          return false;
        }
      }
      return true;
    });
  }, [finderBasePapers, authorFilter, venueFilter, yearFilter]);
  const filteredPapers = useMemo(() => {
    const needle = normalizeText(paperSearch);
    if (!needle) return papersByFilters;
    return papersByFilters.filter((row) => normalizeText(paperOptionLabel(row)).includes(needle));
  }, [papersByFilters, paperSearch]);
  const dedupedPapersByFilters = useMemo(() => dedupePapersByFinderLabel(papersByFilters), [papersByFilters]);
  const dedupedFilteredPapers = useMemo(() => dedupePapersByFinderLabel(filteredPapers), [filteredPapers]);
  const paperSearchOptions = useMemo(() => {
    if (dedupedFilteredPapers.length > 0) return dedupedFilteredPapers;
    return dedupedPapersByFilters;
  }, [dedupedFilteredPapers, dedupedPapersByFilters]);
  const authorFilterOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of finderBasePapers) {
      if (Array.isArray(row.author_items)) {
        for (const item of row.author_items) {
          const name = String(item?.name || "").trim();
          if (name) values.add(name);
        }
      }
      const raw = String(row.display_authors || "").trim();
      if (raw) {
        for (const part of raw.split(",")) {
          const name = part.trim();
          if (name) values.add(name);
        }
      }
    }
    return Array.from(values).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  }, [finderBasePapers]);
  const venueFilterOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of finderBasePapers) {
      const venue = String(row.venue || "").trim();
      if (venue) values.add(venue);
    }
    return Array.from(values).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  }, [finderBasePapers]);
  const yearFilterOptions = useMemo(() => {
    const values = new Set<string>();
    for (const row of finderBasePapers) {
      if (row.publication_year == null) continue;
      values.add(String(row.publication_year));
    }
    return Array.from(values).sort((a, b) => Number(b) - Number(a));
  }, [finderBasePapers]);
  const activeFilterCount = useMemo(() => {
    return [authorFilter, venueFilter, yearFilter].filter((value) => String(value || "").trim().length > 0).length;
  }, [authorFilter, venueFilter, yearFilter]);
  const filteredMatchCount = paperSearch ? dedupedFilteredPapers.length : dedupedPapersByFilters.length;

  function resetPaperFilters() {
    setAuthorFilter("");
    setVenueFilter("");
    setYearFilter("");
  }

  async function refreshAuthAndPapers() {
    const me = await api<{
      username: string;
      csrf_token?: string;
      project_context?: ProjectContextRow;
      projects?: ProjectRow[];
      personas?: PersonaRow[];
    }>("/api/v1/auth/me");
    if (me.ok && me.data?.username) {
      setUser(me.data.username);
      setCsrfToken(String(me.data.csrf_token || ""));
      const projectRows = Array.isArray(me.data.projects) ? me.data.projects : [];
      const personaRows = Array.isArray(me.data.personas) ? me.data.personas : [];
      setProjects(projectRows);
      setPersonas(personaRows);
      setCurrentProjectId(String(me.data.project_context?.project_id || ""));
      setCurrentPersonaId(String(me.data.project_context?.persona_id || ""));
      const p = await api<{ papers: PaperRow[] }>("/api/v1/papers");
      if (p.ok && p.data?.papers) {
        setPapers(p.data.papers);
        if (!paperId && p.data.papers.length > 0) {
          setPaperId(p.data.papers[0].paper_id);
        }
      }
      return;
    }
    setUser("");
    setCsrfToken("");
    setProjects([]);
    setPersonas([]);
    setCurrentProjectId("");
    setCurrentPersonaId("");
  }

  useEffect(() => {
    void refreshAuthAndPapers();
  }, []);

  useEffect(() => {
    if (!paperId) return;
    setMultiPaperIds((prev) => {
      if (prev.includes(paperId)) return prev;
      if (prev.length >= 10) return prev;
      return [paperId, ...prev].slice(0, 10);
    });
  }, [paperId]);

  useEffect(() => {
    setMultiConversationId("");
  }, [currentProjectId, currentPersonaId]);

  useEffect(() => {
    const stored = String(window.localStorage.getItem("rag_theme") || "").trim();
    if (stored === "light" || stored === "dark") {
      setTheme(stored);
      return;
    }
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    setTheme(prefersDark ? "dark" : "light");
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem("rag_theme", theme);
  }, [theme]);

  useEffect(() => {
    const raw = String(window.localStorage.getItem("rag_debug_mode") || "").trim();
    setDebugMode(raw === "1" || raw.toLowerCase() === "true");
  }, []);

  useEffect(() => {
    window.localStorage.setItem("rag_debug_mode", debugMode ? "1" : "0");
  }, [debugMode]);

  useEffect(() => {
    if (!debugMode && (tab === "workflow" || tab === "cache")) {
      setTab("chat");
    }
  }, [debugMode, tab]);

  useEffect(() => {
    if (!selectedPaper) return;
    setPaperSearch(paperOptionLabel(selectedPaper));
  }, [selectedPaper]);

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setStatus("Logging in...");
    const out = await api<{ username: string; csrf_token: string }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ identifier: loginUser, password: loginPass }),
    });
    if (!out.ok || !out.data?.username) {
      setStatus(out.error?.message || "Login failed");
      return;
    }
    setUser(out.data.username);
    setCsrfToken(out.data.csrf_token || "");
    setStatus("Logged in");
    await refreshAuthAndPapers();
  }

  async function onLogout() {
    await api("/api/v1/auth/logout", { method: "POST" }, csrfToken);
    setUser("");
    setCsrfToken("");
    setPapers([]);
    setPaperId("");
    setProjects([]);
    setPersonas([]);
    setCurrentProjectId("");
    setCurrentPersonaId("");
    setStatus("Logged out");
  }

  async function onSelectProject(nextProjectId: string) {
    const target = String(nextProjectId || "").trim();
    if (!target || target === currentProjectId) return;
    setStatus("Switching project...");
    const out = await api<{ project_id: string; persona_id?: string }>(`/api/v1/projects/${encodeURIComponent(target)}/select`, {
      method: "POST",
    }, csrfToken);
    if (!out.ok) {
      setStatus(out.error?.message || "Failed to switch project.");
      return;
    }
    await refreshAuthAndPapers();
    setStatus("Project switched.");
  }

  async function onSelectPersona(nextPersonaId: string) {
    const target = String(nextPersonaId || "").trim();
    if (!target || !currentProjectId || target === currentPersonaId) return;
    setStatus("Switching persona...");
    const out = await api<{ persona_id: string }>(
      `/api/v1/projects/${encodeURIComponent(currentProjectId)}/personas/${encodeURIComponent(target)}/select`,
      { method: "POST" },
      csrfToken,
    );
    if (!out.ok) {
      setStatus(out.error?.message || "Failed to switch persona.");
      return;
    }
    setCurrentPersonaId(target);
    setStatus("Persona switched.");
  }

  async function onForgotPassword(e: FormEvent) {
    e.preventDefault();
    setStatus("Requesting password reset...");
    const out = await api<{ accepted: boolean; message: string; debug_reset_token?: string }>("/api/v1/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ identifier: forgotIdentifier }),
    });
    if (!out.ok) {
      setStatus(out.error?.message || "Reset request failed.");
      return;
    }
    const tokenHint = out.data?.debug_reset_token ? ` Debug token: ${out.data.debug_reset_token}` : "";
    setStatus(`${out.data?.message || "If the account exists, instructions were sent."}${tokenHint}`);
  }

  async function onRegister(e: FormEvent) {
    e.preventDefault();
    if (!registrationEnabled) {
      setStatus("Account registration is currently disabled.");
      return;
    }
    setStatus("Creating account...");
    const out = await api<{ created: boolean; username: string; alert_email_sent?: boolean }>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username: registerUsername,
        email: registerEmail,
        password: registerPassword,
      }),
    });
    if (!out.ok) {
      setStatus(out.error?.message || "Account creation failed.");
      return;
    }
    const alertText = out.data?.alert_email_sent ? " Alert email sent." : "";
    setStatus(`Account created for ${out.data?.username || registerUsername}. You can log in now.${alertText}`);
    setRegisterPassword("");
  }

  async function onResetPassword(e: FormEvent) {
    e.preventDefault();
    setStatus("Resetting password...");
    const out = await api<{ reset: boolean }>("/api/v1/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token: resetToken, new_password: resetPassword }),
    });
    if (!out.ok) {
      setStatus(out.error?.message || "Password reset failed.");
      return;
    }
    setResetToken("");
    setResetPassword("");
    setStatus("Password reset successful. You can log in now.");
  }

  if (!user) {
    return (
      <main className={css.page}>
        <LoginView
          registrationEnabled={registrationEnabled}
          loginUser={loginUser}
          loginPass={loginPass}
          registerUsername={registerUsername}
          registerEmail={registerEmail}
          registerPassword={registerPassword}
          forgotIdentifier={forgotIdentifier}
          resetToken={resetToken}
          resetPassword={resetPassword}
          status={status}
          onChangeUser={setLoginUser}
          onChangePass={setLoginPass}
          onChangeRegisterUsername={setRegisterUsername}
          onChangeRegisterEmail={setRegisterEmail}
          onChangeRegisterPassword={setRegisterPassword}
          onChangeForgotIdentifier={setForgotIdentifier}
          onChangeResetToken={setResetToken}
          onChangeResetPassword={setResetPassword}
          onLogin={onLogin}
          onRegister={onRegister}
          onForgotPassword={onForgotPassword}
          onResetPassword={onResetPassword}
        />
      </main>
    );
  }

  return (
    <main className={css.page}>
      <header className={css.topbar}>
        <div>
          <h1 className={css.title}>Ragonometrics Web</h1>
          <p className={css.subtitle}>Multi-user research workspace for paper chat, structured extraction, and metadata exploration.</p>
        </div>
        <div className={css.topbarActions}>
          <span className={css.userBadge}>User: {user}</span>
          {projects.length > 0 && (
            <label className={css.inlineLabel}>
              Project
              <select
                className={css.inlineSelect}
                value={currentProjectId}
                onChange={(e) => void onSelectProject(e.target.value)}
              >
                {projects.map((row) => (
                  <option key={row.project_id} value={row.project_id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {personas.length > 0 && (
            <label className={css.inlineLabel}>
              Persona
              <select
                className={css.inlineSelect}
                value={currentPersonaId}
                onChange={(e) => void onSelectPersona(e.target.value)}
              >
                {personas.map((row) => (
                  <option key={row.persona_id} value={row.persona_id}>
                    {row.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            className={`${css.topButton} ${debugMode ? css.topButtonActive : ""}`}
            onClick={() => setDebugMode((prev) => !prev)}
          >
            {debugMode ? "Debug: On" : "Debug: Off"}
          </button>
          <button
            className={css.topButton}
            onClick={() => setTheme((prev) => (prev === "light" ? "dark" : "light"))}
          >
            {theme === "light" ? "Dark Mode" : "Light Mode"}
          </button>
          <button className={css.topHelpButton} onClick={() => setHelpOpen(true)}>
            Help / How To
          </button>
          <button className={css.topButton} onClick={() => void onLogout()}>
            Logout
          </button>
        </div>
      </header>

      <section className={css.selectorCard}>
        <div className={css.selectorHeader}>
          <div>
            <h2 className={css.selectorTitle}>Paper Finder</h2>
            <p className={css.selectorMeta}>
              {filteredMatchCount} matching | {papersByFilters.length} after filters | {finderBasePapers.length} total
            </p>
          </div>
          <div className={css.selectorActions}>
            {activeFilterCount > 0 && (
              <span className={css.filterBadge}>
                {activeFilterCount} filter{activeFilterCount === 1 ? "" : "s"} active
              </span>
            )}
            {activeFilterCount > 0 && (
              <button className={css.clearFiltersButton} onClick={resetPaperFilters}>
                Clear Filters
              </button>
            )}
          </div>
        </div>

        <div className={css.finderGrid}>
          <label className={`${css.label} ${css.finderLabel}`}>
            Find paper
            <input
              className={css.input}
              list="paper-title-options"
              value={paperSearch}
              onChange={(e) => {
                const value = String(e.target.value || "");
                setPaperSearch(value);
                const match = paperSearchOptions.find((row) => {
                  const title = normalizeText(paperOptionLabel(row));
                  const needle = normalizeText(value);
                  if (!needle) return false;
                  return title === needle;
                });
                if (match) {
                  setPaperId(match.paper_id);
                }
              }}
              onDoubleClick={(e) => {
                setPaperSearch("");
                const input = e.currentTarget as HTMLInputElement & { showPicker?: () => void };
                if (typeof input.showPicker === "function") {
                  try {
                    input.showPicker();
                  } catch {
                    // Some browsers block programmatic picker calls; keep fallback behavior.
                  }
                }
              }}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                const candidates = paperSearchOptions;
                if (candidates.length < 1) return;
                e.preventDefault();
                const first = candidates[0];
                setPaperId(first.paper_id);
                setPaperSearch(paperOptionLabel(first));
              }}
              placeholder="Type a paper title"
            />
            <datalist id="paper-title-options">
              {paperSearchOptions.map((row) => (
                <option key={`title-${row.paper_id}`} value={paperOptionLabel(row)} />
              ))}
            </datalist>
            <small className={css.selectorHint}>
              Press Enter to pick top match. Double-click to open the list for the current filters.
            </small>
          </label>
          <label className={css.label}>
            Model (optional)
            <input className={css.input} value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-5-nano" />
          </label>
        </div>

        <div className={css.filtersPanel}>
          <label className={css.label}>
            Author
            <input
              className={css.input}
              list="paper-author-options"
              value={authorFilter}
              onChange={(e) => setAuthorFilter(e.target.value)}
              placeholder="Filter by author"
            />
            <datalist id="paper-author-options">
              {authorFilterOptions.map((value) => (
                <option key={`author-${value}`} value={value} />
              ))}
            </datalist>
          </label>
          <label className={css.label}>
            Venue
            <input
              className={css.input}
              list="paper-venue-options"
              value={venueFilter}
              onChange={(e) => setVenueFilter(e.target.value)}
              placeholder="Filter by venue"
            />
            <datalist id="paper-venue-options">
              {venueFilterOptions.map((value) => (
                <option key={`venue-${value}`} value={value} />
              ))}
            </datalist>
          </label>
          <label className={css.label}>
            Year
            <input
              className={css.input}
              list="paper-year-options"
              value={yearFilter}
              onChange={(e) => setYearFilter(e.target.value)}
              placeholder="e.g. 2019"
            />
            <datalist id="paper-year-options">
              {yearFilterOptions.map((value) => (
                <option key={`year-${value}`} value={value} />
              ))}
            </datalist>
          </label>
        </div>
      </section>

      {selectedPaper && (
        <section className={css.paperSummaryCard}>
          <h2>{selectedPaperTitle || "Selected Paper"}</h2>
          {selectedPaperAuthorItems.length > 0 ? (
            <p className={css.paperMeta}>
              Authors:{" "}
              {selectedPaperAuthorItems.map((author, idx) => {
                const label = String(author?.name || "").trim();
                if (!label) return null;
                const href = String(author?.openalex_url || "").trim();
                const key = `${label}-${String(author?.id || idx)}`;
                const prefix = idx > 0 ? <span key={`${key}-sep`}>, </span> : null;
                if (!href) {
                  return (
                    <span key={key}>
                      {prefix}
                      {label}
                    </span>
                  );
                }
                return (
                  <span key={key}>
                    {prefix}
                    <a className={css.paperMetaLink} href={href} target="_blank" rel="noreferrer">
                      {label}
                    </a>
                  </span>
                );
              })}
            </p>
          ) : (
            <p className={css.paperMeta}>{selectedPaperAuthors || "Authors unavailable"}</p>
          )}
          {selectedPaper?.openalex_url && (
            <p className={css.paperMeta}>
              <a className={css.paperMetaLink} href={selectedPaper.openalex_url} target="_blank" rel="noreferrer">
                Open on OpenAlex
              </a>
            </p>
          )}
          <p className={css.paperAbstract}>{selectedPaperAbstract || "No abstract available for this paper."}</p>
        </section>
      )}

      <nav className={css.tabs}>
        <button className={`${css.tab} ${tab === "chat" ? css.tabActive : ""}`} onClick={() => setTab("chat")}>
          Chat
        </button>
        <button className={`${css.tab} ${tab === "viewer" ? css.tabActive : ""}`} onClick={() => setTab("viewer")}>
          Paper Viewer
        </button>
        <button className={`${css.tab} ${tab === "structured" ? css.tabActive : ""}`} onClick={() => setTab("structured")}>
          Structured Workstream
        </button>
        <button className={`${css.tab} ${tab === "openalex" ? css.tabActive : ""}`} onClick={() => setTab("openalex")}>
          OpenAlex Metadata
        </button>
        <button className={`${css.tab} ${tab === "network" ? css.tabActive : ""}`} onClick={() => setTab("network")}>
          Citation Network
        </button>
        <button className={`${css.tab} ${tab === "usage" ? css.tabActive : ""}`} onClick={() => setTab("usage")}>
          Usage
        </button>
        {debugMode && (
          <button className={`${css.tab} ${tab === "workflow" ? css.tabActive : ""}`} onClick={() => setTab("workflow")}>
            Workflow Cache
          </button>
        )}
        {debugMode && (
          <button className={`${css.tab} ${tab === "cache" ? css.tabActive : ""}`} onClick={() => setTab("cache")}>
            Cache Inspector
          </button>
        )}
        <button className={`${css.tab} ${tab === "compare" ? css.tabActive : ""}`} onClick={() => setTab("compare")}>
          Compare
        </button>
      </nav>

      <section className={css.content}>
        {!selectedPaper && tab !== "usage" ? (
          <div className={css.emptyState}>No paper selected.</div>
        ) : (
          <>
            {tab === "chat" && selectedPaper && (
              <ChatTab
                csrfToken={csrfToken}
                paperId={paperId}
                paperName={selectedPaperTitle || selectedPaper.name}
                paperPath={selectedPaper.path}
                papers={papers}
                model={model}
                chatScopeMode={chatScopeMode}
                onChatScopeModeChange={setChatScopeMode}
                multiPaperIds={multiPaperIds}
                onMultiPaperIdsChange={(ids) => {
                  setMultiPaperIds(ids.slice(0, 10));
                  setMultiConversationId("");
                }}
                multiConversationId={multiConversationId}
                onMultiConversationIdChange={setMultiConversationId}
                queuedQuestion={queuedQuestion}
                onQuestionConsumed={() => setQueuedQuestion("")}
                onStatus={setStatus}
                onOpenViewer={(payload) => {
                  setViewerHighlight(payload);
                  setTab("viewer");
                }}
              />
            )}

            {tab === "viewer" && selectedPaper && (
              <PaperViewerTab
                csrfToken={csrfToken}
                paperId={paperId}
                paperTitle={selectedPaperTitle || selectedPaper.name}
                highlightRequest={viewerHighlight}
                onStatus={setStatus}
              />
            )}

            {tab === "structured" && selectedPaper && (
              <StructuredTab
                csrfToken={csrfToken}
                paperId={paperId}
                paperName={selectedPaperTitle || selectedPaper.name}
                model={model}
                onStatus={setStatus}
                onAskInChat={(question) => {
                  setQueuedQuestion(question);
                  setTab("chat");
                  setStatus("Question queued for chat.");
                }}
              />
            )}

            {tab === "openalex" && selectedPaper && (
              <OpenAlexTab csrfToken={csrfToken} paperId={paperId} onStatus={setStatus} />
            )}

            {tab === "network" && selectedPaper && (
              <CitationNetworkTab
                paperId={paperId}
                csrfToken={csrfToken}
                multiPaperIds={multiPaperIds}
                chatScopeMode={chatScopeMode}
                onStatus={setStatus}
              />
            )}

            {tab === "usage" && <UsageTab onStatus={setStatus} />}
            {debugMode && tab === "workflow" && selectedPaper && <WorkflowCacheTab paperId={paperId} onStatus={setStatus} />}
            {debugMode && tab === "cache" && selectedPaper && <CacheInspectorTab paperId={paperId} model={model} onStatus={setStatus} />}
            {tab === "compare" && selectedPaper && (
              <CompareTab
                csrfToken={csrfToken}
                paperId={paperId}
                model={model}
                onStatus={setStatus}
              />
            )}
          </>
        )}
      </section>

      <HowToModal open={helpOpen} onClose={() => setHelpOpen(false)} />

      <footer className={css.statusBar}>{status || "Ready"}</footer>
    </main>
  );
}
