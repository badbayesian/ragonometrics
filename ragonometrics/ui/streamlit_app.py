"""Streamlit UI for interactive RAG over papers with citations and usage tracking."""

from __future__ import annotations

import os
import time
import math
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from dataclasses import replace
import hashlib
import html
import re
from typing import Any, Callable, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI, BadRequestError

from ragonometrics.db.connection import connect, pooled_connection
from ragonometrics.core.main import (
    Settings,
    Paper,
    embed_texts,
    load_papers,
    load_settings,
    prepare_chunks_for_paper,
    top_k_context,
)
from ragonometrics.integrations.openalex import format_openalex_context, request_json as openalex_request_json
from ragonometrics.integrations.citec import format_citec_context
from ragonometrics.core.prompts import MATH_LATEX_REVIEW_PROMPT, RESEARCHER_QA_PROMPT
from ragonometrics.pipeline.query_cache import DEFAULT_CACHE_PATH, get_cached_answer, make_cache_key, set_cached_answer
from ragonometrics.pipeline.token_usage import DEFAULT_USAGE_DB, get_recent_usage, get_usage_by_model, get_usage_summary, record_usage

try:
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None

try:
    import pytesseract
    from PIL import ImageDraw
except Exception:
    pytesseract = None
    ImageDraw = None


st.set_page_config(page_title="Ragonometrics Chat", layout="wide")

_QUERY_TIMINGS_KEY = "ui_query_timings"


def list_papers(papers_dir: Path) -> List[Path]:
    """List PDF files in the provided directory.

    Args:
        papers_dir (Path): Description.

    Returns:
        List[Path]: Description.
    """
    if not papers_dir.exists():
        return []
    return sorted(papers_dir.glob("*.pdf"))


def _reset_query_timings() -> None:
    """Reset per-render query timing rows."""
    st.session_state[_QUERY_TIMINGS_KEY] = []


def _record_query_timing(label: str, elapsed_ms: float) -> None:
    """Append one query timing row for optional debug rendering.

    Args:
        label (str): Short query label.
        elapsed_ms (float): Elapsed milliseconds for the query call.
    """
    rows = st.session_state.setdefault(_QUERY_TIMINGS_KEY, [])
    rows.append({"query": label, "elapsed_ms": round(float(elapsed_ms), 2)})


def _timed_call(label: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute a callable and record elapsed time in session state.

    Args:
        label (str): Short query label shown in debug timings.
        fn (Callable[..., Any]): Callable to execute.
        *args (Any): Positional args for ``fn``.
        **kwargs (Any): Keyword args for ``fn``.

    Returns:
        Any: Callable return value.
    """
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    _record_query_timing(label, (time.perf_counter() - start) * 1000.0)
    return result


@st.cache_data(ttl=900, show_spinner=False)
def _db_openalex_metadata_for_paper(path: Path) -> Optional[Dict[str, Any]]:
    """Load OpenAlex metadata for a paper from Postgres enrichment table.

    Args:
        path (Path): Paper path selected in the UI.

    Returns:
        Optional[Dict[str, Any]]: Stored OpenAlex payload when available.
    """
    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return None

    normalized_path = str(path).replace("\\", "/")
    basename_suffix = f"%/{path.name.lower()}"
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT openalex_json
                FROM enrichment.paper_openalex_metadata
                WHERE match_status = 'matched'
                  AND (
                        lower(replace(paper_path, '\\', '/')) = lower(%s)
                     OR lower(replace(paper_path, '\\', '/')) LIKE %s
                  )
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (normalized_path, basename_suffix),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[0]
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    return None
                return parsed if isinstance(parsed, dict) else None
            return None
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def load_and_prepare(path: Path, settings: Settings):
    """Load a paper, prepare chunks/embeddings, and cache the result.

    Args:
        path (Path): Description.
        settings (Settings): Description.

    Returns:
        Any: Description.
    """
    papers = load_papers([path])
    paper = papers[0]
    if not isinstance(paper.openalex, dict) or not paper.openalex:
        db_openalex = _db_openalex_metadata_for_paper(path)
        if isinstance(db_openalex, dict) and db_openalex:
            paper = replace(paper, openalex=db_openalex)
    chunks = prepare_chunks_for_paper(paper, settings)
    if not chunks:
        return paper, [], []
    client = OpenAI()
    chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
    embeddings = embed_texts(client, chunk_texts, settings.embedding_model, settings.batch_size)
    return paper, chunks, embeddings


def parse_context_chunks(context: str) -> List[dict]:
    """Parse concatenated context into structured chunks.

    Args:
        context (str): Description.

    Returns:
        List[dict]: Description.
    """
    chunks: List[dict] = []
    for block in context.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        meta = None
        text = block
        page: Optional[int] = None
        if block.startswith("(page "):
            parts = block.split("\n", 1)
            meta = parts[0].strip()
            text = parts[1].strip() if len(parts) > 1 else ""
            m = re.search(r"\(page\s+(\d+)\b", meta)
            if m:
                try:
                    page = int(m.group(1))
                except ValueError:
                    page = None
        chunks.append({"meta": meta, "text": text, "page": page})
    return chunks


def build_chat_history_context(history: List[dict], *, paper_path: Path, max_turns: int = 6, max_answer_chars: int = 800) -> str:
    """Build a compact conversation transcript for prompt grounding.

    Args:
        history (List[dict]): Description.
        paper_path (Path): Description.
        max_turns (int): Description.
        max_answer_chars (int): Description.

    Returns:
        str: Description.
    """
    turns: List[tuple[str, str]] = []
    for item in history:
        if isinstance(item, tuple):
            if len(item) >= 2:
                q = str(item[0] or "").strip()
                a = str(item[1] or "").strip()
                if q and a:
                    turns.append((q, a))
            continue

        if not isinstance(item, dict):
            continue
        item_paper_path = item.get("paper_path")
        if item_paper_path and Path(item_paper_path) != paper_path:
            continue
        q = str(item.get("query") or "").strip()
        a = str(item.get("answer") or "").strip()
        if not q or not a:
            continue
        turns.append((q, a))

    if not turns:
        return ""

    selected = turns[-max_turns:]
    lines: List[str] = []
    for idx, (q, a) in enumerate(selected, start=1):
        answer_excerpt = a if len(a) <= max_answer_chars else a[:max_answer_chars].rstrip() + "..."
        lines.append(f"User {idx}: {q}")
        lines.append(f"Assistant {idx}: {answer_excerpt}")
    return "\n".join(lines)


def suggested_paper_questions(paper: Paper) -> List[str]:
    """Return a concise list of starter questions for the selected paper.

    Args:
        paper (Paper): Description.

    Returns:
        List[str]: Description.
    """
    questions = [
        "What is the main research question of this paper?",
        "What identification strategy does the paper use?",
        "What dataset and sample period are used?",
        "What are the key quantitative findings?",
        "What are the main limitations and caveats?",
        "What policy implications follow from the results?",
    ]
    if paper.title:
        questions[0] = f'What is the main research question in "{paper.title}"?'
    return questions


def _openalex_author_names(meta: Optional[dict]) -> List[str]:
    """Extract author display names from an OpenAlex metadata payload.

    Args:
        meta (Optional[dict]): OpenAlex work payload.

    Returns:
        List[str]: Ordered author display names.
    """
    if not isinstance(meta, dict):
        return []
    names: List[str] = []
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if name:
            names.append(name)
    return names


def _openalex_venue(meta: Optional[dict]) -> str:
    """Return a best-effort venue name from OpenAlex metadata.

    Args:
        meta (Optional[dict]): OpenAlex work payload.

    Returns:
        str: Venue name when present; otherwise empty string.
    """
    if not isinstance(meta, dict):
        return ""
    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = str(source.get("display_name") or "").strip()
    if venue:
        return venue
    host = meta.get("host_venue") or {}
    return str(host.get("display_name") or "").strip()


def render_openalex_metadata_tab(paper: Paper) -> None:
    """Render the OpenAlex metadata tab for the selected paper.

    Args:
        paper (Paper): Selected paper object.
    """
    st.subheader("OpenAlex Metadata")
    st.caption("Metadata shown below is the raw OpenAlex payload associated with this paper.")
    meta = paper.openalex
    if not isinstance(meta, dict) or not meta:
        st.info("No OpenAlex metadata found for this paper.")
        return

    id_value = str(meta.get("id") or "")
    title_value = str(meta.get("display_name") or meta.get("title") or "")
    year_value = meta.get("publication_year")
    doi_value = str(meta.get("doi") or "")
    cited_by_value = meta.get("cited_by_count")
    refs_value = meta.get("referenced_works_count")
    venue_value = _openalex_venue(meta)
    primary = meta.get("primary_location") or {}
    landing_url = str(primary.get("landing_page_url") or "")
    authors = _openalex_author_names(meta)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**OpenAlex ID:** `{id_value or 'n/a'}`")
        st.markdown(f"**Title:** {title_value or 'n/a'}")
        st.markdown(f"**Publication Year:** {year_value if year_value is not None else 'n/a'}")
        st.markdown(f"**DOI:** {doi_value or 'n/a'}")
    with col2:
        st.markdown(f"**Venue:** {venue_value or 'n/a'}")
        st.markdown(f"**Cited By Count:** {cited_by_value if cited_by_value is not None else 'n/a'}")
        st.markdown(f"**Referenced Works Count:** {refs_value if refs_value is not None else 'n/a'}")
        if landing_url:
            st.markdown(f"**Landing URL:** {landing_url}")
        else:
            st.markdown("**Landing URL:** n/a")

    st.markdown("**Authors**")
    if authors:
        for idx, author in enumerate(authors, start=1):
            st.markdown(f"{idx}. {author}")
    else:
        st.caption("No author list available in OpenAlex payload.")

    st.markdown("---")
    st.markdown("**Raw OpenAlex JSON**")
    st.json(meta, expanded=False)


def _openalex_work_id(value: Any) -> str:
    """Normalize an OpenAlex work identifier to ``W...`` format.

    Args:
        value (Any): Work id string or URL.

    Returns:
        str: OpenAlex work key (for example ``W2032518524``), or empty string.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    return text


def _openalex_work_url(value: Any) -> str:
    """Build an OpenAlex work URL from id/URL variants.

    Args:
        value (Any): OpenAlex id, API URL, or canonical URL.

    Returns:
        str: Canonical ``https://openalex.org/W...`` URL when resolvable.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    if text.startswith("https://openalex.org/"):
        return text
    if text.startswith("https://api.openalex.org/"):
        key = text.rsplit("/", 1)[-1]
        if key:
            return f"https://openalex.org/{key}"
        return ""
    key = _openalex_work_id(text)
    if key:
        return f"https://openalex.org/{key}"
    return ""


def _openalex_request_json(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Call OpenAlex and return JSON payload.

    Args:
        url (str): Endpoint URL.
        params (Optional[Dict[str, Any]]): Query parameters.
        timeout (int): Request timeout in seconds.

    Returns:
        Optional[Dict[str, Any]]: Parsed payload on success.
    """
    data = openalex_request_json(url, params=params, timeout=timeout)
    if isinstance(data, dict):
        return data
    return None


def _openalex_work_summary(work_id: str, *, include_references: bool = False) -> Optional[Dict[str, Any]]:
    """Fetch a compact OpenAlex work payload.

    Args:
        work_id (str): OpenAlex work id (``W...`` or URL).
        include_references (bool): Whether to include ``referenced_works``.

    Returns:
        Optional[Dict[str, Any]]: Work payload.
    """
    key = _openalex_work_id(work_id)
    if not key:
        return None
    fields = ["id", "display_name", "publication_year", "doi", "cited_by_count"]
    if include_references:
        fields.append("referenced_works")
    url = f"https://api.openalex.org/works/{key}"
    payload = _openalex_request_json(url, params={"select": ",".join(fields)})
    return payload if isinstance(payload, dict) else None


@st.cache_data(ttl=3600, show_spinner=False)
def _openalex_citation_network(center_work_id: str, max_references: int, max_citing: int) -> Dict[str, Any]:
    """Build OpenAlex citation neighborhood for one center work.

    Args:
        center_work_id (str): OpenAlex center work id or URL.
        max_references (int): Max number of referenced papers (left side).
        max_citing (int): Max number of citing papers (right side).

    Returns:
        Dict[str, Any]: ``center``, ``references``, and ``citing`` lists.
    """
    center = _openalex_work_summary(center_work_id, include_references=True)
    if not center:
        return {"center": None, "references": [], "citing": []}

    all_reference_ids = center.get("referenced_works") or []
    reference_candidate_cap = max(
        int(max_references),
        int(os.environ.get("OPENALEX_NETWORK_REFERENCE_CANDIDATES", "200")),
    )
    references: List[Dict[str, Any]] = []
    for ref in all_reference_ids[:reference_candidate_cap]:
        ref_summary = _openalex_work_summary(str(ref), include_references=False)
        if ref_summary:
            references.append(ref_summary)
    references.sort(key=lambda w: int(w.get("cited_by_count") or 0), reverse=True)
    references = references[: max(0, int(max_references))]

    center_key = _openalex_work_id(center.get("id"))
    citing: List[Dict[str, Any]] = []
    if center_key and max_citing > 0:
        search = _openalex_request_json(
            "https://api.openalex.org/works",
            params={
                "filter": f"cites:{center_key}",
                "per-page": int(max_citing),
                "select": "id,display_name,publication_year,doi,cited_by_count",
                "sort": "cited_by_count:desc",
            },
        )
        results = (search or {}).get("results") if isinstance(search, dict) else None
        if isinstance(results, list):
            citing = [item for item in results if isinstance(item, dict)]
    citing.sort(key=lambda w: int(w.get("cited_by_count") or 0), reverse=True)
    citing = citing[: max(0, int(max_citing))]

    return {"center": center, "references": references, "citing": citing}


def _work_title(work: Dict[str, Any]) -> str:
    """Return display title for one OpenAlex work payload.

    Args:
        work (Dict[str, Any]): OpenAlex work.

    Returns:
        str: Work title fallback.
    """
    return str(work.get("display_name") or work.get("title") or work.get("id") or "Unknown paper")


def _work_label(work: Dict[str, Any], *, max_chars: int = 80) -> str:
    """Return a short plot label for an OpenAlex work.

    Args:
        work (Dict[str, Any]): OpenAlex work.
        max_chars (int): Maximum text length.

    Returns:
        str: Label with optional year.
    """
    title = _work_title(work)
    if len(title) > max_chars:
        title = title[: max_chars - 3].rstrip() + "..."
    year = work.get("publication_year")
    return f"{title} ({year})" if year else title


def _network_ys(n: int) -> List[float]:
    """Generate vertically distributed y-coordinates for side nodes.

    Args:
        n (int): Number of nodes.

    Returns:
        List[float]: Y positions.
    """
    if n <= 0:
        return []
    if n == 1:
        return [0.0]
    top = 1.0
    bottom = -1.0
    step = (top - bottom) / (n - 1)
    return [top - idx * step for idx in range(n)]


def _citation_node_size(cited_by_count: Any) -> float:
    """Compute displayed node size from citation count.

    Args:
        cited_by_count (Any): Citation count value.

    Returns:
        float: Pixel size for the visual node marker.
    """
    try:
        value = float(cited_by_count or 0.0)
    except Exception:
        value = 0.0
    value = max(value, 1.0)
    return max(14.0, min(44.0, 8.0 + math.sqrt(value)))


def _citation_count_int(value: Any) -> int:
    """Coerce citation count into a non-negative integer.

    Args:
        value (Any): Citation count candidate.

    Returns:
        int: Non-negative citation count.
    """
    try:
        parsed = int(float(value or 0))
    except Exception:
        parsed = 0
    return max(parsed, 0)


def _sort_works_by_size_desc(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort works from largest to smallest visual node size.

    Args:
        works (List[Dict[str, Any]]): OpenAlex works.

    Returns:
        List[Dict[str, Any]]: Sorted works.
    """
    return sorted(
        works,
        key=lambda item: (
            -_citation_node_size(item.get("cited_by_count")),
            -_citation_count_int(item.get("cited_by_count")),
            _work_title(item).lower(),
        ),
    )


def _work_hover_html(work: Dict[str, Any], group_label: str) -> str:
    """Build HTML tooltip content for one work node.

    Args:
        work (Dict[str, Any]): OpenAlex work payload.
        group_label (str): Display group label.

    Returns:
        str: HTML tooltip text.
    """
    title = html.escape(_work_title(work))
    openalex_id = html.escape(str(work.get("id") or "n/a"))
    year = html.escape(str(work.get("publication_year") or "n/a"))
    cited_by = html.escape(str(work.get("cited_by_count") or 0))
    doi = html.escape(str(work.get("doi") or "n/a"))
    return (
        f"<b>{html.escape(group_label)}</b><br>"
        f"{title}<br>"
        f"Year: {year}<br>"
        f"Citations: {cited_by}<br>"
        f"DOI: {doi}<br>"
        f"<span style='font-family:monospace'>{openalex_id}</span>"
    )


def _citation_network_html(center: Dict[str, Any], references: List[Dict[str, Any]], citing: List[Dict[str, Any]]) -> str:
    """Build citation neighborhood as draggable vis-network HTML.

    Args:
        center (Dict[str, Any]): Center paper.
        references (List[Dict[str, Any]]): Referenced papers.
        citing (List[Dict[str, Any]]): Papers citing the center paper.

    Returns:
        str: HTML snippet containing a draggable network.
    """
    graph_id = f"citation-network-{uuid4().hex}"
    ref_y = _network_ys(len(references))
    cit_y = _network_ys(len(citing))
    y_scale = 300
    left_x = -580
    center_x = 0
    right_x = 580

    center_id = _openalex_work_id(center.get("id")) or "center"
    nodes: List[Dict[str, Any]] = [
        {
            "id": center_id,
            "label": _work_label(center, max_chars=56),
            "title": _work_hover_html(center, "Selected Paper"),
            "openalex_url": _openalex_work_url(center.get("id")),
            "x": center_x,
            "y": 0,
            "size": _citation_node_size(center.get("cited_by_count")),
            "group": "selected",
        }
    ]
    edges: List[Dict[str, Any]] = []

    for idx, (work, y) in enumerate(zip(references, ref_y)):
        node_id = _openalex_work_id(work.get("id")) or f"ref-{idx}"
        nodes.append(
            {
                "id": node_id,
                "label": _work_label(work, max_chars=56),
                "title": _work_hover_html(work, "Reference (cited by selected paper)"),
                "openalex_url": _openalex_work_url(work.get("id")),
                "x": left_x,
                "y": int(y * y_scale),
                "size": _citation_node_size(work.get("cited_by_count")),
                "group": "reference",
            }
        )
        edges.append({"from": center_id, "to": node_id, "arrows": "to"})

    for idx, (work, y) in enumerate(zip(citing, cit_y)):
        node_id = _openalex_work_id(work.get("id")) or f"cit-{idx}"
        nodes.append(
            {
                "id": node_id,
                "label": _work_label(work, max_chars=56),
                "title": _work_hover_html(work, "Citing paper (cites selected paper)"),
                "openalex_url": _openalex_work_url(work.get("id")),
                "x": right_x,
                "y": int(y * y_scale),
                "size": _citation_node_size(work.get("cited_by_count")),
                "group": "citing",
            }
        )
        edges.append({"from": node_id, "to": center_id, "arrows": "to"})

    payload_nodes = json.dumps(nodes)
    payload_edges = json.dumps(edges)
    return f"""
<div style="margin: 0 0 8px 0; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; font-weight: 600;">
  <div style="text-align: left; color: #C44536;">References (Left)</div>
  <div style="text-align: center; color: #2A6F97;">Selected Paper (Center)</div>
  <div style="text-align: right; color: #2A9D8F;">Cited By (Right)</div>
</div>
<div id="{graph_id}" style="width: 100%; height: 660px; border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff;"></div>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script>
  (function() {{
    const container = document.getElementById("{graph_id}");
    const nodes = new vis.DataSet({payload_nodes});
    const edges = new vis.DataSet({payload_edges});
    const data = {{ nodes, edges }};
    const options = {{
      autoResize: true,
      physics: false,
      interaction: {{
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
      }},
      nodes: {{
        shape: "dot",
        font: {{
          color: "#1f2937",
          size: 13,
          face: "Arial",
          multi: "html"
        }},
        borderWidth: 1,
        borderWidthSelected: 2
      }},
      groups: {{
        selected: {{ color: {{ background: "#2A6F97", border: "#1f4f6e" }} }},
        reference: {{ color: {{ background: "#C44536", border: "#8c2f25" }} }},
        citing: {{ color: {{ background: "#2A9D8F", border: "#1f756a" }} }}
      }},
      edges: {{
        color: {{ color: "rgba(100, 116, 139, 0.45)" }},
        smooth: {{
          enabled: true,
          type: "cubicBezier",
          forceDirection: "horizontal",
          roundness: 0.35
        }},
        arrows: {{
          to: {{
            enabled: true,
            scaleFactor: 0.45
          }}
        }}
      }}
    }};
    const network = new vis.Network(container, data, options);
    network.on("hoverNode", function(params) {{
      const node = nodes.get(params.node);
      container.style.cursor = node && node.openalex_url ? "pointer" : "default";
    }});
    network.on("blurNode", function() {{
      container.style.cursor = "default";
    }});
    network.on("click", function(params) {{
      if (!params.nodes || params.nodes.length === 0) return;
      const node = nodes.get(params.nodes[0]);
      if (!node || !node.openalex_url) return;
      window.open(node.openalex_url, "_blank", "noopener,noreferrer");
    }});
  }})();
</script>
"""


def _works_rows(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAlex works into table rows.

    Args:
        works (List[Dict[str, Any]]): OpenAlex works.

    Returns:
        List[Dict[str, Any]]: Table rows.
    """
    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(works, start=1):
        rows.append(
            {
                "rank": idx,
                "title": _work_title(item),
                "node_size": round(_citation_node_size(item.get("cited_by_count")), 2),
                "cited_by_count": _citation_count_int(item.get("cited_by_count")),
                "year": item.get("publication_year"),
                "doi": item.get("doi"),
                "openalex_id": item.get("id"),
            }
        )
    return rows


def render_openalex_citation_network_tab(paper: Paper) -> None:
    """Render interactive citation network around the selected paper.

    Args:
        paper (Paper): Selected paper.
    """
    st.subheader("Citation Network")
    st.caption(
        "Center node is the selected paper, left nodes are papers it cites, "
        "and right nodes are papers that cite it (OpenAlex)."
    )
    st.caption(
        "Nodes on each side are ranked by their own citation count and truncated to the top 10. "
        "Node size is sqrt(cited_by_count). Drag nodes to rearrange the graph. "
        "Click any node to open that paper on OpenAlex."
    )

    meta = paper.openalex
    work_id = ""
    if isinstance(meta, dict):
        work_id = str(meta.get("id") or "").strip()
    if not work_id:
        st.info("No OpenAlex work id found for this paper, so the citation network is unavailable.")
        return

    max_references = 10
    max_citing = 10

    with st.spinner("Loading citation neighborhood from OpenAlex..."):
        network = _openalex_citation_network(work_id, int(max_references), int(max_citing))

    center = network.get("center")
    references = _sort_works_by_size_desc(network.get("references") or [])
    citing = _sort_works_by_size_desc(network.get("citing") or [])
    if not isinstance(center, dict) or not center:
        st.warning("Unable to load OpenAlex data for this paper.")
        return

    reset_key = re.sub(r"[^a-zA-Z0-9_-]+", "_", _openalex_work_id(work_id) or "paper")
    control_col, help_col = st.columns([1, 4])
    with control_col:
        if st.button("Reset layout", key=f"reset-network-{reset_key}"):
            st.rerun()
    with help_col:
        st.caption("Tip: drag nodes to explore local structure, then use reset to restore lane layout.")

    components.html(_citation_network_html(center, references, citing), height=710, scrolling=False)

    summary_cols = st.columns(3)
    summary_cols[0].metric("Center Paper", 1)
    summary_cols[1].metric("References Shown", len(references))
    summary_cols[2].metric("Citing Papers Shown", len(citing))

    st.markdown("---")
    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown("**Referenced Papers (Left)**")
        rows = _works_rows(references)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.caption("No referenced papers returned.")
    with right_col:
        st.markdown("**Citing Papers (Right)**")
        rows = _works_rows(citing)
        if rows:
            st.dataframe(rows, width="stretch")
        else:
            st.caption("No citing papers returned.")


def _response_text_from_final_response(response: object) -> str:
    """Extract best-effort text from a completed OpenAI response object.

    Args:
        response (object): Description.

    Returns:
        str: Description.
    """
    text = getattr(response, "output_text", None) or getattr(response, "text", None)
    if text:
        return str(text).strip()
    parts: List[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "output_text":
                chunk = getattr(content, "text", None)
                if chunk:
                    parts.append(str(chunk))
    return "\n".join(parts).strip()


def stream_openai_answer(
    *,
    client: OpenAI,
    model: str,
    instructions: str,
    user_input: str,
    temperature: Optional[float],
    usage_context: str,
    session_id: Optional[str],
    request_id: Optional[str],
    on_delta: Callable[[str], None],
) -> str:
    """Stream answer tokens from OpenAI Responses API and return final text.

    Args:
        client (OpenAI): Description.
        model (str): Description.
        instructions (str): Description.
        user_input (str): Description.
        temperature (Optional[float]): Description.
        usage_context (str): Description.
        session_id (Optional[str]): Description.
        request_id (Optional[str]): Description.
        on_delta (Callable[[str], None]): Description.

    Returns:
        str: Description.

    Raises:
        Exception: Description.
    """
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    payload = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": None,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    for attempt in range(max_retries + 1):
        try:
            streamed = ""
            final_resp = None
            with client.responses.stream(**payload) as stream:
                for event in stream:
                    if getattr(event, "type", "") == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            streamed += delta
                            on_delta(streamed)
                final_resp = stream.get_final_response()

            final_text = _response_text_from_final_response(final_resp) if final_resp is not None else ""
            if final_text and len(final_text) > len(streamed):
                streamed = final_text
                on_delta(streamed)
            streamed = streamed.strip() or final_text.strip()

            if final_resp is not None:
                try:
                    usage = getattr(final_resp, "usage", None)
                    input_tokens = output_tokens = total_tokens = 0
                    if usage is not None:
                        if isinstance(usage, dict):
                            input_tokens = int(usage.get("input_tokens") or 0)
                            output_tokens = int(usage.get("output_tokens") or 0)
                            total_tokens = int(usage.get("total_tokens") or 0)
                        else:
                            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                    if total_tokens == 0:
                        total_tokens = input_tokens + output_tokens
                    record_usage(
                        model=model,
                        operation=usage_context,
                        step=usage_context,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        session_id=session_id,
                        request_id=request_id,
                    )
                except Exception:
                    pass

            return streamed
        except Exception as exc:
            if attempt >= max_retries:
                db_url = os.environ.get("DATABASE_URL")
                if db_url:
                    try:
                        from ragonometrics.indexing import metadata

                        conn = connect(db_url, require_migrated=True)
                        metadata.record_failure(
                            conn,
                            "openai",
                            str(exc),
                            {"model": model, "streaming": True, "temperature": temperature},
                        )
                        conn.close()
                    except Exception:
                        pass
                raise
            time.sleep(0.5 * (attempt + 1))


_MATH_SIGNAL_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z]_\{[^}]+\}|"
    r"[A-Za-z]_[A-Za-z0-9]+|"
    r"[A-Za-z]\^\{[^}]+\}|"
    r"\bp\([^)\n]*\|[^)\n]*\)|"
    r"\bargmax\b|\bargmin\b|"
    r"\u2211|\u222b|\u221a|\u2248|\u2260|\u2264|\u2265|"
    r"\bE\[[^\]]+\]"
    r")"
)


def _truthy_env(name: str, default: bool) -> bool:
    """Truthy env.

    Args:
        name (str): Description.
        default (bool): Description.

    Returns:
        bool: Description.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _should_review_math_latex(answer: str) -> bool:
    """Should review math latex.

    Args:
        answer (str): Description.

    Returns:
        bool: Description.
    """
    if not answer or not answer.strip():
        return False
    if "$" in answer and (_MATH_SIGNAL_PATTERN.search(answer) is None):
        return False
    if _MATH_SIGNAL_PATTERN.search(answer):
        return True
    # catch plain assignments/ranges often written without delimiters
    if re.search(r"\b[A-Za-z][A-Za-z0-9]*\s*=\s*[^,\n]{1,40}", answer):
        return True
    return False


def _estimate_review_max_tokens(answer: str) -> int:
    # Roughly map characters to tokens; keep bounded for latency/cost.
    """Estimate review max tokens.

    Args:
        answer (str): Description.

    Returns:
        int: Description.
    """
    approx = int(len(answer) / 3.0) + 128
    return max(256, min(3072, approx))


def maybe_review_math_latex(
    *,
    client: OpenAI,
    answer: str,
    source_model: str,
    session_id: Optional[str],
    request_id: Optional[str],
) -> str:
    """Optionally run an AI formatting pass so math renders with LaTeX.

    Args:
        client (OpenAI): Description.
        answer (str): Description.
        source_model (str): Description.
        session_id (Optional[str]): Description.
        request_id (Optional[str]): Description.

    Returns:
        str: Description.
    """
    if not _truthy_env("MATH_LATEX_REVIEW_ENABLED", True):
        return answer
    if not _should_review_math_latex(answer):
        return answer

    review_model = os.environ.get("MATH_LATEX_REVIEW_MODEL", "").strip() or source_model
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    payload = {
        "model": review_model,
        "instructions": MATH_LATEX_REVIEW_PROMPT,
        "input": f"Answer:\n{answer}",
        "max_output_tokens": _estimate_review_max_tokens(answer),
    }

    for attempt in range(max_retries + 1):
        try:
            resp = client.responses.create(**payload)
            reviewed = _response_text_from_final_response(resp).strip()
            if not reviewed:
                return answer
            try:
                usage = getattr(resp, "usage", None)
                input_tokens = output_tokens = total_tokens = 0
                if usage is not None:
                    if isinstance(usage, dict):
                        input_tokens = int(usage.get("input_tokens") or 0)
                        output_tokens = int(usage.get("output_tokens") or 0)
                        total_tokens = int(usage.get("total_tokens") or 0)
                    else:
                        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                if total_tokens == 0:
                    total_tokens = input_tokens + output_tokens
                record_usage(
                    model=review_model,
                    operation="math_latex_review",
                    step="math_latex_review",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    session_id=session_id,
                    request_id=request_id,
                )
            except Exception:
                pass
            return reviewed
        except Exception:
            if attempt >= max_retries:
                return answer
            time.sleep(0.5 * (attempt + 1))
    return answer


def scroll_chat_to_top() -> None:
    """Request a smooth scroll to the top of the Streamlit app.
    """
    components.html(
        """
        <script>
        const doc = window.parent.document;
        const app = doc.querySelector('[data-testid="stAppViewContainer"]');
        if (app) {
          app.scrollTo({ top: 0, behavior: "smooth" });
        } else {
          window.parent.scrollTo({ top: 0, behavior: "smooth" });
        }
        </script>
        """,
        height=0,
    )


def extract_highlight_terms(query: str, max_terms: int = 6) -> List[str]:
    """Extract key terms from a query for highlighting.

    Args:
        query (str): Description.
        max_terms (int): Description.

    Returns:
        List[str]: Description.
    """
    stop = {
        "the", "and", "or", "but", "a", "an", "of", "to", "in", "for", "on", "with",
        "is", "are", "was", "were", "be", "been", "it", "this", "that", "these",
        "those", "as", "at", "by", "from", "about", "into", "over", "after", "before",
        "what", "which", "who", "whom", "why", "how", "when", "where",
    }
    tokens = re.findall(r"[A-Za-z0-9]{3,}", query.lower())
    terms = []
    for tok in tokens:
        if tok in stop:
            continue
        if tok not in terms:
            terms.append(tok)
        if len(terms) >= max_terms:
            break
    return terms


def highlight_text_html(text: str, terms: List[str]) -> str:
    """Return HTML with highlight marks for matching terms.

    Args:
        text (str): Description.
        terms (List[str]): Description.

    Returns:
        str: Description.
    """
    if not terms:
        return html.escape(text)
    escaped = html.escape(text)
    for term in terms:
        pattern = re.compile(rf"\b({re.escape(term)})\b", re.IGNORECASE)
        escaped = pattern.sub(r"<mark>\1</mark>", escaped)
    return escaped


def highlight_image_terms(image, terms: List[str]):
    """Highlight matched terms on a PIL image using OCR.

    Args:
        image (Any): Description.
        terms (List[str]): Description.

    Returns:
        Any: Description.
    """
    if not terms or not pytesseract or not ImageDraw:
        return image
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception:
        return image
    if not data or "text" not in data:
        return image

    img = image.convert("RGBA")
    overlay = ImageDraw.Draw(img, "RGBA")
    terms_lower = {t.lower() for t in terms}
    for i, word in enumerate(data["text"]):
        if not word:
            continue
        w = word.strip().lower()
        if w in terms_lower:
            x = data["left"][i]
            y = data["top"][i]
            w_box = data["width"][i]
            h_box = data["height"][i]
            overlay.rectangle([x, y, x + w_box, y + h_box], fill=(255, 235, 59, 120), outline=(255, 193, 7, 200))
    return img


def render_citation_snapshot(path: Path, citation: dict, key_prefix: str, query: str) -> None:
    """Render a highlighted text snapshot and optional page image for a citation chunk.

    Args:
        path (Path): Description.
        citation (dict): Description.
        key_prefix (str): Description.
        query (str): Description.
    """
    meta = citation.get("meta") or "Context chunk"
    text = citation.get("text") or ""
    page = citation.get("page")
    terms = extract_highlight_terms(query)

    st.markdown(f"**{meta}**")
    if text:
        snippet = text if len(text) <= 1200 else text[:1200] + "..."
        highlighted = highlight_text_html(snippet, terms)
        st.markdown(
            f"<div style='font-family: monospace; white-space: pre-wrap;'>{highlighted}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.info("No text available for this chunk.")

    if page and convert_from_path:
        show_key = f"{key_prefix}_show_page_{page}"
        if st.checkbox(f"Show page {page} snapshot", key=show_key):
            try:
                images = convert_from_path(str(path), first_page=page, last_page=page)
                if images:
                    img = highlight_image_terms(images[0], terms)
                    st.image(img, caption=f"Page {page}")
            except Exception as exc:
                st.warning(f"Failed to render page {page}: {exc}")
    elif page:
        st.caption(f"Page {page} (snapshot requires pdf2image + poppler)")


def auth_gate() -> None:
    """Simple username/password gate for the Streamlit app.
    """
    expected_user = os.getenv("STREAMLIT_USERNAME")
    expected_pass = os.getenv("STREAMLIT_PASSWORD")

    if not expected_user or not expected_pass:
        st.sidebar.info("Login disabled (set STREAMLIT_USERNAME/STREAMLIT_PASSWORD to enable).")
        return

    if st.session_state.get("authenticated"):
        return

    st.sidebar.subheader("Login")
    with st.sidebar.form("login_form"):
        user = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        if user == expected_user and password == expected_pass:
            st.session_state.authenticated = True
            st.sidebar.success("Logged in.")
            return
        st.sidebar.error("Invalid credentials.")

    st.stop()


def main():
    """Run the Streamlit app.

    Raises:
        Exception: Description.
    """
    st.title("Ragonometrics -- Paper Chatbot")

    settings = load_settings()
    auth_gate()
    client = OpenAI()

    st.sidebar.markdown("### Welcome")
    st.sidebar.caption(
        "Ask evidence-grounded questions about one paper at a time. "
        "Select a PDF, use starter prompts, and continue with follow-up questions in chat."
    )
    st.sidebar.markdown("---")
    st.sidebar.header("Settings")
    papers_dir = Path(settings.papers_dir)
    st.sidebar.text_input(
        "Papers directory",
        value=str(papers_dir),
        disabled=True,
        help="Configured by the server environment. This path is read-only in the UI.",
    )
    top_k = st.sidebar.number_input(
        "Top K context chunks",
        value=int(settings.top_k),
        min_value=1,
        max_value=30,
        step=1,
    )
    model_options = [settings.chat_model]
    extra_models = [m.strip() for m in os.getenv("LLM_MODELS", "").split(",") if m.strip()]
    for m in extra_models:
        if m not in model_options:
            model_options.append(m)
    selected_model = st.sidebar.selectbox("LLM model", options=model_options, index=0)

    files = list_papers(papers_dir)

    if not files:
        st.warning(f"No PDF files found in {papers_dir}")
        return

    file_choice = st.selectbox("Select a paper", options=[p.name for p in files])
    selected_path = next(p for p in files if p.name == file_choice)

    with st.spinner("Loading and preparing paper..."):
        paper, chunks, chunk_embeddings = load_and_prepare(selected_path, settings)

    st.subheader(paper.title)
    st.caption(f"Author: {paper.author} -- {paper.path.name}")

    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    if openalex_context or citec_context:
        with st.expander("External Metadata", expanded=False):
            if openalex_context:
                st.markdown("**OpenAlex**")
                st.code(openalex_context, language="text")
            if citec_context:
                st.markdown("**CitEc**")
                st.code(citec_context, language="text")

    if not chunks:
        st.info("No text could be extracted from this PDF.")
        return

    if "history" not in st.session_state:
        st.session_state.history = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid4().hex
        st.session_state.session_started_at = datetime.now(timezone.utc).isoformat()
    if "last_request_id" not in st.session_state:
        st.session_state.last_request_id = None

    if st.sidebar.button("Clear chat history"):
        st.session_state.history = []

    retrieval_settings = settings
    if int(top_k) != settings.top_k:
        retrieval_settings = replace(settings, top_k=int(top_k))

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Paper Questions**")
    st.sidebar.caption(
        "Starter prompts for the selected paper. Click any question to send it to chat, "
        "then continue with follow-ups in your own words."
    )
    st.sidebar.caption(f"Current paper: `{selected_path.name}`")
    starter_questions = suggested_paper_questions(paper)
    for idx, starter in enumerate(starter_questions):
        if st.sidebar.button(starter, key=f"paper_starter_{selected_path.name}_{idx}", use_container_width=True):
            st.session_state["queued_query"] = starter

    tab_chat, tab_metadata, tab_network, tab_usage = st.tabs(
        ["Chat", "OpenAlex Metadata", "Citation Network", "Usage"]
    )

    with tab_chat:
        st.caption("Conversation mode is on. Follow-up questions use recent chat turns for continuity.")
        use_variation = st.toggle(
            "Variation mode",
            value=False,
            help="Use a higher temperature for slightly different wording.",
        )
        with st.container():
            query = st.chat_input("Ask a question about this paper")
            queued_query = st.session_state.pop("queued_query", None)
            if not query and queued_query:
                query = str(queued_query)
            rendered_current_turn = False
            if query:
                request_id = uuid4().hex
                st.session_state.last_request_id = request_id

                with st.chat_message("user"):
                    st.markdown(query)

                with st.chat_message("assistant"):
                    answer_placeholder = st.empty()
                    with st.spinner("Retrieving context and querying model..."):
                        context = top_k_context(
                            chunks,
                            chunk_embeddings,
                            query=query,
                            client=client,
                            settings=retrieval_settings,
                            session_id=st.session_state.session_id,
                            request_id=request_id,
                        )

                        temperature = None
                        cache_allowed = True
                        if use_variation:
                            cache_allowed = False
                            try:
                                temperature = float(os.getenv("RAG_VARIATION_TEMPERATURE", "0.7"))
                            except Exception:
                                temperature = 0.7

                        cached = None
                        cache_key = None
                        try:
                            history_turns = max(1, int(os.getenv("CHAT_HISTORY_TURNS", "6")))
                        except Exception:
                            history_turns = 6
                        history_context = build_chat_history_context(
                            st.session_state.history,
                            paper_path=paper.path,
                            max_turns=history_turns,
                        )

                        if cache_allowed:
                            cache_context = context
                            if history_context:
                                cache_context = f"Conversation History:\n{history_context}\n\n{context}"
                            cache_key = make_cache_key(query, str(paper.path), selected_model, cache_context)
                            cached = get_cached_answer(DEFAULT_CACHE_PATH, cache_key)

                        if cached is not None:
                            answer = cached
                        else:
                            openalex_context = format_openalex_context(paper.openalex)
                            citec_context = format_citec_context(paper.citec)
                            user_input_parts = []
                            if history_context:
                                user_input_parts.append(
                                    "Prior conversation (for continuity; prefer current question + evidence context if conflicts):\n"
                                    f"{history_context}"
                                )
                            user_input_parts.append(f"Context:\n{context}\n\nQuestion: {query}")
                            user_input = "\n\n".join(user_input_parts)
                            prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
                            if prefix_parts:
                                prefix = "\n\n".join(prefix_parts)
                                user_input = f"{prefix}\n\n{user_input}"

                            try:
                                answer = stream_openai_answer(
                                    client=client,
                                    model=selected_model,
                                    instructions=RESEARCHER_QA_PROMPT,
                                    user_input=user_input,
                                    temperature=temperature,
                                    usage_context="answer",
                                    session_id=st.session_state.session_id,
                                    request_id=request_id,
                                    on_delta=lambda txt: answer_placeholder.markdown(txt + "|"),
                                )
                            except BadRequestError as exc:
                                err = str(exc).lower()
                                if temperature is not None and "temperature" in err and "unsupported" in err:
                                    st.warning(
                                        "The selected model does not support temperature. "
                                        "Retrying without variation."
                                    )
                                    answer = stream_openai_answer(
                                        client=client,
                                        model=selected_model,
                                        instructions=RESEARCHER_QA_PROMPT,
                                        user_input=user_input,
                                        temperature=None,
                                        usage_context="answer",
                                        session_id=st.session_state.session_id,
                                        request_id=request_id,
                                        on_delta=lambda txt: answer_placeholder.markdown(txt + "|"),
                                    )
                                else:
                                    raise

                        reviewed_answer = maybe_review_math_latex(
                            client=client,
                            answer=answer,
                            source_model=selected_model,
                            session_id=st.session_state.session_id,
                            request_id=request_id,
                        )
                        if reviewed_answer:
                            answer = reviewed_answer
                        answer_placeholder.markdown(answer)

                        if cache_allowed and cache_key is not None:
                            set_cached_answer(
                                DEFAULT_CACHE_PATH,
                                cache_key=cache_key,
                                query=query,
                                paper_path=str(paper.path),
                                model=selected_model,
                                context=context,
                                answer=answer,
                            )

                        citations = parse_context_chunks(context)
                        if citations:
                            with st.expander("Snapshots", expanded=False):
                                st.caption(
                                    f"Showing {len(citations)} chunks (top_k={retrieval_settings.top_k}, total_chunks={len(chunks)})"
                                )
                                tab_labels = []
                                for c_idx, c in enumerate(citations, start=1):
                                    page = c.get("page")
                                    suffix = f" (p{page})" if page else ""
                                    tab_labels.append(f"Citation {c_idx}{suffix}")
                                tabs = st.tabs(tab_labels)
                                for c_idx, (tab, c) in enumerate(zip(tabs, citations), start=1):
                                    with tab:
                                        key_prefix = f"citation_{request_id}_{c_idx}"
                                        render_citation_snapshot(paper.path, c, key_prefix=key_prefix, query=query)

                    st.session_state.history.append(
                        {
                            "query": query,
                            "answer": answer,
                            "context": context,
                            "citations": citations,
                            "paper_path": str(paper.path),
                            "request_id": request_id,
                        }
                    )
                    rendered_current_turn = True
                    scroll_chat_to_top()

            if st.session_state.history:
                history_items = list(reversed(st.session_state.history))
                if rendered_current_turn and history_items:
                    # The latest turn was already rendered above while streaming.
                    history_items = history_items[1:]
                for item in history_items:
                    q = ""
                    a = ""
                    citations: List[dict] = []
                    citation_path = paper.path
                    request_id = None
                    if isinstance(item, tuple):
                        if len(item) >= 2:
                            q = str(item[0] or "")
                            a = str(item[1] or "")
                    elif isinstance(item, dict):
                        q = str(item.get("query") or "")
                        a = str(item.get("answer") or "")
                        context = item.get("context")
                        citations = item.get("citations")
                        item_paper_path = item.get("paper_path")
                        request_id = item.get("request_id")
                        if context:
                            citations = parse_context_chunks(context)
                        elif citations is None:
                            citations = []
                        if item_paper_path:
                            citation_path = Path(item_paper_path)

                    history_id = request_id
                    if not history_id:
                        token = f"{q}|{a}"
                        history_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]

                    with st.chat_message("user"):
                        st.markdown(q)
                    with st.chat_message("assistant"):
                        st.markdown(a)
                        if citations:
                            with st.expander("Snapshots", expanded=False):
                                st.caption(
                                    f"Showing {len(citations)} chunks (top_k={retrieval_settings.top_k}, total_chunks={len(chunks)})"
                                )
                                tab_labels = []
                                for c_idx, c in enumerate(citations, start=1):
                                    page = c.get("page")
                                    suffix = f" (p{page})" if page else ""
                                    tab_labels.append(f"Citation {c_idx}{suffix}")
                                tabs = st.tabs(tab_labels)
                                for c_idx, (tab, c) in enumerate(zip(tabs, citations), start=1):
                                    with tab:
                                        key_prefix = f"citation_{history_id}_{c_idx}"
                                        render_citation_snapshot(citation_path, c, key_prefix=key_prefix, query=q or "")

    with tab_metadata:
        render_openalex_metadata_tab(paper)

    with tab_network:
        render_openalex_citation_network_tab(paper)

    with tab_usage:
        st.subheader("Token Usage")
        st.caption("Aggregates are computed from Postgres (`observability.token_usage`).")
        _reset_query_timings()

        now = datetime.now(timezone.utc)
        last_24h = (now - timedelta(hours=24)).isoformat()

        total = _timed_call("usage_summary:all_time", get_usage_summary, db_path=DEFAULT_USAGE_DB)
        session_total = _timed_call(
            "usage_summary:session",
            get_usage_summary,
            db_path=DEFAULT_USAGE_DB,
            session_id=st.session_state.session_id,
        )
        recent_total = _timed_call(
            "usage_summary:24h",
            get_usage_summary,
            db_path=DEFAULT_USAGE_DB,
            since=last_24h,
        )

        metrics_cols = st.columns(4)
        metrics_cols[0].metric("Total Tokens (All Time)", f"{total.total_tokens}")
        metrics_cols[1].metric("Total Tokens (Session)", f"{session_total.total_tokens}")
        metrics_cols[2].metric("Total Tokens (24h)", f"{recent_total.total_tokens}")
        metrics_cols[3].metric("Calls (All Time)", f"{total.calls}")

        if st.session_state.last_request_id:
            last_query = _timed_call(
                "usage_summary:last_request",
                db_path=DEFAULT_USAGE_DB,
                fn=get_usage_summary,
                request_id=st.session_state.last_request_id,
            )
            st.metric("Last Query Tokens", f"{last_query.total_tokens}")

        st.markdown("---")
        st.subheader("Usage By Model")
        by_model = _timed_call("usage_by_model", get_usage_by_model, db_path=DEFAULT_USAGE_DB)
        if by_model:
            st.dataframe(by_model, width="stretch")
        else:
            st.info("No usage records yet.")

        st.markdown("---")
        st.subheader("Recent Usage Records")
        recent_limit = st.slider("Recent rows", min_value=50, max_value=1000, value=200, step=50)
        recent = _timed_call("usage_recent", get_recent_usage, db_path=DEFAULT_USAGE_DB, limit=int(recent_limit))
        if recent:
            st.dataframe(recent, width="stretch")
        else:
            st.info("No usage records yet.")

        st.markdown("---")
        show_timings = st.checkbox("Show query timings (debug)", value=False)
        if show_timings:
            rows = st.session_state.get(_QUERY_TIMINGS_KEY, [])
            if rows:
                total_ms = round(sum(float(r.get("elapsed_ms") or 0.0) for r in rows), 2)
                st.caption(f"DB query wall time (this render): {total_ms} ms")
                st.dataframe(rows, width="stretch")
            else:
                st.info("No timing rows captured yet.")


if __name__ == "__main__":
    main()
