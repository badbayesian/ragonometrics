"""OpenAlex citation network service for selected paper views."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ragonometrics.integrations.openalex import request_json as openalex_request_json
from ragonometrics.services.papers import PaperRef, load_prepared


def _openalex_work_id(value: Any) -> str:
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
    return text if text.startswith("W") else ""


def _openalex_work_url(value: Any) -> str:
    work_id = _openalex_work_id(value)
    return f"https://openalex.org/{work_id}" if work_id else ""


def _openalex_work_summary(work_id: str, *, include_references: bool = False) -> Optional[Dict[str, Any]]:
    key = _openalex_work_id(work_id)
    if not key:
        return None
    fields = ["id", "display_name", "publication_year", "doi", "cited_by_count"]
    if include_references:
        fields.append("referenced_works")
    url = f"https://api.openalex.org/works/{key}"
    payload = openalex_request_json(url, params={"select": ",".join(fields)}, timeout=20)
    return payload if isinstance(payload, dict) else None


def _work_title(work: Dict[str, Any]) -> str:
    return str(work.get("display_name") or work.get("title") or work.get("id") or "Unknown paper")


def _sort_works_desc(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        works,
        key=lambda w: (-(int(w.get("cited_by_count") or 0)), _work_title(w).lower()),
    )


def _work_row(work: Dict[str, Any], group: str) -> Dict[str, Any]:
    return {
        "id": str(work.get("id") or ""),
        "openalex_url": _openalex_work_url(work.get("id")),
        "title": _work_title(work),
        "publication_year": work.get("publication_year"),
        "doi": str(work.get("doi") or ""),
        "cited_by_count": int(work.get("cited_by_count") or 0),
        "group": group,
    }


def citation_network_for_paper(paper_ref: PaperRef, *, max_references: int = 10, max_citing: int = 10) -> Dict[str, Any]:
    """Return OpenAlex citation neighborhood around the selected paper."""
    paper, _, _, _ = load_prepared(paper_ref)
    meta = paper.openalex if isinstance(paper.openalex, dict) else {}
    center_id = _openalex_work_id(meta.get("id") if isinstance(meta, dict) else "")
    if not center_id:
        return {
            "available": False,
            "paper_id": paper_ref.paper_id,
            "paper_name": paper_ref.name,
            "message": "No OpenAlex work id found for this paper.",
            "center": None,
            "references": [],
            "citing": [],
        }

    max_refs = max(0, min(50, int(max_references)))
    max_cit = max(0, min(50, int(max_citing)))

    center = _openalex_work_summary(center_id, include_references=True) or {}
    if not center:
        return {
            "available": False,
            "paper_id": paper_ref.paper_id,
            "paper_name": paper_ref.name,
            "message": "Unable to load OpenAlex center work.",
            "center": None,
            "references": [],
            "citing": [],
        }

    all_reference_ids = center.get("referenced_works") if isinstance(center.get("referenced_works"), list) else []
    reference_candidate_cap = max(max_refs, int(os.environ.get("OPENALEX_NETWORK_REFERENCE_CANDIDATES", "200")))
    references: List[Dict[str, Any]] = []
    for ref_id in all_reference_ids[:reference_candidate_cap]:
        ref = _openalex_work_summary(str(ref_id), include_references=False)
        if isinstance(ref, dict):
            references.append(ref)
    references = _sort_works_desc(references)[:max_refs]

    citing: List[Dict[str, Any]] = []
    if max_cit > 0:
        search = openalex_request_json(
            "https://api.openalex.org/works",
            params={
                "filter": f"cites:{center_id}",
                "per-page": max_cit,
                "select": "id,display_name,publication_year,doi,cited_by_count",
                "sort": "cited_by_count:desc",
            },
            timeout=20,
        )
        results = (search or {}).get("results") if isinstance(search, dict) else []
        if isinstance(results, list):
            citing = [item for item in results if isinstance(item, dict)]
    citing = _sort_works_desc(citing)[:max_cit]

    center_row = _work_row(center, "center")
    reference_rows = [_work_row(item, "reference") for item in references]
    citing_rows = [_work_row(item, "citing") for item in citing]
    return {
        "available": True,
        "paper_id": paper_ref.paper_id,
        "paper_name": paper_ref.name,
        "center": center_row,
        "references": reference_rows,
        "citing": citing_rows,
        "summary": {
            "references_shown": len(reference_rows),
            "citing_shown": len(citing_rows),
            "max_references": max_refs,
            "max_citing": max_cit,
        },
    }

