"""Persist paper-level metadata to Postgres without building vector indexes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ragonometrics.core.main import extract_dois_from_text, load_papers, load_settings
from ragonometrics.indexing import metadata


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8", errors="ignore"))


def _doc_id_for_paper(path: Path, text: str) -> str:
    try:
        file_bytes = path.read_bytes()
    except Exception:
        file_bytes = b""
    file_hash = _sha256_bytes(file_bytes)
    text_hash = _sha256_text(text)
    return _sha256_text(f"{file_hash}:{text_hash}")


def _hashes_for_paper(path: Path, text: str) -> tuple[str, str]:
    try:
        file_bytes = path.read_bytes()
    except Exception:
        file_bytes = b""
    file_hash = _sha256_bytes(file_bytes)
    text_hash = _sha256_text(text)
    return file_hash, text_hash


def _dedupe_keep_order(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        v = str(value or "").strip()
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _split_author_names(author_text: str) -> List[str]:
    text = str(author_text or "").strip()
    if not text:
        return []
    tmp = text.replace(" and ", ", ").replace(" & ", ", ").replace(";", ",")
    return _dedupe_keep_order([p.strip() for p in tmp.split(",") if p.strip()])


def _openalex_author_names(openalex_meta: Dict[str, Any] | None) -> List[str]:
    if not openalex_meta:
        return []
    names: List[str] = []
    for item in openalex_meta.get("authorships") or []:
        if not isinstance(item, dict):
            continue
        author_obj = item.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if name:
            names.append(name)
    return _dedupe_keep_order(names)


def _openalex_venue(openalex_meta: Dict[str, Any] | None) -> str | None:
    if not openalex_meta:
        return None
    primary = openalex_meta.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = source.get("display_name")
    if venue:
        return str(venue)
    host = openalex_meta.get("host_venue") or {}
    venue = host.get("display_name")
    return str(venue) if venue else None


def _openalex_source_url(openalex_meta: Dict[str, Any] | None) -> str | None:
    if not openalex_meta:
        return None
    primary = openalex_meta.get("primary_location") or {}
    landing = primary.get("landing_page_url")
    if landing:
        return str(landing)
    oa_id = openalex_meta.get("id")
    return str(oa_id) if oa_id else None


def store_paper_metadata(
    *,
    paper_paths: Iterable[Path],
    meta_db_url: str | None = None,
    progress: bool = True,
) -> int:
    """Load papers and upsert paper-level metadata rows into Postgres."""
    db_url = meta_db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("No metadata DB URL provided and DATABASE_URL not set")

    path_list = [Path(p) for p in paper_paths]
    if not path_list:
        return 0

    conn = metadata.init_metadata_db(db_url)
    try:
        papers = load_papers(path_list, progress=progress, progress_desc="Loading paper metadata")
        upserted = 0
        for paper in papers:
            file_hash, text_hash = _hashes_for_paper(paper.path, paper.text)
            doc_id = _sha256_text(f"{file_hash}:{text_hash}")
            dois = extract_dois_from_text(paper.text)
            openalex_meta = paper.openalex or {}
            citec_meta = paper.citec or {}
            openalex_doi = str(openalex_meta.get("doi") or "").strip() or None
            primary_doi = openalex_doi or (dois[0] if dois else None)
            author_names = _openalex_author_names(openalex_meta if openalex_meta else None) or _split_author_names(
                paper.author
            )
            publication_year = openalex_meta.get("publication_year")
            try:
                publication_year = int(publication_year) if publication_year is not None else None
            except Exception:
                publication_year = None

            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO ingestion.documents
                (doc_id, path, title, author, extracted_at, file_hash, text_hash)
                VALUES (%s, %s, %s, %s, NOW(), %s, %s)
                ON CONFLICT (doc_id) DO UPDATE SET
                    path = EXCLUDED.path,
                    title = EXCLUDED.title,
                    author = EXCLUDED.author,
                    file_hash = EXCLUDED.file_hash,
                    text_hash = EXCLUDED.text_hash
                """,
                (
                    doc_id,
                    str(paper.path),
                    paper.title,
                    paper.author,
                    file_hash,
                    text_hash,
                ),
            )

            metadata.upsert_paper_metadata(
                conn,
                doc_id=doc_id,
                path=str(paper.path),
                title=paper.title,
                author=paper.author,
                authors=author_names,
                primary_doi=primary_doi,
                dois=dois,
                openalex_id=str(openalex_meta.get("id") or "") or None,
                openalex_doi=openalex_doi,
                publication_year=publication_year,
                venue=_openalex_venue(openalex_meta if openalex_meta else None),
                repec_handle=str(citec_meta.get("repec_handle") or "") or None,
                source_url=_openalex_source_url(openalex_meta if openalex_meta else None),
                openalex_json=openalex_meta if openalex_meta else None,
                citec_json=citec_meta if citec_meta else None,
                metadata_json={
                    "doc_id": doc_id,
                    "title": paper.title,
                    "author": paper.author,
                    "authors": author_names,
                    "dois": dois,
                    "openalex_present": bool(openalex_meta),
                    "citec_present": bool(citec_meta),
                },
            )
            upserted += 1
        return upserted
    finally:
        conn.close()


def main() -> None:
    """CLI entrypoint for metadata-only ingestion."""
    import argparse

    parser = argparse.ArgumentParser(description="Store paper metadata in Postgres without building vectors")
    parser.add_argument("--papers-dir", type=str, default=None, help="Directory containing PDFs (default: settings)")
    parser.add_argument("--meta-db-url", type=str, default=None, help="Postgres database URL")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of papers to process (0 = all)")
    args = parser.parse_args()

    settings = load_settings()
    papers_dir = Path(args.papers_dir) if args.papers_dir else settings.papers_dir
    paper_paths = sorted(papers_dir.glob("*.pdf"))
    if args.limit and args.limit > 0:
        paper_paths = paper_paths[: args.limit]
    if not paper_paths:
        raise SystemExit("No PDF files found to store metadata.")

    count = store_paper_metadata(paper_paths=paper_paths, meta_db_url=args.meta_db_url, progress=True)
    print(f"Stored metadata for {count} paper(s).")


if __name__ == "__main__":
    main()
