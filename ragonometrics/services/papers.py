"""Paper discovery, scoping, and prepared-paper caching helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ragonometrics.core.main import Paper, Settings, embed_texts, load_papers, load_settings, prepare_chunks_for_paper
from ragonometrics.db.connection import pooled_connection
from ragonometrics.llm.runtime import build_llm_runtime


@dataclass(frozen=True)
class PaperRef:
    """Public paper reference exposed to web clients."""

    paper_id: str
    path: str
    name: str


def _fallback_title_from_name(name: str) -> str:
    """Internal helper for fallback title from name."""
    stem = Path(str(name or "")).stem
    return " ".join(stem.replace("_", " ").split())


def _openalex_work_url(value: Any) -> str:
    """Internal helper for openalex work url."""
    text = str(value or "").strip()
    if not text:
        return ""
    token = text.rsplit("/", 1)[-1]
    return f"https://openalex.org/{token}" if token else ""


def _openalex_author_names(meta: Dict[str, Any]) -> List[str]:
    """Internal helper for openalex author names."""
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


def _openalex_author_items(meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """Internal helper for openalex author items."""
    items: List[Dict[str, str]] = []
    seen = set()
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author_obj = authorship.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        raw_id = str(author_obj.get("id") or "").strip()
        if not name:
            continue
        clean_id = raw_id.split("?", 1)[0].rstrip("/")
        if clean_id.startswith("https://api.openalex.org/"):
            clean_id = clean_id.rsplit("/", 1)[-1]
        elif clean_id.startswith("https://openalex.org/"):
            clean_id = clean_id.rsplit("/", 1)[-1]
        openalex_url = f"https://openalex.org/{clean_id}" if clean_id else ""
        dedupe_key = f"{name.lower()}::{openalex_url.lower()}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            {
                "name": name,
                "id": raw_id,
                "openalex_url": openalex_url,
            }
        )
    return items


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


def normalize_paper_path(path: str | Path) -> str:
    """Normalize a paper path for cross-platform matching."""
    return str(path).replace("\\", "/").strip()


def paper_id_for_path(path: str | Path) -> str:
    """Build a deterministic paper id from normalized path."""
    normalized = normalize_paper_path(path)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def list_papers(settings: Optional[Settings] = None) -> List[PaperRef]:
    """List configured paper files as scoped references."""
    resolved_settings = settings or load_settings()
    papers_dir = Path(resolved_settings.papers_dir)
    if not papers_dir.exists():
        return []
    refs: List[PaperRef] = []
    for path in sorted(papers_dir.glob("*.pdf")):
        refs.append(
            PaperRef(
                paper_id=paper_id_for_path(path),
                path=normalize_paper_path(path),
                name=path.name,
            )
        )
    return refs


def resolve_paper(paper_id: str, settings: Optional[Settings] = None) -> Optional[PaperRef]:
    """Resolve a paper id into one allowlisted paper reference."""
    wanted = str(paper_id or "").strip()
    if not wanted:
        return None
    for ref in list_papers(settings=settings):
        if ref.paper_id == wanted:
            return ref
    return None


def paper_overview(ref: PaperRef) -> Dict[str, Any]:
    """Return display metadata for one paper (OpenAlex-first fallback chain)."""
    meta = _db_openalex_metadata_for_paper(Path(ref.path))
    fallback_title = _fallback_title_from_name(ref.name)
    if isinstance(meta, dict) and meta:
        title = str(meta.get("display_name") or meta.get("title") or "").strip() or fallback_title
        authors = _openalex_author_names(meta)
        author_items = _openalex_author_items(meta)
        abstract = _abstract_from_inverted_index(meta.get("abstract_inverted_index"))
        if len(abstract) > 1200:
            abstract = abstract[:1197].rstrip() + "..."
        source = (meta.get("primary_location") or {}).get("source") or {}
        venue = str(source.get("display_name") or (meta.get("host_venue") or {}).get("display_name") or "").strip()
        return {
            "paper_id": ref.paper_id,
            "name": ref.name,
            "path": ref.path,
            "title": title,
            "display_title": title,
            "title_source": "openalex",
            "authors": authors,
            "author_items": author_items,
            "display_authors": ", ".join(authors),
            "abstract": abstract,
            "display_abstract": abstract,
            "openalex_url": _openalex_work_url(meta.get("id")),
            "doi": str(meta.get("doi") or ""),
            "publication_year": meta.get("publication_year"),
            "venue": venue,
            "landing_url": str((meta.get("primary_location") or {}).get("landing_page_url") or ""),
        }
    return {
        "paper_id": ref.paper_id,
        "name": ref.name,
        "path": ref.path,
        "title": fallback_title,
        "display_title": fallback_title,
        "title_source": "filename",
        "authors": [],
        "author_items": [],
        "display_authors": "",
        "abstract": "",
        "display_abstract": "",
        "openalex_url": "",
        "doi": "",
        "publication_year": None,
        "venue": "",
        "landing_url": "",
    }


def _db_openalex_metadata_for_paper(path: Path) -> Optional[Dict[str, Any]]:
    """Load OpenAlex payload from metadata table when available."""
    import os

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        return None
    normalized_path = normalize_paper_path(path)
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


@lru_cache(maxsize=24)
def _load_prepared_cached(path_text: str, settings_fingerprint: str) -> Tuple[Paper, List[Dict[str, Any]], List[List[float]]]:
    """Load one paper + chunks + embeddings with process-local memoization."""
    settings = load_settings()
    path = Path(path_text)
    papers = load_papers([path])
    if not papers:
        raise RuntimeError(f"Paper not found or extraction failed: {path}")
    paper = papers[0]
    if not isinstance(paper.openalex, dict) or not paper.openalex:
        db_openalex = _db_openalex_metadata_for_paper(path)
        if isinstance(db_openalex, dict) and db_openalex:
            paper = replace(paper, openalex=db_openalex)
    chunks = prepare_chunks_for_paper(paper, settings)
    if not chunks:
        return paper, [], []
    runtime = build_llm_runtime(settings)
    chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
    chunk_embeddings = embed_texts(runtime, chunk_texts, settings.embedding_model, settings.batch_size)
    return paper, chunks, chunk_embeddings


def load_prepared(paper_ref: PaperRef, settings: Optional[Settings] = None) -> Tuple[Paper, List[Dict[str, Any]], List[List[float]], Settings]:
    """Resolve one paper into prepared retrieval artifacts."""
    resolved_settings = settings or load_settings()
    fingerprint = str(resolved_settings.config_hash or "default")
    paper, chunks, chunk_embeddings = _load_prepared_cached(paper_ref.path, fingerprint)
    return paper, chunks, chunk_embeddings, resolved_settings
