import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../shared/api";
import { Spinner } from "../../shared/Spinner";
import css from "./CitationNetworkTab.module.css";

const DEFAULT_MAX_REFERENCES = 10;
const DEFAULT_MAX_CITING = 10;
const DEFAULT_HOPS = 1;
const AUTO_REFRESH_DELAY_MS = 2000;

type WorkRow = {
  id: string;
  openalex_url: string;
  title: string;
  publication_year?: number | null;
  doi?: string;
  cited_by_count?: number;
  group?: string;
  hop?: number;
};

type GraphEdge = {
  from: string;
  to: string;
  relation?: string;
  hop?: number;
};

type GraphPayload = {
  nodes: WorkRow[];
  edges: GraphEdge[];
  n_hops?: number;
  node_count?: number;
  edge_count?: number;
};

type NetworkCache = {
  status?: string;
  cache_key?: string;
  generated_at?: string;
  expires_at?: string;
  stale_until?: string;
  refresh_enqueued?: boolean;
};

type NetworkPayload = {
  available: boolean;
  message?: string;
  center: WorkRow | null;
  references: WorkRow[];
  citing: WorkRow[];
  graph?: GraphPayload;
  cache?: NetworkCache;
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
  const start = -320;
  const end = 320;
  return start + (index * (end - start)) / (total - 1);
}

function relationForEdge(value: unknown): "references" | "cites" {
  const text = String(value || "").trim().toLowerCase();
  if (text === "cites") return "cites";
  return "references";
}

function edgeColor(relation: "references" | "cites"): string {
  return relation === "cites" ? "#2f9b88" : "#c64f39";
}

function edgeTitle(
  relation: "references" | "cites",
  hop: unknown,
  fromLabel: string,
  toLabel: string
): string {
  const h = Number(hop || 1);
  const hopText = Number.isFinite(h) ? h : 1;
  if (relation === "cites") {
    return `Was cited chain (hop ${hopText}): ${fromLabel} was cited by ${toLabel}`;
  }
  return `Cites chain (hop ${hopText}): ${toLabel} cites ${fromLabel}`;
}

export function CitationNetworkTab(props: Props) {
  const [maxReferences, setMaxReferences] = useState(DEFAULT_MAX_REFERENCES);
  const [maxCiting, setMaxCiting] = useState(DEFAULT_MAX_CITING);
  const [nHops, setNHops] = useState(DEFAULT_HOPS);
  const [data, setData] = useState<NetworkPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [graphError, setGraphError] = useState("");
  const graphRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<any>(null);
  const centerNodeIdRef = useRef<string>("");
  const autoRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestSeqRef = useRef(0);
  const prevPaperIdRef = useRef<string>("");
  const suppressNextAutoRef = useRef(false);

  function clearAutoRefreshTimer() {
    if (autoRefreshTimerRef.current) {
      clearTimeout(autoRefreshTimerRef.current);
      autoRefreshTimerRef.current = null;
    }
  }

  async function load(
    opts?: {
      maxReferences?: number;
      maxCiting?: number;
      nHops?: number;
      reason?: string;
    }
  ) {
    if (!props.paperId) return;
    const effectiveMaxReferences = Math.max(
      1,
      Math.min(50, Number((opts?.maxReferences ?? maxReferences) || DEFAULT_MAX_REFERENCES))
    );
    const effectiveMaxCiting = Math.max(
      1,
      Math.min(50, Number((opts?.maxCiting ?? maxCiting) || DEFAULT_MAX_CITING))
    );
    const effectiveHops = Math.max(1, Math.min(5, Number((opts?.nHops ?? nHops) || DEFAULT_HOPS)));
    const seq = requestSeqRef.current + 1;
    requestSeqRef.current = seq;
    setLoading(true);
    setError("");
    const path =
      `/api/v1/openalex/citation-network?paper_id=${encodeURIComponent(props.paperId)}` +
      `&max_references=${encodeURIComponent(String(effectiveMaxReferences))}` +
      `&max_citing=${encodeURIComponent(String(effectiveMaxCiting))}` +
      `&n_hops=${encodeURIComponent(String(effectiveHops))}`;
    const out = await api<NetworkPayload>(path);
    if (seq !== requestSeqRef.current) {
      return;
    }
    if (!out.ok || !out.data) {
      const message = out.error?.message || "OpenAlex citation network request failed.";
      setError(message);
      props.onStatus(message);
      setLoading(false);
      return;
    }
    setData(out.data);
    const cacheStatus = String(out.data.cache?.status || "n/a");
    const reason = String(opts?.reason || "reload");
    props.onStatus(`Citation network loaded (${reason}; cache=${cacheStatus}).`);
    setLoading(false);
  }

  function resetControlsAndView() {
    clearAutoRefreshTimer();
    suppressNextAutoRef.current = true;
    setMaxReferences(DEFAULT_MAX_REFERENCES);
    setMaxCiting(DEFAULT_MAX_CITING);
    setNHops(DEFAULT_HOPS);
    if (networkRef.current && typeof networkRef.current.fit === "function") {
      networkRef.current.fit({ animation: true });
    }
    if (networkRef.current && typeof networkRef.current.focus === "function" && centerNodeIdRef.current) {
      networkRef.current.focus(centerNodeIdRef.current, { scale: 1.0, animation: true });
    }
    void load({
      maxReferences: DEFAULT_MAX_REFERENCES,
      maxCiting: DEFAULT_MAX_CITING,
      nHops: DEFAULT_HOPS,
      reason: "reset",
    });
  }

  useEffect(() => {
    if (!props.paperId) return;
    const paperChanged = prevPaperIdRef.current !== props.paperId;
    prevPaperIdRef.current = props.paperId;
    clearAutoRefreshTimer();
    if (paperChanged) {
      void load({ reason: "paper-change" });
      return;
    }
    if (suppressNextAutoRef.current) {
      suppressNextAutoRef.current = false;
      return;
    }
    autoRefreshTimerRef.current = setTimeout(() => {
      void load({ reason: "auto" });
    }, AUTO_REFRESH_DELAY_MS);
    return () => {
      clearAutoRefreshTimer();
    };
  }, [props.paperId, maxReferences, maxCiting, nHops]);

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

        const rawGraphNodes = Array.isArray(data.graph?.nodes) ? data.graph?.nodes || [] : [];
        const rawGraphEdges = Array.isArray(data.graph?.edges) ? data.graph?.edges || [] : [];

        let nodes: any[] = [];
        let edges: any[] = [];
        if (rawGraphNodes.length > 0) {
          const idMap = new Map<string, string>();
          const sideMap = new Map<string, "left" | "right" | "center">();
          const centerCanonicalId = String(data.center?.id || "").trim();
          if (centerCanonicalId) {
            sideMap.set(centerCanonicalId, "center");
          }
          for (const edge of rawGraphEdges) {
            const relation = relationForEdge(edge.relation);
            const fromCanon = String(edge.from || "").trim();
            const toCanon = String(edge.to || "").trim();
            if (!fromCanon || !toCanon) continue;
            if (relation === "references") {
              if (!sideMap.has(toCanon)) sideMap.set(toCanon, "left");
              if (!sideMap.has(fromCanon) && fromCanon !== centerCanonicalId) sideMap.set(fromCanon, "right");
            } else {
              if (!sideMap.has(fromCanon)) sideMap.set(fromCanon, "right");
              if (!sideMap.has(toCanon) && toCanon !== centerCanonicalId) sideMap.set(toCanon, "left");
            }
          }
          const laneBuckets = new Map<string, string[]>();
          const sortedRawNodes = [...rawGraphNodes].sort((a, b) =>
            String(a.title || "").localeCompare(String(b.title || ""), undefined, { sensitivity: "base" })
          );
          for (const row of sortedRawNodes) {
            const canonical = String(row.id || "").trim();
            const hop = Math.max(0, Number(row.hop || 0));
            let side = sideMap.get(canonical);
            if (!side) {
              side = row.group === "reference" ? "left" : row.group === "citing" ? "right" : "right";
            }
            const laneKey = `${side}:${hop}`;
            const list = laneBuckets.get(laneKey) || [];
            list.push(canonical);
            laneBuckets.set(laneKey, list);
          }

          nodes = sortedRawNodes.map((row, idx) => {
            const key = workKey(row, idx, "node");
            const canonical = String(row.id || "").trim();
            idMap.set(canonical, key);
            const hop = Math.max(0, Number(row.hop || 0));
            const side = sideMap.get(canonical) || (row.group === "reference" ? "left" : row.group === "citing" ? "right" : "right");
            const laneKey = `${side}:${hop}`;
            const laneItems = laneBuckets.get(laneKey) || [canonical];
            const laneIndex = Math.max(0, laneItems.indexOf(canonical));
            const xBase = hop === 0 ? 0 : 280 + ((hop - 1) * 220);
            const x = side === "center" ? 0 : side === "left" ? -xBase : xBase;
            const y = side === "center" ? 0 : laneY(laneIndex, laneItems.length);
            return {
              id: key,
              label: row.title,
              title: `${row.title}\nCitations: ${row.cited_by_count || 0}\nHop: ${row.hop ?? "n/a"}`,
              size: nodeSize(row.cited_by_count),
              group: side === "center" ? "center" : side === "left" ? "reference" : "citing",
              openalex_url: row.openalex_url,
              x,
              y,
            };
          });
          const nodeLabelByRenderedId = new Map<string, string>();
          for (const node of nodes) {
            nodeLabelByRenderedId.set(String(node.id), String(node.label || ""));
          }
          edges = rawGraphEdges
            .map((edge, idx) => {
              const rawFrom = idMap.get(String(edge.from || "").trim()) || "";
              const rawTo = idMap.get(String(edge.to || "").trim()) || "";
              if (!rawFrom || !rawTo) return null;
              const from = rawTo;
              const to = rawFrom;
              const relation = relationForEdge(edge.relation);
              const fromLabel = nodeLabelByRenderedId.get(from) || String(from);
              const toLabel = nodeLabelByRenderedId.get(to) || String(to);
              return {
                id: `edge-${idx}`,
                from,
                to,
                arrows: "to",
                relation,
                title: edgeTitle(relation, edge.hop, fromLabel, toLabel),
                color: {
                  color: edgeColor(relation),
                  highlight: edgeColor(relation),
                  hover: edgeColor(relation),
                  opacity: 0.72,
                },
                width: relation === "references" ? 2.0 : 2.2,
                dashes: relation === "cites",
              };
            })
            .filter(Boolean);
        } else {
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

          nodes = [
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

          edges = [
            ...refNodes.map((row) => ({
              from: row.id,
              to: centerId,
              arrows: "to",
              relation: "references",
              title: edgeTitle("references", 1, String(row.label || row.id), String(data.center?.title || "Center")),
              color: {
                color: edgeColor("references"),
                highlight: edgeColor("references"),
                hover: edgeColor("references"),
                opacity: 0.72,
              },
              width: 2.0,
              dashes: false,
            })),
            ...citingNodes.map((row) => ({
              from: centerId,
              to: row.id,
              arrows: "to",
              relation: "cites",
              title: edgeTitle("cites", 1, String(data.center?.title || "Center"), String(row.label || row.id)),
              color: {
                color: edgeColor("cites"),
                highlight: edgeColor("cites"),
                hover: edgeColor("cites"),
                opacity: 0.72,
              },
              width: 2.2,
              dashes: true,
            })),
          ];
        }

        const network = new vis.Network(
          graphRef.current,
          { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
          {
            physics: {
              enabled: true,
              solver: "barnesHut",
              stabilization: {
                enabled: true,
                iterations: rawGraphNodes.length > 0 ? 220 : 160,
                fit: true,
                updateInterval: 25,
              },
              barnesHut: {
                gravitationalConstant: -11500,
                centralGravity: 0.08,
                springLength: rawGraphNodes.length > 0 ? 195 : 230,
                springConstant: 0.03,
                damping: 0.24,
                avoidOverlap: 0.4,
              },
              minVelocity: 0.35,
              maxVelocity: 40,
              timestep: 0.45,
            },
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
              neighborhood: { color: { background: "#7a6fb2", border: "#4d457f" } },
            },
            edges: {
              smooth:
                rawGraphNodes.length > 0
                  ? { enabled: true, type: "dynamic" }
                  : { enabled: true, type: "cubicBezier", forceDirection: "horizontal", roundness: 0.4 },
              color: { color: "rgba(76, 98, 120, 0.62)" },
            },
          }
        );

        const centerNode = nodes.find((node) => String(node.group || "") === "center");
        centerNodeIdRef.current = String(centerNode?.id || "");

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
          <p className={css.caption}>
            Colors and arrows stay consistent across hops: one color for papers this paper cites, one for papers that cite this paper.
          </p>
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
              onChange={(e) => setMaxReferences(Number(e.target.value || DEFAULT_MAX_REFERENCES))}
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
              onChange={(e) => setMaxCiting(Number(e.target.value || DEFAULT_MAX_CITING))}
            />
          </label>
          <label>
            Hops
            <input
              className={css.input}
              type="number"
              min={1}
              max={5}
              value={nHops}
              onChange={(e) => setNHops(Number(e.target.value || DEFAULT_HOPS))}
            />
          </label>
          <button
            className={css.button}
            onClick={() => {
              clearAutoRefreshTimer();
              void load({ reason: "manual" });
            }}
            disabled={loading}
          >
            {loading ? <Spinner label="Loading" small /> : "Reload"}
          </button>
          <button className={css.buttonSecondary} onClick={resetControlsAndView} disabled={loading}>
            Reset
          </button>
        </div>
      </header>
      <p className={css.autoHint}>Auto-refresh runs 2 seconds after edits.</p>

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
            <span className={css.summaryItem}>Hops: {Number(data.summary?.n_hops_requested || nHops)}</span>
            <span className={css.summaryItem}>Nodes: {Number(data.summary?.nodes_shown || data.graph?.node_count || 0)}</span>
            <span className={css.summaryItem}>Cache: {String(data.cache?.status || "n/a")}</span>
          </div>
          <div className={css.legendRow}>
            <span className={css.legendItem}>
              <span className={`${css.legendSwatch} ${css.legendReference}`} />
              Selected paper cites this paper
            </span>
            <span className={css.legendItem}>
              <span className={`${css.legendSwatch} ${css.legendCites}`} />
              Selected paper was cited by this paper
            </span>
            <span className={css.legendItem}>
              <span className={`${css.legendSwatch} ${css.legendCenter}`} />
              selected paper
            </span>
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
