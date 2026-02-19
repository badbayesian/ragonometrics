"""OpenAlex metadata shaping service for web surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import requests

from ragonometrics.db.connection import pooled_connection

from ragonometrics.services.papers import PaperRef, load_prepared


def _openalex_work_id(value: Any) -> str:
    """Internal helper for openalex work id."""
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
    """Internal helper for openalex work url."""
    work_id = _openalex_work_id(value)
    return f"https://openalex.org/{work_id}" if work_id else ""


def _normalize_openalex_api_url(value: str) -> str:
    """Internal helper for normalize openalex api url."""
    text = str(value or "").strip()
    if not text:
        return ""
    clean = text.split("?", 1)[0].rstrip("/")
    lower = clean.lower()
    if lower.startswith("https://openalex.org/"):
        token = clean.rsplit("/", 1)[-1]
        if token:
            return f"https://api.openalex.org/works/{token}"
        return ""
    if lower.startswith("https://api.openalex.org/works/"):
        return clean
    if lower.startswith("https://api.openalex.org/"):
        token = clean.rsplit("/", 1)[-1]
        if token:
            return f"https://api.openalex.org/works/{token}"
    return ""


def _author_names(meta: Dict[str, Any]) -> List[str]:
    """Internal helper for author names."""
    out: List[str] = []
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
        out.append(name)
    return out


def _normalize_path_text(path_text: str) -> str:
    """Internal helper for normalize path text."""
    return str(path_text or "").replace("\\", "/").strip()


def _alias_paths_for_paper(*, paper_path: str, existing_paths: List[str]) -> List[str]:
    """Internal helper for alias paths for paper."""
    filename = Path(str(paper_path or "")).name
    aliases = set()
    if paper_path:
        aliases.add(paper_path)
        aliases.add(_normalize_path_text(paper_path))
    if filename:
        aliases.add(f"/app/papers/{filename}")
        aliases.add(f"papers\\{filename}")
    for item in existing_paths:
        text = str(item or "").strip()
        if text:
            aliases.add(text)
    return sorted(item for item in aliases if str(item).strip())


def _upsert_openalex_metadata_rows(
    *,
    db_url: str,
    alias_paths: List[str],
    openalex_meta: Dict[str, Any],
) -> int:
    """Internal helper for upsert openalex metadata rows."""
    if not alias_paths:
        return 0
    title = str(openalex_meta.get("display_name") or openalex_meta.get("title") or "").strip()
    authors = _author_names(openalex_meta)
    authors_text = ", ".join(authors)
    query_year = openalex_meta.get("publication_year")
    if not isinstance(query_year, int):
        query_year = None
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        for paper_path in alias_paths:
            cur.execute(
                """
                INSERT INTO enrichment.paper_openalex_metadata (
                    paper_path,
                    title,
                    authors,
                    query_title,
                    query_authors,
                    query_year,
                    openalex_id,
                    openalex_doi,
                    openalex_title,
                    openalex_publication_year,
                    openalex_authors_json,
                    openalex_json,
                    match_status,
                    error_text,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,'matched',NULL,NOW(),NOW()
                )
                ON CONFLICT (paper_path) DO UPDATE SET
                    title = EXCLUDED.title,
                    authors = EXCLUDED.authors,
                    query_title = EXCLUDED.query_title,
                    query_authors = EXCLUDED.query_authors,
                    query_year = EXCLUDED.query_year,
                    openalex_id = EXCLUDED.openalex_id,
                    openalex_doi = EXCLUDED.openalex_doi,
                    openalex_title = EXCLUDED.openalex_title,
                    openalex_publication_year = EXCLUDED.openalex_publication_year,
                    openalex_authors_json = EXCLUDED.openalex_authors_json,
                    openalex_json = EXCLUDED.openalex_json,
                    match_status = 'matched',
                    error_text = NULL,
                    updated_at = NOW()
                """,
                (
                    paper_path,
                    title,
                    authors_text,
                    title,
                    authors_text,
                    query_year,
                    str(openalex_meta.get("id") or "") or None,
                    str(openalex_meta.get("doi") or "") or None,
                    title or None,
                    openalex_meta.get("publication_year"),
                    json.dumps(authors, ensure_ascii=False),
                    json.dumps(openalex_meta, ensure_ascii=False),
                ),
            )
        conn.commit()
    return len(alias_paths)


def manual_link_openalex_for_paper(
    *,
    paper_ref: PaperRef,
    openalex_api_url: str,
    db_url: str,
) -> Dict[str, Any]:
    """Persist one manual OpenAlex match for the selected paper."""
    api_url = _normalize_openalex_api_url(openalex_api_url)
    if not api_url:
        raise ValueError("Invalid OpenAlex URL. Use https://api.openalex.org/... or https://openalex.org/...")
    if not str(db_url or "").strip():
        raise ValueError("DATABASE_URL is required for manual OpenAlex linking.")

    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload:
        raise ValueError("OpenAlex response was empty.")

    filename = Path(str(paper_ref.path or "")).name
    with pooled_connection(db_url) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT paper_path
            FROM enrichment.paper_openalex_metadata
            WHERE lower(replace(paper_path, '\\', '/')) LIKE %s
            """,
            (f"%/{filename.lower()}",),
        )
        existing_paths = [str((row or [None])[0] or "") for row in (cur.fetchall() or [])]

    aliases = _alias_paths_for_paper(paper_path=paper_ref.path, existing_paths=existing_paths)
    updated = _upsert_openalex_metadata_rows(
        db_url=db_url,
        alias_paths=aliases,
        openalex_meta=payload,
    )
    openalex_id = str(payload.get("id") or "").strip()
    return {
        "paper_id": paper_ref.paper_id,
        "paper_name": paper_ref.name,
        "paper_path": paper_ref.path,
        "openalex_id": openalex_id,
        "openalex_url": _openalex_work_url(openalex_id),
        "openalex_title": str(payload.get("display_name") or payload.get("title") or "").strip(),
        "publication_year": payload.get("publication_year"),
        "aliases_updated": int(updated),
    }


def _openalex_author_names(meta: Dict[str, Any]) -> List[str]:
    """Internal helper for openalex author names."""
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
    """Internal helper for abstract from inverted index."""
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
    """Internal helper for work or entity url."""
    value = str(raw_id or "").strip()
    if not value:
        return ""
    entity_id = value.rsplit("/", 1)[-1]
    return f"https://openalex.org/{entity_id}" if entity_id else ""


def _openalex_venue(meta: Dict[str, Any]) -> str:
    """Internal helper for openalex venue."""
    primary = meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = str(source.get("display_name") or "").strip()
    if venue:
        return venue
    host = meta.get("host_venue") or {}
    return str(host.get("display_name") or "").strip()


def _entity_items(items: Any) -> List[Dict[str, Any]]:
    """Normalize OpenAlex topic/concept entities with links and scores."""
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "").strip()
        out.append(
            {
                "name": str(item.get("display_name") or "").strip(),
                "id": raw_id,
                "openalex_url": _work_or_entity_url(raw_id),
                "score": item.get("score"),
            }
        )
    return out


def _doi_url(value: Any) -> str:
    """Return normalized https DOI URL from raw OpenAlex DOI value."""
    doi = str(value or "").strip()
    if not doi:
        return ""
    token = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    return f"https://doi.org/{token}" if token else ""


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
    topics = _entity_items(meta.get("topics"))
    concepts = _entity_items(meta.get("concepts"))

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
            "doi_url": _doi_url(meta.get("doi")),
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
