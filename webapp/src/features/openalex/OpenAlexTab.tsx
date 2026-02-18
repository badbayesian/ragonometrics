import { useEffect, useState } from "react";
import { api } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import css from "./OpenAlexTab.module.css";

type OpenAlexPayload = {
  available: boolean;
  message?: string;
  work?: {
    id: string;
    openalex_url: string;
    title: string;
    publication_year: number | null;
    doi: string;
    cited_by_count: number | null;
    referenced_works_count: number | null;
    venue: string;
    landing_url: string;
    authors: string[];
    abstract?: string;
    doi_url?: string;
    author_items?: Array<{ name: string; id: string; openalex_url: string }>;
    source?: { name: string; id: string; openalex_url: string };
    host_venue?: { name: string; id: string; openalex_url: string };
    topics?: Array<{ name: string; id: string; openalex_url: string; score?: number }>;
    concepts?: Array<{ name: string; id: string; openalex_url: string; score?: number }>;
  };
  raw?: Record<string, unknown>;
};

type Props = {
  csrfToken: string;
  paperId: string;
  onStatus: (text: string) => void;
};

function linkOrText(value: string, label = "Open") {
  const clean = String(value || "").trim();
  if (!clean) return <span>n/a</span>;
  return (
    <a href={clean} target="_blank" rel="noreferrer" className={css.link}>
      {label}
    </a>
  );
}

export function OpenAlexTab(props: Props) {
  const [data, setData] = useState<OpenAlexPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState("");
  const [manualOpenAlexUrl, setManualOpenAlexUrl] = useState("");

  async function load() {
    if (!props.paperId) return;
    setLoading(true);
    setError("");
    const out = await api<OpenAlexPayload>(`/api/v1/openalex/metadata?paper_id=${encodeURIComponent(props.paperId)}`);
    if (!out.ok || !out.data) {
      const message = out.error?.message || "OpenAlex metadata request failed.";
      setError(message);
      props.onStatus(message);
      setLoading(false);
      return;
    }
    setData(out.data);
    setLoading(false);
  }

  async function applyManualOpenAlexLink() {
    const url = String(manualOpenAlexUrl || "").trim();
    if (!url) {
      const message = "Enter an OpenAlex API URL first.";
      setError(message);
      props.onStatus(message);
      return;
    }
    setLinking(true);
    setError("");
    const out = await api<{ openalex_id: string; openalex_title: string; aliases_updated: number }>(
      "/api/v1/openalex/metadata/manual-link",
      {
        method: "POST",
        body: JSON.stringify({
          paper_id: props.paperId,
          openalex_api_url: url,
        }),
      },
      props.csrfToken,
    );
    if (!out.ok || !out.data) {
      const message = out.error?.message || "Manual OpenAlex link failed.";
      setError(message);
      props.onStatus(message);
      setLinking(false);
      return;
    }
    props.onStatus(
      `Manual OpenAlex link saved (${out.data.openalex_id || "n/a"}; aliases updated=${Number(out.data.aliases_updated || 0)}).`,
    );
    await load();
    setLinking(false);
  }

  useEffect(() => {
    void load();
  }, [props.paperId]);

  const work = data?.work || {
    id: "",
    openalex_url: "",
    title: "",
    publication_year: null,
    doi: "",
    cited_by_count: null,
    referenced_works_count: null,
    venue: "",
    landing_url: "",
    authors: [],
  };

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>OpenAlex Metadata</h2>
          <p className={css.caption}>Canonical metadata enrichment for the selected paper.</p>
        </div>
        <button className={css.button} onClick={() => void load()} disabled={loading || linking}>
          {loading ? <Spinner label="Loading" small /> : "Refresh"}
        </button>
      </header>

      <section className={css.manualLinkCard}>
        <h3>Manual OpenAlex Link</h3>
        <p className={css.caption}>
          If auto-match is missing or wrong, paste an OpenAlex API/canonical URL and save it for this paper.
        </p>
        <div className={css.manualLinkRow}>
          <input
            className={css.manualInput}
            value={manualOpenAlexUrl}
            onChange={(e) => setManualOpenAlexUrl(e.target.value)}
            placeholder="https://api.openalex.org/w2914218338"
          />
          <button className={css.button} onClick={() => void applyManualOpenAlexLink()} disabled={loading || linking}>
            {linking ? <Spinner label="Saving" small /> : "Apply Link"}
          </button>
        </div>
      </section>

      {error && <div className={css.error}>{error}</div>}

      {!data ? (
        <Spinner label="Loading metadata..." />
      ) : !data.available ? (
        <p className={css.empty}>{data.message || "No OpenAlex metadata found for this paper."}</p>
      ) : (
        <>
          <div className={css.metrics}>
            <article className={css.metricCard}>
              <div className={css.metricLabel}>Title</div>
              <div className={css.metricValue}>{work.title || "n/a"}</div>
            </article>
            <article className={css.metricCard}>
              <div className={css.metricLabel}>Venue</div>
              <div className={css.metricValue}>{work.venue || "n/a"}</div>
            </article>
            <article className={css.metricCard}>
              <div className={css.metricLabel}>Publication Year</div>
              <div className={css.metricValue}>{work.publication_year ?? "n/a"}</div>
            </article>
            <article className={css.metricCard}>
              <div className={css.metricLabel}>Cited By</div>
              <div className={css.metricValue}>{work.cited_by_count ?? "n/a"}</div>
            </article>
            <article className={css.metricCard}>
              <div className={css.metricLabel}>Referenced Works</div>
              <div className={css.metricValue}>{work.referenced_works_count ?? "n/a"}</div>
            </article>
          </div>

          <div className={css.detailsGrid}>
            <article className={css.detailCard}>
              <h3>Identifiers</h3>
              <dl>
                <dt>OpenAlex ID</dt>
                <dd>{work.openalex_url ? linkOrText(work.openalex_url, work.id || "OpenAlex") : work.id || "n/a"}</dd>
                <dt>OpenAlex URL</dt>
                <dd>{linkOrText(work.openalex_url, "Open on OpenAlex")}</dd>
                <dt>DOI</dt>
                <dd>{work.doi_url ? linkOrText(work.doi_url, work.doi || "Open DOI") : work.doi || "n/a"}</dd>
                <dt>Landing URL</dt>
                <dd>{linkOrText(work.landing_url, "Open landing page")}</dd>
                <dt>Source / Journal</dt>
                <dd>
                  {work.source?.openalex_url
                    ? linkOrText(work.source.openalex_url, work.source.name || "Open source")
                    : work.source?.name || work.host_venue?.name || "n/a"}
                </dd>
              </dl>
            </article>

            <article className={css.detailCard}>
              <h3>Authors</h3>
              {Array.isArray(work.author_items) && work.author_items.length > 0 ? (
                <div className={css.authorWrap}>
                  {work.author_items.map((author) => (
                    <a
                      key={`${author.id || author.name}`}
                      href={author.openalex_url || "#"}
                      target="_blank"
                      rel="noreferrer"
                      className={css.authorChip}
                    >
                      {author.name}
                    </a>
                  ))}
                </div>
              ) : (
                <p className={css.empty}>No authors returned.</p>
              )}
            </article>
          </div>

          {work.abstract && (
            <article className={css.detailCard}>
              <h3>Abstract</h3>
              <p>{work.abstract}</p>
            </article>
          )}

          <div className={css.detailsGrid}>
            <article className={css.detailCard}>
              <h3>Topics</h3>
              {Array.isArray(work.topics) && work.topics.length > 0 ? (
                <div className={css.authorWrap}>
                  {work.topics.map((topic) => (
                    <a key={topic.id || topic.name} href={topic.openalex_url} target="_blank" rel="noreferrer" className={css.authorChip}>
                      {topic.name || topic.id}
                    </a>
                  ))}
                </div>
              ) : (
                <p className={css.empty}>No topics available.</p>
              )}
            </article>
            <article className={css.detailCard}>
              <h3>Concepts</h3>
              {Array.isArray(work.concepts) && work.concepts.length > 0 ? (
                <div className={css.authorWrap}>
                  {work.concepts.map((concept) => (
                    <a
                      key={concept.id || concept.name}
                      href={concept.openalex_url}
                      target="_blank"
                      rel="noreferrer"
                      className={css.authorChip}
                    >
                      {concept.name || concept.id}
                    </a>
                  ))}
                </div>
              ) : (
                <p className={css.empty}>No concepts available.</p>
              )}
            </article>
          </div>

          <details className={css.rawPanel}>
            <summary>Raw OpenAlex JSON</summary>
            <pre>{JSON.stringify(data.raw || {}, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
