"""Store OpenAlex metadata matched by paper title + authors in Postgres."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import psycopg2

from ragonometrics.core.main import load_papers
from ragonometrics.integrations.openalex import fetch_openalex_metadata


def _year_from_path(path: Path) -> int | None:
    """Year from path.

    Args:
        path (Path): Description.

    Returns:
        int | None: Description.
    """
    match = re.search(r"\((\d{4})\)", path.stem)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _openalex_author_names(meta: Dict[str, Any] | None) -> List[str]:
    """Openalex author names.

    Args:
        meta (Dict[str, Any] | None): Description.

    Returns:
        List[str]: Description.
    """
    if not isinstance(meta, dict):
        return []
    names: List[str] = []
    seen = set()
    for item in meta.get("authorships") or []:
        if not isinstance(item, dict):
            continue
        author_obj = item.get("author") or {}
        name = str(author_obj.get("display_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _ensure_table(conn) -> None:
    """Ensure table.

    Args:
        conn (Any): Description.
    """
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS enrichment")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment.paper_openalex_metadata (
            paper_path TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            query_title TEXT NOT NULL,
            query_authors TEXT NOT NULL,
            query_year INTEGER,
            openalex_id TEXT,
            openalex_doi TEXT,
            openalex_title TEXT,
            openalex_publication_year INTEGER,
            openalex_authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            openalex_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            match_status TEXT NOT NULL,
            error_text TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (match_status IN ('matched', 'not_found', 'error'))
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS enrichment_paper_openalex_metadata_updated_idx
        ON enrichment.paper_openalex_metadata(updated_at DESC)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS enrichment_paper_openalex_metadata_openalex_id_idx
        ON enrichment.paper_openalex_metadata(openalex_id)
        """
    )
    conn.commit()


def _has_existing_match(conn, paper_path: str) -> bool:
    """Has existing match.

    Args:
        conn (Any): Description.
        paper_path (str): Description.

    Returns:
        bool: Description.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM enrichment.paper_openalex_metadata
        WHERE paper_path = %s
          AND match_status = 'matched'
        LIMIT 1
        """,
        (paper_path,),
    )
    return cur.fetchone() is not None


def _upsert_row(
    conn,
    *,
    paper_path: str,
    title: str,
    authors: str,
    query_title: str,
    query_authors: str,
    query_year: int | None,
    openalex_meta: Dict[str, Any] | None,
    status: str,
    error_text: str | None = None,
) -> None:
    """Upsert row.

    Args:
        conn (Any): Description.
        paper_path (str): Description.
        title (str): Description.
        authors (str): Description.
        query_title (str): Description.
        query_authors (str): Description.
        query_year (int | None): Description.
        openalex_meta (Dict[str, Any] | None): Description.
        status (str): Description.
        error_text (str | None): Description.
    """
    meta = openalex_meta or {}
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
            %s,
            %s,
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
            match_status = EXCLUDED.match_status,
            error_text = EXCLUDED.error_text,
            updated_at = NOW()
        """,
        (
            paper_path,
            title,
            authors,
            query_title,
            query_authors,
            query_year,
            str(meta.get("id") or "") or None,
            str(meta.get("doi") or "") or None,
            str(meta.get("display_name") or meta.get("title") or "") or None,
            meta.get("publication_year"),
            json.dumps(_openalex_author_names(meta), ensure_ascii=False),
            json.dumps(meta, ensure_ascii=False),
            status,
            error_text,
        ),
    )


def store_openalex_metadata_by_title_author(
    *,
    paper_paths: Iterable[Path],
    db_url: str | None = None,
    progress: bool = True,
    refresh: bool = False,
) -> Dict[str, int]:
    """Match papers by title+authors on OpenAlex and persist results in Postgres.

    Args:
        paper_paths (Iterable[Path]): Description.
        db_url (str | None): Description.
        progress (bool): Description.
        refresh (bool): Description.

    Returns:
        Dict[str, int]: Description.

    Raises:
        Exception: Description.
    """
    resolved_db_url = (db_url or os.environ.get("DATABASE_URL") or "").strip()
    if not resolved_db_url:
        raise RuntimeError("No DB URL provided and DATABASE_URL is not set.")

    previous_db_url = os.environ.get("DATABASE_URL")
    previous_openalex_disable = os.environ.get("OPENALEX_DISABLE")
    os.environ["DATABASE_URL"] = resolved_db_url
    conn = psycopg2.connect(resolved_db_url)
    try:
        _ensure_table(conn)
        paths = [Path(p) for p in paper_paths]
        if not paths:
            return {"total": 0, "matched": 0, "not_found": 0, "error": 0, "skipped": 0}

        # Force local title/author extraction first (without OpenAlex enrichment),
        # then perform explicit title+author OpenAlex lookups below.
        os.environ["OPENALEX_DISABLE"] = "1"
        papers = load_papers(paths, progress=progress, progress_desc="Loading title/author metadata")
        os.environ["OPENALEX_DISABLE"] = "0"
        stats = {"total": len(papers), "matched": 0, "not_found": 0, "error": 0, "skipped": 0}
        for paper in papers:
            path_text = str(paper.path)
            if not refresh and _has_existing_match(conn, path_text):
                stats["skipped"] += 1
                continue

            query_title = str(paper.title or "").strip() or paper.path.stem
            query_authors = str(paper.author or "").strip() or "Unknown"
            query_year = _year_from_path(paper.path)
            try:
                meta = fetch_openalex_metadata(
                    title=query_title,
                    author=query_authors,
                    year=query_year,
                    doi=None,
                )
                if meta:
                    status = "matched"
                    stats["matched"] += 1
                else:
                    status = "not_found"
                    stats["not_found"] += 1
                _upsert_row(
                    conn,
                    paper_path=path_text,
                    title=query_title,
                    authors=query_authors,
                    query_title=query_title,
                    query_authors=query_authors,
                    query_year=query_year,
                    openalex_meta=meta,
                    status=status,
                    error_text=None,
                )
            except Exception as exc:  # noqa: BLE001
                stats["error"] += 1
                _upsert_row(
                    conn,
                    paper_path=path_text,
                    title=query_title,
                    authors=query_authors,
                    query_title=query_title,
                    query_authors=query_authors,
                    query_year=query_year,
                    openalex_meta=None,
                    status="error",
                    error_text=str(exc),
                )
            conn.commit()
        return stats
    finally:
        conn.close()
        if previous_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_db_url
        if previous_openalex_disable is None:
            os.environ.pop("OPENALEX_DISABLE", None)
        else:
            os.environ["OPENALEX_DISABLE"] = previous_openalex_disable
