import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import { PaperNote } from "../../shared/types";
import { Spinner } from "../../shared/Spinner";
import css from "./PaperViewerTab.module.css";

type HighlightRequest = {
  page?: number | null;
  terms?: string[];
  excerpt?: string;
};

type Props = {
  csrfToken: string;
  paperId: string;
  paperTitle: string;
  highlightRequest?: HighlightRequest | null;
  onStatus: (text: string) => void;
};

function cleanTerms(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 12);
}

export function PaperViewerTab(props: Props) {
  const [page, setPage] = useState(1);
  const [notes, setNotes] = useState<PaperNote[]>([]);
  const [loadingNotes, setLoadingNotes] = useState(false);
  const [saving, setSaving] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [highlightText, setHighlightText] = useState("");
  const [highlightTermsInput, setHighlightTermsInput] = useState("");
  const [color, setColor] = useState("#ffe58a");
  const [error, setError] = useState("");

  const highlightTerms = useMemo(() => cleanTerms(highlightTermsInput), [highlightTermsInput]);
  const searchHint = highlightTerms[0] ? `&search=${encodeURIComponent(highlightTerms[0])}` : "";
  const pdfUrl = `/api/v1/papers/${encodeURIComponent(props.paperId)}/content#page=${encodeURIComponent(String(page))}${searchHint}`;

  async function loadNotes() {
    if (!props.paperId) return;
    setLoadingNotes(true);
    setError("");
    const out = await api<{ rows: PaperNote[] }>(
      `/api/v1/papers/${encodeURIComponent(props.paperId)}/notes?page=${encodeURIComponent(String(page))}`
    );
    if (!out.ok) {
      const message = out.error?.message || "Unable to load notes.";
      setError(message);
      props.onStatus(message);
      setLoadingNotes(false);
      return;
    }
    setNotes(Array.isArray(out.data?.rows) ? out.data?.rows : []);
    setLoadingNotes(false);
  }

  useEffect(() => {
    setPage(1);
    setNotes([]);
    setNoteText("");
    setHighlightText("");
    setHighlightTermsInput("");
    void loadNotes();
  }, [props.paperId]);

  useEffect(() => {
    void loadNotes();
  }, [page]);

  useEffect(() => {
    if (!props.highlightRequest) return;
    if (props.highlightRequest.page && Number.isFinite(props.highlightRequest.page)) {
      setPage(Math.max(1, Number(props.highlightRequest.page)));
    }
    const reqTerms = Array.isArray(props.highlightRequest.terms) ? props.highlightRequest.terms.filter(Boolean) : [];
    if (reqTerms.length > 0) {
      setHighlightTermsInput(reqTerms.join(", "));
    }
    if (props.highlightRequest.excerpt) {
      setHighlightText(String(props.highlightRequest.excerpt));
    }
  }, [props.highlightRequest]);

  async function addNote() {
    if (!props.paperId || !noteText.trim() || saving) return;
    setSaving(true);
    setError("");
    const out = await api<PaperNote>(
      `/api/v1/papers/${encodeURIComponent(props.paperId)}/notes`,
      {
        method: "POST",
        body: JSON.stringify({
          paper_id: props.paperId,
          page_number: page,
          highlight_text: highlightText,
          highlight_terms: highlightTerms,
          note_text: noteText,
          color,
        }),
      },
      props.csrfToken
    );
    if (!out.ok) {
      const message = out.error?.message || "Unable to save note.";
      setError(message);
      props.onStatus(message);
      setSaving(false);
      return;
    }
    setNoteText("");
    props.onStatus("Note saved.");
    await loadNotes();
    setSaving(false);
  }

  async function removeNote(noteId: number) {
    const out = await api<{ deleted: boolean }>(
      `/api/v1/papers/${encodeURIComponent(props.paperId)}/notes/${encodeURIComponent(String(noteId))}`,
      { method: "DELETE" },
      props.csrfToken
    );
    if (!out.ok) {
      const message = out.error?.message || "Unable to delete note.";
      setError(message);
      props.onStatus(message);
      return;
    }
    props.onStatus("Note deleted.");
    await loadNotes();
  }

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>Paper Viewer</h2>
          <p className={css.caption}>Read selected PDF, jump to evidence pages, and save notes/highlight terms.</p>
        </div>
        <div className={css.controls}>
          <label>
            Page
            <input
              className={css.input}
              type="number"
              min={1}
              max={5000}
              value={page}
              onChange={(event) => setPage(Math.max(1, Number(event.target.value || 1)))}
            />
          </label>
          <button className={css.button} onClick={() => void loadNotes()} disabled={loadingNotes}>
            {loadingNotes ? <Spinner label="Loading" small /> : "Reload Notes"}
          </button>
        </div>
      </header>

      <p className={css.paper}>{props.paperTitle || "Selected paper"}</p>
      {error && <div className={css.error}>{error}</div>}

      <div className={css.viewerWrap}>
        <iframe title="Paper PDF Viewer" src={pdfUrl} className={css.viewer} />
      </div>

      <section className={css.noteComposer}>
        <h3>Add Note</h3>
        <div className={css.row}>
          <label className={css.label}>
            Highlight text (optional)
            <input className={css.input} value={highlightText} onChange={(event) => setHighlightText(event.target.value)} />
          </label>
          <label className={css.label}>
            Highlight terms (comma separated)
            <input
              className={css.input}
              value={highlightTermsInput}
              onChange={(event) => setHighlightTermsInput(event.target.value)}
              placeholder="VAR, impulse response, local projection"
            />
          </label>
          <label className={css.label}>
            Color
            <input className={css.colorInput} type="color" value={color} onChange={(event) => setColor(event.target.value)} />
          </label>
        </div>
        <label className={css.label}>
          Note
          <textarea className={css.textarea} rows={3} value={noteText} onChange={(event) => setNoteText(event.target.value)} />
        </label>
        <button className={css.buttonPrimary} onClick={() => void addNote()} disabled={saving || !noteText.trim()}>
          {saving ? <Spinner label="Saving" small /> : "Save Note"}
        </button>
      </section>

      <section className={css.notesList}>
        <h3>Notes on Page {page}</h3>
        {loadingNotes ? (
          <Spinner label="Loading notes..." />
        ) : notes.length === 0 ? (
          <p className={css.empty}>No notes on this page.</p>
        ) : (
          <ul className={css.list}>
            {notes.map((item) => (
              <li key={item.id} className={css.noteCard}>
                <div className={css.noteMeta}>
                  <span>Page {item.page_number || "n/a"}</span>
                  <span>{item.created_at?.replace("T", " ").slice(0, 19) || ""}</span>
                </div>
                {item.highlight_text && <p className={css.highlight}>Highlight: {item.highlight_text}</p>}
                {Array.isArray(item.highlight_terms) && item.highlight_terms.length > 0 && (
                  <p className={css.terms}>Terms: {item.highlight_terms.join(", ")}</p>
                )}
                <p>{item.note_text}</p>
                <button className={css.deleteButton} onClick={() => void removeNote(item.id)}>
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
