"""Multi-paper chat orchestration, history, suggestions, and graph helpers."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from uuid import uuid4

from ragonometrics.core.main import load_settings
from ragonometrics.db.connection import pooled_connection
from ragonometrics.integrations.openalex import request_json as openalex_request_json
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.pipeline import call_llm
from ragonometrics.services import chat as chat_service
from ragonometrics.services import paper_compare as paper_compare_service
from ragonometrics.services import papers as papers_service
from ragonometrics.services import projects as projects_service
from ragonometrics.services import provenance as provenance_service

MAX_MULTI_CHAT_PAPERS = 10
MAX_MULTI_HISTORY_LIMIT = 200
DEFAULT_SUGGESTION_LIMIT = 10
DEFAULT_EXTERNAL_SUGGESTION_LIMIT = 10

MULTI_PAPER_SYNTHESIS_PROMPT = (
    "You are helping a researcher synthesize evidence across multiple papers.\n"
    "Use only the provided per-paper answers and evidence snippets.\n"
    "Do not invent results or effect sizes.\n"
    "Attribute claims to paper titles or paper IDs inline.\n"
    "When papers conflict, state the disagreement explicitly.\n"
    "Preserve uncertainty and note missing evidence.\n"
    "Return markdown with exactly these headings:\n"
    "## Short Answer\n"
    "## Consensus\n"
    "## Disagreements\n"
    "## Methods and Data Differences\n"
    "## Evidence Strength and Caveats\n"
    "## What This Means for the Field\n"
    "## Next Questions\n"
    "Use concise bullets under each section where appropriate."
)


def _db_url() -> str:
    """Return the active database URL."""
    return str(os.environ.get("DATABASE_URL") or "").strip()


def _to_iso(value: Any) -> str:
    """Convert datetime-like values to ISO strings."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except Exception:
            return str(value or "")
    return str(value or "")


def _json_obj(value: Any) -> Dict[str, Any]:
    """Parse a JSON object payload, falling back to empty dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> List[Any]:
    """Parse a JSON list payload, falling back to empty list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _status_for_score(score: float) -> str:
    """Map a numeric provenance score to a label."""
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _normalize_text(value: Any) -> str:
    """Normalize text for comparisons."""
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Compute Jaccard overlap."""
    a = {str(item).strip().lower() for item in left if str(item or "").strip()}
    b = {str(item).strip().lower() for item in right if str(item or "").strip()}
    if not a or not b:
        return 0.0
    den = len(a | b)
    if den <= 0:
        return 0.0
    return float(len(a & b)) / float(den)


def _weighted_jaccard(left: Dict[str, float], right: Dict[str, float]) -> float:
    """Weighted Jaccard similarity for topic/concept vectors."""
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    num = 0.0
    den = 0.0
    for key in keys:
        lv = float(left.get(key, 0.0))
        rv = float(right.get(key, 0.0))
        num += min(lv, rv)
        den += max(lv, rv)
    if den <= 0.0:
        return 0.0
    return float(num / den)


def _openalex_work_id(value: Any) -> str:
    """Normalize OpenAlex work ids to ``W...`` format."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("?", 1)[0].rstrip("/")
    if text.startswith("https://api.openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    elif text.startswith("https://openalex.org/"):
        text = text.rsplit("/", 1)[-1]
    if text.lower().startswith("works/"):
        text = text.split("/", 1)[-1]
    if text.lower().startswith("w"):
        text = "W" + text[1:]
    return text if re.fullmatch(r"W\d+", text) else ""


def _openalex_url(value: Any) -> str:
    """Return canonical OpenAlex URL for a work id."""
    work_id = _openalex_work_id(value)
    return f"https://openalex.org/{work_id}" if work_id else ""


def _author_names_from_meta(meta: Dict[str, Any]) -> List[str]:
    """Extract author names from OpenAlex metadata."""
    names: List[str] = []
    seen = set()
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _extract_topic_signature(meta: Dict[str, Any]) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Build topic/concept signatures from OpenAlex metadata."""
    topics: Dict[str, float] = {}
    concepts: Dict[str, float] = {}
    for item in _json_list(meta.get("topics")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            score = float(item.get("score") or 1.0)
        except Exception:
            score = 1.0
        topics[name] = max(topics.get(name, 0.0), max(0.01, score))
    for item in _json_list(meta.get("concepts")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            score = float(item.get("score") or 1.0)
        except Exception:
            score = 1.0
        concepts[name] = max(concepts.get(name, 0.0), max(0.01, score))
    merged: Dict[str, float] = {}
    for key, value in topics.items():
        merged[f"t:{key}"] = float(value)
    for key, value in concepts.items():
        merged[f"c:{key}"] = float(value) * 0.8
    return topics, concepts, merged


def _history_scope_sql(*, user_id: Optional[int], username: str) -> Tuple[str, List[Any]]:
    """Return SQL clause for one user's history scope."""
    clean_user = str(username or "").strip()
    if user_id is not None:
        return (
            "(user_id = %s OR (user_id IS NULL AND lower(username) = lower(%s)))",
            [int(user_id), clean_user],
        )
    return ("lower(username) = lower(%s)", [clean_user])


def _paper_ref_map(*, project_id: Optional[str] = None) -> Dict[str, papers_service.PaperRef]:
    """Return paper refs keyed by paper_id within the requested project scope."""
    settings = load_settings()
    refs = papers_service.list_papers(settings=settings)
    scoped_project = str(project_id or "").strip()
    if scoped_project:
        allowed = set(projects_service.project_paper_ids(_db_url(), project_id=scoped_project))
        if allowed:
            refs = [ref for ref in refs if ref.paper_id in allowed]
        elif scoped_project != projects_service.DEFAULT_PROJECT_ID:
            refs = []
    return {ref.paper_id: ref for ref in refs}


def _validate_paper_ids(
    paper_ids: Sequence[str],
    *,
    project_id: Optional[str] = None,
) -> List[papers_service.PaperRef]:
    """Validate and resolve selected paper ids."""
    refs_by_id = _paper_ref_map(project_id=project_id)
    ordered_ids: List[str] = []
    seen = set()
    for item in list(paper_ids or []):
        pid = str(item or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        ordered_ids.append(pid)
    if len(ordered_ids) < 2:
        raise ValueError("At least 2 papers are required.")
    if len(ordered_ids) > MAX_MULTI_CHAT_PAPERS:
        raise ValueError(f"At most {MAX_MULTI_CHAT_PAPERS} papers are allowed.")
    unknown = [pid for pid in ordered_ids if pid not in refs_by_id]
    if unknown:
        raise ValueError(f"Unknown paper ids: {', '.join(unknown)}")
    return [refs_by_id[pid] for pid in ordered_ids]


def _multi_history_context(history: Sequence[Dict[str, Any]], *, max_turns: int = 8, max_answer_chars: int = 1200) -> str:
    """Build compact cross-paper conversation history context."""
    rows: List[str] = []
    for item in list(history or [])[-max_turns:]:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not query or not answer:
            continue
        paper_ids = [str(v or "").strip() for v in _json_list(item.get("paper_ids")) if str(v or "").strip()]
        if len(answer) > max_answer_chars:
            answer = answer[: max_answer_chars - 3] + "..."
        scope = ", ".join(paper_ids[:6]) + (" ..." if len(paper_ids) > 6 else "")
        rows.append(f"User: {query}\nAssistant ({scope or 'multi'}): {answer}")
    return "\n\n".join(rows)


def suggested_multi_paper_questions() -> List[str]:
    """Return deterministic starter prompts for multi-paper synthesis chat."""
    return [
        "What broad research question or field debate do these papers collectively address?",
        "Where do these papers agree on the main findings?",
        "Where do these papers disagree, and what likely explains the disagreement?",
        "How do the identification strategies differ across papers?",
        "How do datasets, sample periods, and populations differ?",
        "Are the outcome definitions and treatment variables comparable?",
        "How comparable are the reported effect sizes or quantitative results?",
        "What robustness checks are common across the papers, and which are missing?",
        "How do assumptions differ, and which conclusions depend most on strong assumptions?",
        "What heterogeneity analyses are reported, and do they point in the same direction?",
        "What mechanisms are proposed, and which are directly tested vs inferred?",
        "How do external validity and policy relevance differ across papers?",
        "Which paper provides the strongest evidence for causal claims, and why?",
        "Which paper is most influential in this set (methods/citations), and does that match evidentiary strength?",
        "What important limitations recur across the papers?",
        "What key unanswered questions remain in this literature cluster?",
        "What is the best follow-up paper (in-project) to read next, and why?",
        "What external papers should be added to strengthen this comparison set?",
    ]


def _suggested_followups(*, question: str) -> List[str]:
    """Return a short follow-up list excluding the current question when possible."""
    norm_q = _normalize_text(question)
    out: List[str] = []
    for item in suggested_multi_paper_questions():
        if _normalize_text(item) == norm_q:
            continue
        out.append(item)
        if len(out) >= 6:
            break
    return out


def _extract_section(answer_text: str, heading: str) -> List[str]:
    """Extract bullet rows from a markdown section heading."""
    text = str(answer_text or "")
    if not text.strip():
        return []
    pattern = rf"(?ims)^\s*##\s*{re.escape(heading)}\s*$"
    m = re.search(pattern, text)
    if not m:
        return []
    start = m.end()
    next_m = re.search(r"(?ims)^\s*##\s+.+$", text[start:])
    block = text[start : start + next_m.start()] if next_m else text[start:]
    rows: List[str] = []
    for raw in block.splitlines():
        clean = raw.strip()
        if not clean:
            continue
        if clean.startswith(("- ", "* ")):
            rows.append(clean[2:].strip())
        elif re.match(r"^\d+\.\s+", clean):
            rows.append(re.sub(r"^\d+\.\s+", "", clean).strip())
    return rows[:8]


def _comparison_summary_from_answer(answer_text: str) -> Dict[str, List[str]]:
    """Derive structured comparison summary lists from the synthesis markdown."""
    consensus = _extract_section(answer_text, "Consensus")
    disagreements = _extract_section(answer_text, "Disagreements")
    methods = _extract_section(answer_text, "Methods and Data Differences")
    evidence = _extract_section(answer_text, "Evidence Strength and Caveats")
    evidence_gaps = [row for row in evidence if re.search(r"\b(missing|gap|uncertain|unclear|limited|caveat)\b", row, flags=re.I)]
    return {
        "consensus_points": consensus,
        "disagreement_points": disagreements,
        "methods_contrasts": methods,
        "evidence_gaps": evidence_gaps[:8] if evidence_gaps else evidence[:8],
    }


def _aggregate_provenance(paper_answers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute a transparent aggregate provenance payload for multi-paper synthesis."""
    per_paper: List[Dict[str, Any]] = []
    scores: List[float] = []
    selected_count = len(list(paper_answers or []))
    answered_count = 0
    cited_answer_count = 0
    cached_answer_count = 0
    for item in paper_answers:
        if not isinstance(item, dict):
            continue
        answer_text = str(item.get("answer") or "").strip()
        if answer_text:
            answered_count += 1
        citations = item.get("citations") if isinstance(item.get("citations"), list) else []
        if citations:
            cited_answer_count += 1
        if bool(item.get("cache_hit")):
            cached_answer_count += 1
        prov = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        score = prov.get("score")
        try:
            fscore = float(score)
        except Exception:
            fscore = None
        if fscore is not None:
            scores.append(fscore)
        per_paper.append(
            {
                "paper_id": str(item.get("paper_id") or ""),
                "score": float(fscore or 0.0),
                "status": str(prov.get("status") or _status_for_score(float(fscore or 0.0))),
            }
        )
    aggregate_score = round(sum(scores) / len(scores), 3) if scores else 0.0
    coverage_ratio = round(float(cited_answer_count) / float(selected_count or 1), 3)
    return {
        "score": aggregate_score,
        "status": _status_for_score(aggregate_score),
        "per_paper": per_paper,
        "coverage": {
            "selected_paper_count": int(selected_count),
            "answered_paper_count": int(answered_count),
            "cited_answer_count": int(cited_answer_count),
            "cached_answer_count": int(cached_answer_count),
            "fresh_answer_count": max(0, int(answered_count - cached_answer_count)),
            "cited_answer_ratio": coverage_ratio,
        },
    }


def _synthesis_user_input(
    *,
    question: str,
    paper_answers: Sequence[Dict[str, Any]],
    history_context: str,
) -> str:
    """Build synthesis user input payload."""
    parts: List[str] = []
    if history_context:
        parts.append(
            "Prior multi-paper conversation (for continuity; prefer current question + current evidence if conflicts):\n"
            f"{history_context}"
        )
    parts.append(f"Current multi-paper question:\n{str(question or '').strip()}")
    answer_blocks: List[str] = []
    for idx, item in enumerate(list(paper_answers or []), start=1):
        if not isinstance(item, dict):
            continue
        prov = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
        citation_rows = item.get("citations") if isinstance(item.get("citations"), list) else []
        citation_preview: List[str] = []
        for c in citation_rows[:4]:
            if not isinstance(c, dict):
                continue
            page = c.get("page")
            text = str(c.get("text") or "").strip()
            if len(text) > 240:
                text = text[:237].rstrip() + "..."
            label = f"p.{page}" if page is not None else "p.?"
            if text:
                citation_preview.append(f"- [{label}] {text}")
        answer_text = str(item.get("answer") or "").strip()
        if len(answer_text) > 3500:
            answer_text = answer_text[:3497].rstrip() + "..."
        answer_blocks.append(
            "\n".join(
                [
                    f"Paper {idx}: {str(item.get('paper_title') or item.get('paper_id') or '').strip()}",
                    f"paper_id: {str(item.get('paper_id') or '').strip()}",
                    f"cache: {'hit' if bool(item.get('cache_hit')) else 'fresh'} ({str(item.get('cache_hit_layer') or 'none')})",
                    f"provenance: {float(prov.get('score') or 0.0):.3f} ({str(prov.get('status') or 'low')})",
                    "answer:",
                    answer_text or "(empty)",
                    "citations:",
                    "\n".join(citation_preview) if citation_preview else "- none",
                ]
            )
        )
    parts.append("Per-paper evidence summaries:\n" + "\n\n---\n\n".join(answer_blocks))
    return "\n\n".join(parts)


def _extract_openalex_meta(ref: papers_service.PaperRef) -> Dict[str, Any]:
    """Load OpenAlex metadata for one paper if available."""
    try:
        paper, _, _, _ = papers_service.load_prepared(ref)
    except Exception:
        return {}
    meta = getattr(paper, "openalex", None)
    return meta if isinstance(meta, dict) else {}


def _build_paper_answer_rows(
    *,
    refs: Sequence[papers_service.PaperRef],
    question: str,
    model: Optional[str],
    top_k: Optional[int],
    session_id: Optional[str],
    request_id: Optional[str],
    project_id: Optional[str],
    persona_id: Optional[str],
    allow_cross_project_answer_reuse: bool,
    allow_custom_question_sharing: bool,
    user_id: Optional[int],
    on_paper_answer: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Generate or reuse per-paper answers and provenance."""
    rows: List[Dict[str, Any]] = []
    total = len(list(refs or []))
    for idx, ref in enumerate(list(refs or []), start=1):
        overview = papers_service.paper_overview(ref)
        out = chat_service.chat_turn(
            paper_ref=ref,
            query=question,
            model=model,
            top_k=top_k,
            session_id=session_id,
            request_id=request_id,
            history=[],
            variation_mode=False,
            project_id=project_id,
            persona_id=persona_id,
            allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
            allow_custom_question_sharing=allow_custom_question_sharing,
            user_id=user_id,
        )
        citations = out.get("citations") if isinstance(out.get("citations"), list) else []
        try:
            provenance = provenance_service.score_answer_provenance(
                paper_ref=ref,
                question=question,
                answer=str(out.get("answer") or ""),
                citations=citations,
            )
        except Exception:
            provenance = {}
        row = {
            "paper_id": ref.paper_id,
            "paper_path": ref.path,
            "paper_name": ref.name,
            "paper_title": str(overview.get("display_title") or overview.get("title") or ref.name),
            "authors": str(overview.get("display_authors") or ""),
            "openalex_url": str(overview.get("openalex_url") or ""),
            "answer": str(out.get("answer") or ""),
            "citations": citations,
            "retrieval_stats": out.get("retrieval_stats") if isinstance(out.get("retrieval_stats"), dict) else {},
            "cache_hit": bool(out.get("cache_hit")) if isinstance(out.get("cache_hit"), bool) else False,
            "cache_hit_layer": str(out.get("cache_hit_layer") or "none"),
            "cache_scope": str(out.get("cache_scope") or ""),
            "cache_miss_reason": str(out.get("cache_miss_reason") or ""),
            "model": str(out.get("model") or model or ""),
            "provenance": provenance,
        }
        rows.append(row)
        if callable(on_paper_answer):
            try:
                on_paper_answer(row, idx=idx, total=total)
            except Exception:
                pass
    return rows


def _rank_project_companion_papers(
    *,
    refs: Sequence[papers_service.PaperRef],
    project_id: Optional[str],
    limit: int = DEFAULT_SUGGESTION_LIMIT,
) -> List[Dict[str, Any]]:
    """Aggregate compare-service suggestions across selected papers."""
    selected_ids = {str(ref.paper_id or "") for ref in refs}
    agg: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        try:
            out = paper_compare_service.suggest_similar_papers(
                ref.paper_id,
                limit=max(20, int(limit) * 3),
                project_id=project_id,
            )
        except Exception:
            continue
        for row in out.get("rows") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("paper_id") or "").strip()
            if not pid or pid in selected_ids:
                continue
            slot = agg.setdefault(
                pid,
                {
                    "paper_id": pid,
                    "name": str(row.get("name") or ""),
                    "path": str(row.get("path") or ""),
                    "title": str(row.get("title") or ""),
                    "authors": str(row.get("authors") or ""),
                    "openalex_url": str(row.get("openalex_url") or ""),
                    "score": 0.0,
                    "score_breakdown": {"topic_similarity": 0.0, "keyword_overlap": 0.0},
                    "supporting_selected_paper_ids": [],
                    "overlap_topics": set(),
                    "overlap_concepts": set(),
                },
            )
            slot["score"] = float(slot.get("score") or 0.0) + float(row.get("score") or 0.0)
            breakdown = row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {}
            score_breakdown = slot.get("score_breakdown") if isinstance(slot.get("score_breakdown"), dict) else {}
            score_breakdown["topic_similarity"] = float(score_breakdown.get("topic_similarity") or 0.0) + float(
                breakdown.get("topic_similarity") or 0.0
            )
            score_breakdown["keyword_overlap"] = float(score_breakdown.get("keyword_overlap") or 0.0) + float(
                breakdown.get("keyword_overlap") or 0.0
            )
            slot["score_breakdown"] = score_breakdown
            supports = slot.get("supporting_selected_paper_ids")
            if isinstance(supports, list) and ref.paper_id not in supports:
                supports.append(ref.paper_id)
            for topic in row.get("overlap_topics") or []:
                if isinstance(slot.get("overlap_topics"), set):
                    slot["overlap_topics"].add(str(topic))
            for concept in row.get("overlap_concepts") or []:
                if isinstance(slot.get("overlap_concepts"), set):
                    slot["overlap_concepts"].add(str(concept))
    rows: List[Dict[str, Any]] = []
    for slot in agg.values():
        support_count = len(slot.get("supporting_selected_paper_ids") or [])
        if support_count > 0:
            slot["score"] = round(float(slot.get("score") or 0.0) / float(support_count), 6)
            score_breakdown = slot.get("score_breakdown") if isinstance(slot.get("score_breakdown"), dict) else {}
            slot["score_breakdown"] = {
                "topic_similarity": round(float(score_breakdown.get("topic_similarity") or 0.0) / float(support_count), 6),
                "keyword_overlap": round(float(score_breakdown.get("keyword_overlap") or 0.0) / float(support_count), 6),
            }
        slot["support_count"] = support_count
        slot["overlap_topics"] = sorted([str(v) for v in list(slot.get("overlap_topics") or set())])[:8]
        slot["overlap_concepts"] = sorted([str(v) for v in list(slot.get("overlap_concepts") or set())])[:8]
        rows.append(slot)
    rows.sort(
        key=lambda r: (
            -int(r.get("support_count") or 0),
            -float(r.get("score") or 0.0),
            str(r.get("title") or "").lower(),
        )
    )
    return rows[: max(1, min(100, int(limit or DEFAULT_SUGGESTION_LIMIT)))]


def _external_openalex_suggestions(
    *,
    refs: Sequence[papers_service.PaperRef],
    limit: int = DEFAULT_EXTERNAL_SUGGESTION_LIMIT,
) -> List[Dict[str, Any]]:
    """Return external OpenAlex suggestions based on shared topic/concept terms."""
    metas: List[Dict[str, Any]] = []
    selected_openalex_ids = set()
    selected_titles = set()
    topic_scores: Dict[str, float] = {}
    concept_scores: Dict[str, float] = {}
    for ref in refs:
        meta = _extract_openalex_meta(ref)
        if not meta:
            continue
        metas.append(meta)
        work_id = _openalex_work_id(meta.get("id"))
        if work_id:
            selected_openalex_ids.add(work_id)
        title = str(meta.get("display_name") or meta.get("title") or "").strip().lower()
        if title:
            selected_titles.add(title)
        topics, concepts, _ = _extract_topic_signature(meta)
        for key, value in topics.items():
            topic_scores[key] = topic_scores.get(key, 0.0) + float(value)
        for key, value in concepts.items():
            concept_scores[key] = concept_scores.get(key, 0.0) + float(value)
    if not metas:
        return []
    top_topic_terms = [k for k, _ in sorted(topic_scores.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    top_concept_terms = [k for k, _ in sorted(concept_scores.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    search_terms = [term for term in [*top_topic_terms, *top_concept_terms] if term]
    if not search_terms:
        return []
    payload = openalex_request_json(
        "https://api.openalex.org/works",
        params={
            "search": " ".join(search_terms[:4]),
            "per-page": max(10, min(50, int(limit or DEFAULT_EXTERNAL_SUGGESTION_LIMIT) * 3)),
            "sort": "cited_by_count:desc",
            "select": "id,display_name,publication_year,doi,cited_by_count,authorships,topics,concepts",
        },
        timeout=20,
    )
    results = (payload or {}).get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    selected_signature = {f"t:{k}": float(v) for k, v in topic_scores.items()}
    for key, value in concept_scores.items():
        selected_signature[f"c:{key}"] = float(value) * 0.8
    rows: List[Dict[str, Any]] = []
    seen = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        work_id = _openalex_work_id(item.get("id"))
        title = str(item.get("display_name") or item.get("title") or "").strip()
        if not work_id or not title:
            continue
        if work_id in selected_openalex_ids or title.lower() in selected_titles or work_id in seen:
            continue
        seen.add(work_id)
        _, _, merged = _extract_topic_signature(item)
        score = _weighted_jaccard(selected_signature, merged)
        rows.append(
            {
                "paper_id": "",
                "name": title,
                "path": "",
                "title": title,
                "authors": ", ".join(_author_names_from_meta(item)),
                "openalex_url": _openalex_url(work_id),
                "openalex_id": work_id,
                "publication_year": item.get("publication_year"),
                "doi": str(item.get("doi") or ""),
                "score": round(float(score), 6),
                "source": "openalex_external",
                "ingested": False,
                "reason": f"Matched shared topic/concept terms: {', '.join(search_terms[:4])}",
            }
        )
    rows.sort(key=lambda r: (-float(r.get("score") or 0.0), str(r.get("title") or "").lower()))
    return rows[: max(1, min(100, int(limit or DEFAULT_EXTERNAL_SUGGESTION_LIMIT)))]


def suggest_companion_papers(
    *,
    paper_ids: Sequence[str],
    project_id: Optional[str] = None,
    project_limit: int = DEFAULT_SUGGESTION_LIMIT,
    external_limit: int = DEFAULT_EXTERNAL_SUGGESTION_LIMIT,
) -> Dict[str, Any]:
    """Suggest additional papers that fit the selected set."""
    refs = _validate_paper_ids(paper_ids, project_id=project_id)
    project_rows = _rank_project_companion_papers(refs=refs, project_id=project_id, limit=project_limit)
    try:
        external_rows = _external_openalex_suggestions(refs=refs, limit=external_limit)
    except Exception:
        external_rows = []
    return {
        "selected_paper_ids": [ref.paper_id for ref in refs],
        "project": project_rows,
        "external": external_rows,
        "rationale_summary": {
            "project_ranker": "Aggregated compare similarity across selected papers (topic/concept + keyword overlap).",
            "external_ranker": "OpenAlex search over shared topics/concepts with metadata-based similarity scoring.",
        },
    }


def selected_paper_interaction_graph(
    *,
    paper_ids: Sequence[str],
    project_id: Optional[str] = None,
    include_topic_edges: bool = True,
    include_author_edges: bool = True,
    include_citation_edges: bool = True,
    min_similarity: float = 0.15,
) -> Dict[str, Any]:
    """Build a selected-paper interaction graph payload for the web UI."""
    refs = _validate_paper_ids(paper_ids, project_id=project_id)
    nodes: List[Dict[str, Any]] = []
    metas_by_id: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    openalex_id_by_paper: Dict[str, str] = {}
    for ref in refs:
        overview = papers_service.paper_overview(ref)
        meta = _extract_openalex_meta(ref)
        metas_by_id[ref.paper_id] = meta
        work_id = _openalex_work_id(meta.get("id") if isinstance(meta, dict) else "")
        if work_id:
            openalex_id_by_paper[ref.paper_id] = work_id
        else:
            warnings.append(f"No OpenAlex work id for paper {ref.paper_id}.")
        nodes.append(
            {
                "id": ref.paper_id,
                "paper_id": ref.paper_id,
                "label": str(overview.get("display_title") or overview.get("title") or ref.name),
                "title": str(overview.get("display_title") or overview.get("title") or ref.name),
                "authors": str(overview.get("display_authors") or ""),
                "publication_year": overview.get("publication_year"),
                "openalex_url": str(overview.get("openalex_url") or ""),
                "openalex_id": work_id,
                "group": "selected",
            }
        )
    ids = [ref.paper_id for ref in refs]
    node_by_id = {row["id"]: row for row in nodes}
    edges: List[Dict[str, Any]] = []
    edge_seen = set()

    def _add_edge(edge: Dict[str, Any]) -> None:
        key = (str(edge.get("from") or ""), str(edge.get("to") or ""), str(edge.get("type") or ""))
        if key in edge_seen:
            return
        edge_seen.add(key)
        edges.append(edge)

    min_sim = max(0.0, min(1.0, float(min_similarity or 0.0)))
    topics_by_id: Dict[str, Dict[str, float]] = {}
    concepts_by_id: Dict[str, Dict[str, float]] = {}
    authors_by_id: Dict[str, List[str]] = {}
    refs_by_id_set: Dict[str, set[str]] = {}
    for paper_id in ids:
        meta = metas_by_id.get(paper_id) if isinstance(metas_by_id.get(paper_id), dict) else {}
        topics, concepts, _merged = _extract_topic_signature(meta or {})
        topics_by_id[paper_id] = topics
        concepts_by_id[paper_id] = concepts
        authors = _author_names_from_meta(meta or {})
        if not authors:
            authors = [a.strip() for a in str((node_by_id.get(paper_id) or {}).get("authors") or "").split(",") if a.strip()]
        authors_by_id[paper_id] = authors
        refs_by_id_set[paper_id] = {_openalex_work_id(val) for val in _json_list((meta or {}).get("referenced_works")) if _openalex_work_id(val)}
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            left_work_id = openalex_id_by_paper.get(left, "")
            right_work_id = openalex_id_by_paper.get(right, "")
            left_refs = refs_by_id_set.get(left, set())
            right_refs = refs_by_id_set.get(right, set())
            if include_citation_edges and left_work_id and right_work_id:
                if right_work_id in left_refs:
                    _add_edge({"from": left, "to": right, "type": "cites", "weight": 1.0, "label": "cites", "directed": True, "details": {"via": "referenced_works"}})
                    _add_edge({"from": right, "to": left, "type": "cited_by_selected", "weight": 1.0, "label": "cited by selected", "directed": True, "details": {"via": "referenced_works"}})
                if left_work_id in right_refs:
                    _add_edge({"from": right, "to": left, "type": "cites", "weight": 1.0, "label": "cites", "directed": True, "details": {"via": "referenced_works"}})
                    _add_edge({"from": left, "to": right, "type": "cited_by_selected", "weight": 1.0, "label": "cited by selected", "directed": True, "details": {"via": "referenced_works"}})
                shared_refs = _jaccard(left_refs, right_refs)
                if shared_refs >= min_sim:
                    _add_edge({"from": left, "to": right, "type": "shared_references", "weight": round(float(shared_refs), 6), "label": "shared refs", "directed": False, "details": {"shared_reference_ratio": round(float(shared_refs), 6)}})
            topic_sim = _weighted_jaccard(topics_by_id.get(left, {}), topics_by_id.get(right, {}))
            concept_sim = _weighted_jaccard(concepts_by_id.get(left, {}), concepts_by_id.get(right, {}))
            if include_topic_edges and topic_sim >= min_sim:
                _add_edge({"from": left, "to": right, "type": "topic_overlap", "weight": round(float(topic_sim), 6), "label": "topic overlap", "directed": False, "details": {"similarity": round(float(topic_sim), 6)}})
            if include_topic_edges and concept_sim >= min_sim:
                _add_edge({"from": left, "to": right, "type": "concept_overlap", "weight": round(float(concept_sim), 6), "label": "concept overlap", "directed": False, "details": {"similarity": round(float(concept_sim), 6)}})
            if include_author_edges:
                author_sim = _jaccard(authors_by_id.get(left, []), authors_by_id.get(right, []))
                if author_sim >= min_sim:
                    _add_edge({"from": left, "to": right, "type": "author_overlap", "weight": round(float(author_sim), 6), "label": "author overlap", "directed": False, "details": {"similarity": round(float(author_sim), 6)}})
    counts_by_type: Dict[str, int] = {}
    for edge in edges:
        key = str(edge.get("type") or "")
        counts_by_type[key] = counts_by_type.get(key, 0) + 1
    strongest = sorted(
        edges,
        key=lambda e: (-float(e.get("weight") or 0.0), str(e.get("type") or ""), str(e.get("from") or ""), str(e.get("to") or "")),
    )[:10]
    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "selected_paper_count": len(nodes),
            "edge_count": len(edges),
            "edge_counts_by_type": counts_by_type,
            "strongest_edges": strongest,
        },
        "legend": {
            "cites": {"directed": True, "description": "Direct citation relation among selected papers."},
            "cited_by_selected": {"directed": True, "description": "Reverse view of a selected-paper citation edge."},
            "topic_overlap": {"directed": False, "description": "Weighted topic overlap from OpenAlex topics."},
            "concept_overlap": {"directed": False, "description": "Weighted concept overlap from OpenAlex concepts."},
            "author_overlap": {"directed": False, "description": "Author-name overlap between papers."},
            "shared_references": {"directed": False, "description": "Overlap in referenced works (bibliographic coupling proxy)."},
        },
        "warnings": warnings,
    }


def _synthesis_payload(
    *,
    refs: Sequence[papers_service.PaperRef],
    question: str,
    model: Optional[str],
    top_k: Optional[int],
    session_id: Optional[str],
    request_id: Optional[str],
    history: Sequence[Dict[str, Any]],
    project_id: Optional[str],
    persona_id: Optional[str],
    allow_cross_project_answer_reuse: bool,
    allow_custom_question_sharing: bool,
    user_id: Optional[int],
    conversation_id: Optional[str],
    seed_paper_id: Optional[str],
    on_paper_answer: Optional[Any] = None,
    stream_synthesis: bool = False,
    on_delta: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the full multi-paper synthesis pipeline and return the response payload."""
    settings = load_settings()
    selected_model = str(model or settings.chat_model).strip()
    if not selected_model:
        raise ValueError("model is required.")
    paper_answers = _build_paper_answer_rows(
        refs=refs,
        question=question,
        model=selected_model,
        top_k=top_k,
        session_id=session_id,
        request_id=request_id,
        project_id=project_id,
        persona_id=persona_id,
        allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
        allow_custom_question_sharing=allow_custom_question_sharing,
        user_id=user_id,
        on_paper_answer=on_paper_answer,
    )
    history_context = _multi_history_context(history)
    synth_input = _synthesis_user_input(
        question=question,
        paper_answers=paper_answers,
        history_context=history_context,
    )
    runtime = build_llm_runtime(settings)
    if stream_synthesis and callable(on_delta):
        synthesis_answer = chat_service.stream_llm_answer(
            client=runtime,
            model=selected_model,
            instructions=MULTI_PAPER_SYNTHESIS_PROMPT,
            user_input=synth_input,
            temperature=None,
            usage_context="multi_paper_synthesis",
            session_id=session_id,
            request_id=request_id,
            project_id=project_id,
            persona_id=persona_id,
            on_delta=on_delta,
        ).strip()
    else:
        synthesis_answer = call_llm(
            runtime,
            model=selected_model,
            instructions=MULTI_PAPER_SYNTHESIS_PROMPT,
            user_input=synth_input,
            max_output_tokens=None,
            temperature=None,
            usage_context="multi_paper_synthesis",
            session_id=session_id,
            request_id=request_id,
            meta={"project_id": project_id, "persona_id": persona_id},
        ).strip()
    comparison_summary = _comparison_summary_from_answer(synthesis_answer)
    aggregate_provenance = _aggregate_provenance(paper_answers)
    suggested_papers = suggest_companion_papers(
        paper_ids=[ref.paper_id for ref in refs],
        project_id=project_id,
    )
    return {
        "conversation_id": str(conversation_id or uuid4().hex),
        "answer": synthesis_answer,
        "model": selected_model,
        "request_id": str(request_id or uuid4().hex),
        "scope": {
            "mode": "multi",
            "paper_ids": [ref.paper_id for ref in refs],
            "seed_paper_id": str(seed_paper_id or (refs[0].paper_id if refs else "")),
            "paper_count": len(refs),
        },
        "paper_answers": paper_answers,
        "comparison_summary": comparison_summary,
        "aggregate_provenance": aggregate_provenance,
        "suggested_followups": _suggested_followups(question=question),
        "suggested_papers": suggested_papers,
    }


def multi_chat_turn(
    *,
    paper_ids: Sequence[str],
    question: str,
    model: Optional[str] = None,
    top_k: Optional[int] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    history: Optional[Sequence[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    seed_paper_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one multi-paper synthesis chat turn and return the full payload."""
    refs = _validate_paper_ids(paper_ids, project_id=project_id)
    return _synthesis_payload(
        refs=refs,
        question=str(question or ""),
        model=model,
        top_k=top_k,
        session_id=session_id,
        request_id=request_id,
        history=list(history or []),
        project_id=project_id,
        persona_id=persona_id,
        allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
        allow_custom_question_sharing=allow_custom_question_sharing,
        user_id=user_id,
        conversation_id=conversation_id,
        seed_paper_id=seed_paper_id,
        on_paper_answer=None,
        stream_synthesis=False,
        on_delta=None,
    )


def stream_multi_chat_turn(
    *,
    paper_ids: Sequence[str],
    question: str,
    model: Optional[str] = None,
    top_k: Optional[int] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    history: Optional[Sequence[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    allow_cross_project_answer_reuse: bool = True,
    allow_custom_question_sharing: bool = False,
    user_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    seed_paper_id: Optional[str] = None,
) -> Iterator[str]:
    """Yield NDJSON rows for one streamed multi-paper synthesis chat turn."""
    req_id = str(request_id or uuid4().hex)
    refs = _validate_paper_ids(paper_ids, project_id=project_id)
    conv_id = str(conversation_id or uuid4().hex)
    yield json.dumps(
        {
            "event": "start",
            "request_id": req_id,
            "conversation_id": conv_id,
            "scope": {
                "mode": "multi",
                "paper_ids": [ref.paper_id for ref in refs],
                "seed_paper_id": str(seed_paper_id or (refs[0].paper_id if refs else "")),
                "paper_count": len(refs),
            },
        },
        ensure_ascii=False,
    ) + "\n"

    queued_rows: List[str] = []
    last_delta = ""

    def _emit_paper(row: Dict[str, Any], *, idx: int, total: int) -> None:
        queued_rows.append(
            json.dumps(
                {
                    "event": "paper_answer",
                    "request_id": req_id,
                    "conversation_id": conv_id,
                    "paper_id": str(row.get("paper_id") or ""),
                    "paper_title": str(row.get("paper_title") or row.get("paper_id") or ""),
                    "cache_hit": bool(row.get("cache_hit")) if isinstance(row.get("cache_hit"), bool) else False,
                    "cache_hit_layer": str(row.get("cache_hit_layer") or "none"),
                    "answer_preview": (
                        str(row.get("answer") or "")[:280]
                        + ("..." if len(str(row.get("answer") or "")) > 280 else "")
                    ),
                    "provenance": {
                        "score": float(((row.get("provenance") or {}) if isinstance(row.get("provenance"), dict) else {}).get("score") or 0.0),
                        "status": str(((row.get("provenance") or {}) if isinstance(row.get("provenance"), dict) else {}).get("status") or "low"),
                    },
                    "index": int(idx),
                    "total": int(total),
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    def _on_delta(text: str) -> None:
        nonlocal last_delta
        current = str(text or "")
        delta_text = current[len(last_delta) :] if current.startswith(last_delta) else current
        last_delta = current
        queued_rows.append(
            json.dumps(
                {
                    "event": "delta",
                    "request_id": req_id,
                    "conversation_id": conv_id,
                    "delta": delta_text,
                    "text": current,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    payload = _synthesis_payload(
        refs=refs,
        question=str(question or ""),
        model=model,
        top_k=top_k,
        session_id=session_id,
        request_id=req_id,
        history=list(history or []),
        project_id=project_id,
        persona_id=persona_id,
        allow_cross_project_answer_reuse=allow_cross_project_answer_reuse,
        allow_custom_question_sharing=allow_custom_question_sharing,
        user_id=user_id,
        conversation_id=conv_id,
        seed_paper_id=seed_paper_id,
        on_paper_answer=_emit_paper,
        stream_synthesis=True,
        on_delta=_on_delta,
    )
    while queued_rows:
        yield queued_rows.pop(0)
    yield json.dumps({"event": "done", **payload}, ensure_ascii=False) + "\n"


def _session_rows_for_scope(
    *,
    db_url: str,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str],
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Load candidate multi-chat sessions for one user/project scope."""
    clean_user = str(username or "").strip()
    if not db_url or not clean_user:
        return []
    clean_project = str(project_id or "").strip()
    scope_sql, scope_params = _history_scope_sql(user_id=user_id, username=clean_user)
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    conversation_id,
                    seed_paper_id,
                    paper_ids_json,
                    paper_paths_json,
                    name,
                    created_at,
                    updated_at
                FROM retrieval.multi_chat_sessions
                WHERE (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                [clean_project, clean_project, *scope_params, max(1, min(200, int(limit or 50)))],
            )
            rows = cur.fetchall()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "conversation_id": str(row[0] or ""),
                "seed_paper_id": str(row[1] or ""),
                "paper_ids": [str(v or "") for v in _json_list(row[2]) if str(v or "").strip()],
                "paper_paths": [str(v or "") for v in _json_list(row[3]) if str(v or "").strip()],
                "name": str(row[4] or ""),
                "created_at": _to_iso(row[5]),
                "updated_at": _to_iso(row[6]),
            }
        )
    return out


def ensure_conversation(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str],
    persona_id: Optional[str],
    session_id: Optional[str],
    paper_ids: Sequence[str],
    paper_paths: Sequence[str],
    conversation_id: Optional[str] = None,
    seed_paper_id: Optional[str] = None,
) -> str:
    """Create or validate a multi-chat conversation session row."""
    clean_user = str(username or "").strip()
    clean_project = str(project_id or "").strip()
    ordered_ids = [str(v or "").strip() for v in list(paper_ids or []) if str(v or "").strip()]
    ordered_paths = [str(v or "").strip() for v in list(paper_paths or []) if str(v or "").strip()]
    if not db_url or not clean_user or not ordered_ids:
        return str(conversation_id or uuid4().hex)
    requested = str(conversation_id or "").strip()
    if requested:
        for row in _session_rows_for_scope(
            db_url=db_url,
            user_id=user_id,
            username=clean_user,
            project_id=clean_project,
            limit=100,
        ):
            if str(row.get("conversation_id") or "") != requested:
                continue
            if list(row.get("paper_ids") or []) == ordered_ids:
                return requested
            break
    conv_id = requested or uuid4().hex
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO retrieval.multi_chat_sessions (
                    conversation_id, user_id, username, project_id, persona_id, session_id,
                    seed_paper_id, paper_ids_json, paper_paths_json, name, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s::jsonb, %s, NOW(), NOW()
                )
                ON CONFLICT (conversation_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    username = EXCLUDED.username,
                    project_id = EXCLUDED.project_id,
                    persona_id = EXCLUDED.persona_id,
                    session_id = EXCLUDED.session_id,
                    seed_paper_id = EXCLUDED.seed_paper_id,
                    paper_ids_json = EXCLUDED.paper_ids_json,
                    paper_paths_json = EXCLUDED.paper_paths_json,
                    updated_at = NOW()
                """,
                (
                    conv_id,
                    int(user_id) if user_id is not None else None,
                    clean_user,
                    clean_project or None,
                    str(persona_id or "") or None,
                    str(session_id or "") or None,
                    str(seed_paper_id or "") or None,
                    json.dumps(ordered_ids, ensure_ascii=False),
                    json.dumps(ordered_paths, ensure_ascii=False),
                    " / ".join(ordered_ids[:3]) + (" ..." if len(ordered_ids) > 3 else ""),
                ),
            )
            conn.commit()
    except Exception:
        return conv_id
    return conv_id


def append_turn(
    db_url: str,
    *,
    conversation_id: str,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str],
    persona_id: Optional[str],
    session_id: Optional[str],
    model: Optional[str],
    query: str,
    answer: str,
    paper_ids: Sequence[str],
    paper_answers: Sequence[Dict[str, Any]],
    comparison_summary: Optional[Dict[str, Any]],
    aggregate_provenance: Optional[Dict[str, Any]],
    suggested_papers: Optional[Dict[str, Any]],
    request_id: Optional[str],
) -> None:
    """Persist one multi-paper chat turn."""
    clean_user = str(username or "").strip()
    clean_query = str(query or "").strip()
    clean_answer = str(answer or "").strip()
    if not db_url or not clean_user or not str(conversation_id or "").strip() or not clean_query or not clean_answer:
        return
    rows = [str(v or "").strip() for v in list(paper_ids or []) if str(v or "").strip()]
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO retrieval.multi_chat_turns (
                    conversation_id, user_id, username, project_id, persona_id, session_id, model,
                    query, answer, paper_ids_json, paper_answers_json, comparison_summary_json,
                    aggregate_provenance_json, suggested_papers_json, request_id, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s, NOW()
                )
                """,
                (
                    str(conversation_id or ""),
                    int(user_id) if user_id is not None else None,
                    clean_user,
                    str(project_id or "") or None,
                    str(persona_id or "") or None,
                    str(session_id or "") or None,
                    str(model or "") or None,
                    clean_query,
                    clean_answer,
                    json.dumps(rows, ensure_ascii=False),
                    json.dumps(list(paper_answers or []), ensure_ascii=False),
                    json.dumps(comparison_summary or {}, ensure_ascii=False),
                    json.dumps(aggregate_provenance or {}, ensure_ascii=False),
                    json.dumps(suggested_papers or {}, ensure_ascii=False),
                    str(request_id or "") or None,
                ),
            )
            cur.execute(
                "UPDATE retrieval.multi_chat_sessions SET updated_at = NOW() WHERE conversation_id = %s",
                (str(conversation_id or ""),),
            )
            conn.commit()
    except Exception:
        return


def _resolve_conversation_id_for_history(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str],
    conversation_id: Optional[str],
    paper_ids: Optional[Sequence[str]],
) -> str:
    """Resolve a conversation id from explicit id or latest matching paper set."""
    requested = str(conversation_id or "").strip()
    if requested:
        return requested
    target_ids = [str(v or "").strip() for v in list(paper_ids or []) if str(v or "").strip()]
    if not target_ids:
        return ""
    for row in _session_rows_for_scope(
        db_url=db_url,
        user_id=user_id,
        username=username,
        project_id=project_id,
        limit=100,
    ):
        if list(row.get("paper_ids") or []) == target_ids:
            return str(row.get("conversation_id") or "")
    return ""


def list_turns(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    paper_ids: Optional[Sequence[str]] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return persisted multi-paper chat history rows for one user scope."""
    clean_user = str(username or "").strip()
    if not db_url or not clean_user:
        return {"conversation_id": "", "rows": [], "count": 0}
    row_limit = max(1, min(MAX_MULTI_HISTORY_LIMIT, int(limit or 50)))
    conv_id = _resolve_conversation_id_for_history(
        db_url,
        user_id=user_id,
        username=clean_user,
        project_id=project_id,
        conversation_id=conversation_id,
        paper_ids=paper_ids,
    )
    if not conv_id:
        return {"conversation_id": "", "rows": [], "count": 0}
    clean_project = str(project_id or "").strip()
    scope_sql, scope_params = _history_scope_sql(user_id=user_id, username=clean_user)
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT
                    model, query, answer, paper_ids_json, paper_answers_json,
                    comparison_summary_json, aggregate_provenance_json, suggested_papers_json,
                    request_id, created_at
                FROM retrieval.multi_chat_turns
                WHERE conversation_id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                [conv_id, clean_project, clean_project, *scope_params, row_limit],
            )
            rows = cur.fetchall()
    except Exception:
        return {"conversation_id": conv_id, "rows": [], "count": 0}
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "model": str(row[0] or ""),
                "query": str(row[1] or ""),
                "answer": str(row[2] or ""),
                "paper_ids": [str(v or "") for v in _json_list(row[3]) if str(v or "").strip()],
                "paper_answers": [v for v in _json_list(row[4]) if isinstance(v, dict)],
                "comparison_summary": _json_obj(row[5]),
                "aggregate_provenance": _json_obj(row[6]),
                "suggested_papers": _json_obj(row[7]),
                "request_id": str(row[8] or ""),
                "created_at": _to_iso(row[9]),
            }
        )
    out.reverse()
    return {"conversation_id": conv_id, "rows": out, "count": len(out)}


def clear_turns(
    db_url: str,
    *,
    user_id: Optional[int],
    username: str,
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    paper_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Delete multi-paper chat history rows for one conversation in user scope."""
    clean_user = str(username or "").strip()
    if not db_url or not clean_user:
        return {"conversation_id": "", "deleted_count": 0}
    conv_id = _resolve_conversation_id_for_history(
        db_url,
        user_id=user_id,
        username=clean_user,
        project_id=project_id,
        conversation_id=conversation_id,
        paper_ids=paper_ids,
    )
    if not conv_id:
        return {"conversation_id": "", "deleted_count": 0}
    clean_project = str(project_id or "").strip()
    scope_sql, scope_params = _history_scope_sql(user_id=user_id, username=clean_user)
    try:
        with pooled_connection(db_url) as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM retrieval.multi_chat_turns
                WHERE conversation_id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                """,
                [conv_id, clean_project, clean_project, *scope_params],
            )
            before = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                f"""
                DELETE FROM retrieval.multi_chat_turns
                WHERE conversation_id = %s
                  AND (%s = '' OR COALESCE(project_id, '') = %s)
                  AND {scope_sql}
                """,
                [conv_id, clean_project, clean_project, *scope_params],
            )
            cur.execute(
                "UPDATE retrieval.multi_chat_sessions SET updated_at = NOW() WHERE conversation_id = %s",
                (conv_id,),
            )
            conn.commit()
            return {"conversation_id": conv_id, "deleted_count": before}
    except Exception:
        return {"conversation_id": conv_id, "deleted_count": 0}
