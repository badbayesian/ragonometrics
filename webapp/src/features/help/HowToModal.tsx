import { useEffect } from "react";
import css from "./HowToModal.module.css";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function HowToModal(props: Props) {
  useEffect(() => {
    if (!props.open) return undefined;

    const previousOverflow = document.body.style.overflow;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      props.onClose();
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [props.open, props.onClose]);

  if (!props.open) return null;

  return (
    <div
      className={css.overlay}
      data-testid="howto-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          props.onClose();
        }
      }}
    >
      <section className={css.modal} role="dialog" aria-modal="true" aria-labelledby="howto-title">
        <header className={css.header}>
          <h2 id="howto-title">How To Use Ragonometrics Web</h2>
          <button type="button" className={css.closeButton} onClick={props.onClose} aria-label="Close how to popup">
            Close
          </button>
        </header>

        <div className={css.body}>
          <section className={css.section}>
            <h3>Quick Start</h3>
            <ol>
              <li>Log in with your account.</li>
              <li>Use Find paper to pick a paper title (falls back to file name when title is unavailable).</li>
              <li>Use Author, Venue, and Year filter boxes to narrow the paper title dropdown.</li>
              <li>Double-click inside Find paper to open the dropdown for the current filtered set.</li>
              <li>Ask a question in Chat, or use Ask (Stream) for incremental output.</li>
              <li>Use other tabs to extract structured answers, inspect metadata, and review usage.</li>
            </ol>
          </section>

          <section className={css.section}>
            <h3>Tab Guide</h3>
            <ul>
              <li>Chat: Ask, stream, clear history, and use suggested prompts.</li>
              <li>Paper Viewer: View pages, highlights, and notes tied to evidence.</li>
              <li>Structured Workstream: Refresh cache, generate missing answers, and export JSON/PDF.</li>
              <li>Compare: Select up to 10 papers, build a cache-first matrix, then fill missing cells.</li>
              <li>OpenAlex Metadata: Refresh metadata, inspect linked entities, and use Manual OpenAlex Link when auto-match fails.</li>
              <li>Citation Network: Adjust limits, reload graph, and click nodes for OpenAlex pages.</li>
              <li>Usage: Toggle session scope and refresh summary, model, and recent rows.</li>
            </ul>
          </section>

          <section className={css.section}>
            <h3>Tips</h3>
            <ul>
              <li>Keep one selected paper at a time to avoid scope confusion.</li>
              <li>If a paper is missing from the picker, clear Author/Venue/Year filters first.</li>
              <li>Use stream mode for long responses.</li>
              <li>Check the status bar for success and error messages after actions.</li>
            </ul>
          </section>
        </div>
      </section>
    </div>
  );
}
