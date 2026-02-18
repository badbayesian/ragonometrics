import { FormEvent, useEffect, useMemo, useState } from "react";
import { LoginView } from "./features/auth/LoginView";
import { ChatTab } from "./features/chat/ChatTab";
import { HowToModal } from "./features/help/HowToModal";
import { CitationNetworkTab } from "./features/network/CitationNetworkTab";
import { OpenAlexTab } from "./features/openalex/OpenAlexTab";
import { StructuredTab } from "./features/structured/StructuredTab";
import { UsageTab } from "./features/usage/UsageTab";
import { PaperViewerTab } from "./features/viewer/PaperViewerTab";
import { WorkflowCacheTab } from "./features/workflow/WorkflowCacheTab";
import { api } from "./shared/api";
import { PaperRow, TabKey } from "./shared/types";
import css from "./App.module.css";

export function App() {
  const [user, setUser] = useState<string>("");
  const [csrfToken, setCsrfToken] = useState<string>("");
  const [loginUser, setLoginUser] = useState<string>("");
  const [loginPass, setLoginPass] = useState<string>("");
  const [forgotIdentifier, setForgotIdentifier] = useState<string>("");
  const [resetToken, setResetToken] = useState<string>("");
  const [resetPassword, setResetPassword] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [tab, setTab] = useState<TabKey>("chat");
  const [queuedQuestion, setQueuedQuestion] = useState<string>("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [viewerHighlight, setViewerHighlight] = useState<{ page?: number | null; terms?: string[]; excerpt?: string } | null>(null);

  const [papers, setPapers] = useState<PaperRow[]>([]);
  const [paperId, setPaperId] = useState<string>("");
  const [model, setModel] = useState<string>("");

  const selectedPaper = useMemo(() => papers.find((p) => p.paper_id === paperId), [papers, paperId]);
  const selectedPaperTitle = String(selectedPaper?.display_title || selectedPaper?.title || selectedPaper?.name || "").trim();
  const selectedPaperAuthors = String(selectedPaper?.display_authors || "").trim();
  const selectedPaperAbstract = String(selectedPaper?.display_abstract || selectedPaper?.abstract || "").trim();

  async function refreshAuthAndPapers() {
    const me = await api<{ username: string; csrf_token?: string }>("/api/v1/auth/me");
    if (me.ok && me.data?.username) {
      setUser(me.data.username);
      setCsrfToken(String(me.data.csrf_token || ""));
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
  }

  useEffect(() => {
    void refreshAuthAndPapers();
  }, []);

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
    setStatus("Logged out");
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
          loginUser={loginUser}
          loginPass={loginPass}
          forgotIdentifier={forgotIdentifier}
          resetToken={resetToken}
          resetPassword={resetPassword}
          status={status}
          onChangeUser={setLoginUser}
          onChangePass={setLoginPass}
          onChangeForgotIdentifier={setForgotIdentifier}
          onChangeResetToken={setResetToken}
          onChangeResetPassword={setResetPassword}
          onLogin={onLogin}
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
        <div className={css.selectorGrid}>
          <label className={css.label}>
            Paper
            <select className={css.input} value={paperId} onChange={(e) => setPaperId(e.target.value)}>
              {papers.map((p) => (
                <option value={p.paper_id} key={p.paper_id}>
                  {p.display_title || p.title || p.name}
                </option>
              ))}
            </select>
          </label>
          <label className={css.label}>
            Model (optional)
            <input className={css.input} value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-5-nano" />
          </label>
        </div>
      </section>

      {selectedPaper && (
        <section className={css.paperSummaryCard}>
          <h2>{selectedPaperTitle || "Selected Paper"}</h2>
          <p className={css.paperMeta}>{selectedPaperAuthors || "Authors unavailable"}</p>
          {selectedPaper?.openalex_url && (
            <p className={css.paperMeta}>
              <a href={selectedPaper.openalex_url} target="_blank" rel="noreferrer">
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
        <button className={`${css.tab} ${tab === "workflow" ? css.tabActive : ""}`} onClick={() => setTab("workflow")}>
          Workflow Cache
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
                model={model}
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

            {tab === "openalex" && selectedPaper && <OpenAlexTab paperId={paperId} onStatus={setStatus} />}

            {tab === "network" && selectedPaper && <CitationNetworkTab paperId={paperId} onStatus={setStatus} />}

            {tab === "usage" && <UsageTab onStatus={setStatus} />}
            {tab === "workflow" && selectedPaper && <WorkflowCacheTab paperId={paperId} onStatus={setStatus} />}
          </>
        )}
      </section>

      <HowToModal open={helpOpen} onClose={() => setHelpOpen(false)} />

      <footer className={css.statusBar}>{status || "Ready"}</footer>
    </main>
  );
}
