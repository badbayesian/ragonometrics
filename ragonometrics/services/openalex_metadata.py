"""OpenAlex metadata shaping service for web surfaces."""

from __future__ import annotations

from typing import Any, Dict, List

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


def _openalex_author_names(meta: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if name:
            names.append(name)
    return names


def _openalex_author_items(meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """Return author objects with OpenAlex links."""
    out: List[Dict[str, str]] = []
    seen = set()
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        raw_id = str(author_obj.get("id") or "").strip()
        if not name:
            continue
        key = f"{name.lower()}::{raw_id.lower()}"
        if key in seen:
            continue
        seen.add(key)
        author_id = raw_id.rsplit("/", 1)[-1] if raw_id else ""
        out.append(
            {
                "name": name,
                "id": raw_id,
                "openalex_url": f"https://openalex.org/{author_id}" if author_id else "",
            }
        )
    return out


def _abstract_from_inverted_index(inv: Any) -> str:
    if not isinstance(inv, dict):
        return ""
    positions: List[int] = []
    for vals in inv.values():
        if isinstance(vals, list):
            positions.extend([item for item in vals if isinstance(item, int)])
    if not positions:
        return ""
    max_pos = max(positions)
    words: List[str] = [""] * (max_pos + 1)
    for token, offsets in inv.items():
        if not isinstance(offsets, list):
            continue
        for pos in offsets:
            if isinstance(pos, int) and 0 <= pos <= max_pos:
                words[pos] = str(token or "")
    return " ".join([word for word in words if word]).strip()


def _work_or_entity_url(raw_id: str) -> str:
    value = str(raw_id or "").strip()
    if not value:
        return ""
    entity_id = value.rsplit("/", 1)[-1]
    return f"https://openalex.org/{entity_id}" if entity_id else ""


def _openalex_venue(meta: Dict[str, Any]) -> str:
    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = str(source.get("display_name") or "").strip()
    if venue:
        return venue
    host = meta.get("host_venue") or {}
    return str(host.get("display_name") or "").strip()


def metadata_for_paper(paper_ref: PaperRef) -> Dict[str, Any]:
    """Return one normalized OpenAlex metadata payload for a selected paper."""
    paper, _, _, _ = load_prepared(paper_ref)
    meta = paper.openalex if isinstance(paper.openalex, dict) else {}
    if not meta:
        return {
            "available": False,
            "paper_id": paper_ref.paper_id,
            "paper_name": paper_ref.name,
            "message": "No OpenAlex metadata found for this paper.",
        }

    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    host = meta.get("host_venue") or {}
    referenced_works_count = meta.get("referenced_works_count")
    if referenced_works_count is None and isinstance(meta.get("referenced_works"), list):
        referenced_works_count = len(meta.get("referenced_works"))
    abstract = _abstract_from_inverted_index(meta.get("abstract_inverted_index"))
    author_items = _openalex_author_items(meta)

    topics: List[Dict[str, Any]] = []
    for item in meta.get("topics") or []:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").strip()
        topics.append(
            {
                "name": str(item.get("display_name") or "").strip(),
                "id": raw_id,
                "openalex_url": _work_or_entity_url(raw_id),
                "score": item.get("score"),
            }
        )

    concepts: List[Dict[str, Any]] = []
    for item in meta.get("concepts") or []:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").strip()
        concepts.append(
            {
                "name": str(item.get("display_name") or "").strip(),
                "id": raw_id,
                "openalex_url": _work_or_entity_url(raw_id),
                "score": item.get("score"),
            }
        )

    return {
        "available": True,
        "paper_id": paper_ref.paper_id,
        "paper_name": paper_ref.name,
        "work": {
            "id": str(meta.get("id") or ""),
            "openalex_url": _openalex_work_url(meta.get("id")),
            "title": str(meta.get("display_name") or meta.get("title") or ""),
            "publication_year": meta.get("publication_year"),
            "doi": str(meta.get("doi") or ""),
            "doi_url": f"https://doi.org/{str(meta.get('doi') or '').replace('https://doi.org/', '').replace('http://doi.org/', '').strip()}"
            if str(meta.get("doi") or "").strip()
            else "",
            "cited_by_count": meta.get("cited_by_count"),
            "referenced_works_count": referenced_works_count,
            "venue": _openalex_venue(meta),
            "landing_url": str(primary.get("landing_page_url") or ""),
            "authors": _openalex_author_names(meta),
            "author_items": author_items,
            "abstract": abstract,
            "source": {
                "name": str(source.get("display_name") or ""),
                "id": str(source.get("id") or ""),
                "openalex_url": _work_or_entity_url(str(source.get("id") or "")),
            },
            "host_venue": {
                "name": str(host.get("display_name") or ""),
                "id": str(host.get("id") or ""),
                "openalex_url": _work_or_entity_url(str(host.get("id") or "")),
            },
            "topics": topics,
            "concepts": concepts,
        },
        "raw": meta,
    }
