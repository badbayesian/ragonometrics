"""Manually upsert OpenAlex metadata for one local paper.

Usage examples:
  python tools/manual_openalex_link.py \
    --paper "Impact of Restaurant Hygiene Grade Cards on Foodborne-Disease Hospitalizations in Los Angeles County - Simon et al. (2005).pdf" \
    --openalex-api-url "https://api.openalex.org/w28470166"

  python tools/manual_openalex_link.py \
    --paper "alcott food deserts.pdf" \
    --openalex-api-url "https://api.openalex.org/w2914218338" \
    --db-url "postgres://postgres:postgres@localhost:5432/ragonometrics"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests

from ragonometrics.db.connection import connect, ensure_schema_ready


def _normalize_path_text(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/").strip()


def _fallback_title_from_name(name: str) -> str:
    stem = Path(str(name or "")).stem
    return " ".join(stem.replace("_", " ").split())


def _is_openalex_like_url(url: str) -> bool:
    text = str(url or "").strip().lower()
    return text.startswith("https://api.openalex.org/") or text.startswith("https://openalex.org/")


def _author_names_from_meta(meta: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    seen = set()
    for authorship in meta.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = str(author.get("display_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _resolve_paper_input(paper_input: str, papers_dir: Path) -> Path:
    text = str(paper_input or "").strip()
    if not text:
        raise ValueError("--paper is required")

    direct = Path(text)
    if direct.exists() and direct.is_file():
        return direct.resolve()

    if papers_dir.exists():
        exact = papers_dir / text
        if exact.exists() and exact.is_file():
            return exact.resolve()

    if papers_dir.exists():
        needle = text.lower()
        matches = [
            item.resolve()
            for item in papers_dir.glob("*.pdf")
            if needle in item.name.lower() or needle in item.stem.lower()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(m.name for m in matches[:8])
            raise ValueError(f"Ambiguous --paper value; multiple matches found: {names}")

    raise FileNotFoundError(f"Paper not found from input {text!r} (searched {papers_dir})")


def _build_alias_paths(paper_path: Path, existing_rows: Iterable[str]) -> List[str]:
    filename = paper_path.name
    aliases = set()
    aliases.add(_normalize_path_text(str(paper_path)))
    aliases.add(_normalize_path_text(f"/app/papers/{filename}"))
    aliases.add(f"papers\\{filename}")
    for row_path in existing_rows:
        normalized = _normalize_path_text(row_path)
        if normalized.endswith(f"/{filename.lower()}") or normalized.lower().endswith(f"/{filename.lower()}"):
            aliases.add(str(row_path))
    out = [item for item in aliases if str(item).strip()]
    out.sort()
    return out


def _upsert_openalex_row(
    conn,
    *,
    paper_path: str,
    query_title: str,
    query_authors: str,
    query_year: int | None,
    openalex_meta: Dict[str, Any],
) -> None:
    title = str(openalex_meta.get("display_name") or openalex_meta.get("title") or query_title or "").strip()
    if not title:
        title = _fallback_title_from_name(Path(paper_path).name)
    author_names = _author_names_from_meta(openalex_meta)
    authors_text = ", ".join(author_names)
    cur = conn.cursor()
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
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s::jsonb,
            %s::jsonb,
            'matched',
            NULL,
            NOW(),
            NOW()
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
            query_title,
            query_authors,
            query_year,
            str(openalex_meta.get("id") or "") or None,
            str(openalex_meta.get("doi") or "") or None,
            str(openalex_meta.get("display_name") or openalex_meta.get("title") or "") or None,
            openalex_meta.get("publication_year"),
            json.dumps(author_names, ensure_ascii=False),
            json.dumps(openalex_meta, ensure_ascii=False),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually link a paper to an OpenAlex work API URL.")
    parser.add_argument("--paper", required=True, help="Paper file path, filename, or unique filename substring.")
    parser.add_argument("--openalex-api-url", required=True, help="OpenAlex API/canonical work URL.")
    parser.add_argument("--db-url", default="", help="Postgres URL; defaults to DATABASE_URL.")
    parser.add_argument("--papers-dir", default="", help="Paper directory; defaults to PAPERS_DIR or ./papers.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print planned actions without DB writes.")
    args = parser.parse_args()

    openalex_url = str(args.openalex_api_url or "").strip()
    if not _is_openalex_like_url(openalex_url):
        raise SystemExit("Invalid --openalex-api-url: expected https://api.openalex.org/... or https://openalex.org/...")

    db_url = str(args.db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        raise SystemExit("No DB URL provided. Set --db-url or DATABASE_URL.")

    papers_dir_text = str(args.papers_dir or os.environ.get("PAPERS_DIR") or "papers").strip()
    papers_dir = Path(papers_dir_text)

    try:
        paper_path = _resolve_paper_input(args.paper, papers_dir)
    except Exception as exc:
        raise SystemExit(str(exc))

    response = requests.get(openalex_url, timeout=30)
    response.raise_for_status()
    meta = response.json()
    if not isinstance(meta, dict) or not meta:
        raise SystemExit("OpenAlex response was empty or non-object JSON.")

    query_title = str(meta.get("display_name") or meta.get("title") or "").strip() or _fallback_title_from_name(paper_path.name)
    author_names = _author_names_from_meta(meta)
    query_authors = ", ".join(author_names)
    publication_year = meta.get("publication_year")
    query_year = int(publication_year) if isinstance(publication_year, int) else None

    if args.dry_run:
        print("dry_run=true")
        print(f"paper_path={_normalize_path_text(str(paper_path))}")
        print(f"openalex_id={str(meta.get('id') or '').strip()}")
        print(f"title={query_title}")
        print(f"authors={query_authors}")
        print(f"publication_year={query_year}")
        return 0

    conn = connect(db_url, require_migrated=True)
    try:
        ensure_schema_ready(conn)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT paper_path
            FROM enrichment.paper_openalex_metadata
            WHERE lower(replace(paper_path, '\\', '/')) LIKE %s
            """,
            (f"%/{paper_path.name.lower()}",),
        )
        existing_paths = [str(row[0] or "") for row in cur.fetchall() or []]
        alias_paths = _build_alias_paths(paper_path, existing_paths)
        for alias_path in alias_paths:
            _upsert_openalex_row(
                conn,
                paper_path=str(alias_path),
                query_title=query_title,
                query_authors=query_authors,
                query_year=query_year,
                openalex_meta=meta,
            )
        conn.commit()
    finally:
        conn.close()

    print("manual_openalex_link: updated")
    print(f"paper={paper_path.name}")
    print(f"openalex_id={str(meta.get('id') or '').strip()}")
    print(f"title={query_title}")
    print(f"aliases_updated={len(alias_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
