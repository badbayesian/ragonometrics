import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import css from "./CitationNetworkTab.module.css";

type WorkRow = {
  id: string;
  openalex_url: string;
  title: string;
  publication_year?: number | null;
  doi?: string;
  cited_by_count?: number;
  group?: string;
};

type NetworkPayload = {
  available: boolean;
  message?: string;
  center: WorkRow | null;
  references: WorkRow[];
  citing: WorkRow[];
  summary?: Record<string, unknown>;
};

type Props = {
  paperId: string;
  onStatus: (text: string) => void;
};

function workKey(row: WorkRow, idx: number, prefix: string): string {
  const raw = String(row.id || "").trim();
  if (raw) {
    const part = raw.split("/").pop() || raw;
    return `${prefix}-${part}`;
  }
  return `${prefix}-${idx}`;
}

function nodeSize(citedByCount: number | undefined): number {
  const value = Math.max(1, Number(citedByCount || 0));
  return Math.max(16, Math.min(44, 10 + Math.sqrt(value)));
}

function laneY(index: number, total: number): number {
  if (total <= 1) return 0;
  const start = -220;
  const end = 220;
  return start + (index * (end - start)) / (total - 1);
}

export function CitationNetworkTab(props: Props) {
  const [maxReferences, setMaxReferences] = useState(10);
  const [maxCiting, setMaxCiting] = useState(10);
  const [data, setData] = useState<NetworkPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [graphError, setGraphError] = useState("");
  const graphRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<any>(null);

  async function load() {
    if (!props.paperId) return;
    setLoading(true);
    setError("");
    const path =
      `/api/v1/openalex/citation-network?paper_id=${encodeURIComponent(props.paperId)}` +
      `&max_references=${encodeURIComponent(String(maxReferences))}` +
      `&max_citing=${encodeURIComponent(String(maxCiting))}`;
    const out = await api<NetworkPayload>(path);
    if (!out.ok || !out.data) {
      const message = out.error?.message || "OpenAlex citation network request failed.";
      setError(message);
      props.onStatus(message);
      setLoading(false);
      return;
    }
    setData(out.data);
    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, [props.paperId]);

  useEffect(() => {
    if (!data || !data.available || !data.center || !graphRef.current) return;
    let cancelled = false;

    async function renderGraph() {
      setGraphError("");
      try {
        if (networkRef.current && typeof networkRef.current.destroy === "function") {
          networkRef.current.destroy();
          networkRef.current = null;
        }
        const vis: any = await import("vis-network/standalone");
        if (cancelled || !graphRef.current) return;

        const centerId = workKey(data.center, 0, "center");
        const refNodes = data.references.map((row, idx) => ({
          id: workKey(row, idx, "ref"),
          label: row.title,
          title: `${row.title}\nCitations: ${row.cited_by_count || 0}`,
          x: -520,
          y: laneY(idx, data.references.length),
          size: nodeSize(row.cited_by_count),
          group: "reference",
          openalex_url: row.openalex_url,
        }));
        const citingNodes = data.citing.map((row, idx) => ({
          id: workKey(row, idx, "citing"),
          label: row.title,
          title: `${row.title}\nCitations: ${row.cited_by_count || 0}`,
          x: 520,
          y: laneY(idx, data.citing.length),
          size: nodeSize(row.cited_by_count),
          group: "citing",
          openalex_url: row.openalex_url,
        }));

        const nodes = [
          {
            id: centerId,
            label: data.center.title,
            title: `${data.center.title}\nCitations: ${data.center.cited_by_count || 0}`,
            x: 0,
            y: 0,
            size: nodeSize(data.center.cited_by_count),
            group: "center",
            openalex_url: data.center.openalex_url,
          },
          ...refNodes,
          ...citingNodes,
        ];

        const edges = [
          ...refNodes.map((row) => ({ from: centerId, to: row.id, arrows: "to" })),
          ...citingNodes.map((row) => ({ from: row.id, to: centerId, arrows: "to" })),
        ];

        const network = new vis.Network(
          graphRef.current,
          { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
          {
            physics: false,
            interaction: { dragNodes: true, dragView: true, zoomView: true, hover: true },
            nodes: {
              shape: "dot",
              font: { size: 12, color: "#1b2733" },
              borderWidth: 1,
            },
            groups: {
              center: { color: { background: "#1f6fb2", border: "#0f4d7e" } },
              reference: { color: { background: "#c64f39", border: "#8a3324" } },
              citing: { color: { background: "#2f9b88", border: "#1f6f61" } },
            },
            edges: {
              smooth: { enabled: true, type: "cubicBezier", forceDirection: "horizontal", roundness: 0.4 },
              color: { color: "rgba(76, 98, 120, 0.45)" },
            },
          }
        );

        network.on("click", (params: any) => {
          if (!params.nodes || params.nodes.length === 0) return;
          const selected = nodes.find((node) => node.id === params.nodes[0]);
          if (!selected || !selected.openalex_url) return;
          window.open(String(selected.openalex_url), "_blank", "noopener,noreferrer");
        });
        networkRef.current = network;
      } catch (exc) {
        setGraphError(`Graph render unavailable: ${String(exc || "unknown error")}`);
      }
    }

    void renderGraph();
    return () => {
      cancelled = true;
      if (networkRef.current && typeof networkRef.current.destroy === "function") {
        networkRef.current.destroy();
        networkRef.current = null;
      }
    };
  }, [data]);

  const centerSummary = useMemo(() => {
    if (!data?.center) return "";
    return `${data.center.title} (${data.center.publication_year || "n/a"})`; 
  }, [data]);

  return (
    <section className={css.card}>
      <header className={css.header}>
        <div>
          <h2>Citation Network</h2>
          <p className={css.caption}>Interactive graph with references on the left and citing papers on the right.</p>
        </div>
        <div className={css.controls}>
          <label>
            Max references
            <input
              className={css.input}
              type="number"
              min={1}
              max={50}
              value={maxReferences}
              onChange={(e) => setMaxReferences(Number(e.target.value || 10))}
            />
          </label>
          <label>
            Max citing
            <input
              className={css.input}
              type="number"
              min={1}
              max={50}
              value={maxCiting}
              onChange={(e) => setMaxCiting(Number(e.target.value || 10))}
            />
          </label>
          <button className={css.button} onClick={() => void load()} disabled={loading}>
            {loading ? <Spinner label="Loading" small /> : "Reload"}
          </button>
        </div>
      </header>

      {error && <div className={css.error}>{error}</div>}

      {!data ? (
        <Spinner label="Loading network..." />
      ) : !data.available ? (
        <p className={css.empty}>{data.message || "No citation network available."}</p>
      ) : (
        <>
          <div className={css.summaryRow}>
            <span className={css.summaryItem}>Center: {centerSummary}</span>
            <span className={css.summaryItem}>References: {data.references.length}</span>
            <span className={css.summaryItem}>Citing: {data.citing.length}</span>
          </div>

          <div className={css.graphShell}>
            <div ref={graphRef} className={css.graph} />
            {graphError && <div className={css.graphError}>{graphError}</div>}
          </div>

          <div className={css.tableGrid}>
            <section className={css.tableCard}>
              <h3>References</h3>
              <ul className={css.list}>
                {data.references.map((row, idx) => (
                  <li key={workKey(row, idx, "ref-row")}>
                    <a href={row.openalex_url} target="_blank" rel="noreferrer">
                      {row.title}
                    </a>{" "}
                    ({row.publication_year || "n/a"})
                  </li>
                ))}
              </ul>
            </section>
            <section className={css.tableCard}>
              <h3>Citing</h3>
              <ul className={css.list}>
                {data.citing.map((row, idx) => (
                  <li key={workKey(row, idx, "citing-row")}>
                    <a href={row.openalex_url} target="_blank" rel="noreferrer">
                      {row.title}
                    </a>{" "}
                    ({row.publication_year || "n/a"})
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <details className={css.rawPanel}>
            <summary>Network JSON</summary>
            <pre>{JSON.stringify(data || {}, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
