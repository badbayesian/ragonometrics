"""Ingest OpenAlex plaintext export records into paper_openalex_metadata.

This importer accepts plaintext records that look like:
```
PT J
AU Alice
TI Paper title
PY 2020
DI 10.xxxx/yyy
ER
```

Each record is upserted into `enrichment.paper_openalex_metadata` using a
synthetic `paper_path` key (for example `openalex-import/doi/<doi>`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.types.json import Jsonb


DEFAULT_DB_URL = "postgres://postgres:postgres@localhost:5432/ragonometrics"


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    p = argparse.ArgumentParser(description="Ingest OpenAlex plaintext export into Postgres metadata table.")
    p.add_argument("--input", required=True, help="Path to plaintext export file.")
    p.add_argument(
        "--db-url",
        default=(os.environ.get("DATABASE_URL") or DEFAULT_DB_URL),
        help="Postgres URL (defaults to $DATABASE_URL or localhost default).",
    )
    p.add_argument(
        "--source-label",
        default="openalex_plaintext_import",
        help="Source label added into openalex_json.",
    )
    p.add_argument("--dry-run", action="store_true", help="Parse and report only; do not write to DB.")
    return p.parse_args()


def _clean_spaces(text: str) -> str:
    """Normalize whitespace in a string."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _first(record: Dict[str, List[str]], tag: str) -> str:
    """Return the first value for a tag."""
    vals = record.get(tag) or []
    return _clean_spaces(vals[0]) if vals else ""


def _as_int(value: str) -> Optional[int]:
    """Parse integer safely."""
    text = _clean_spaces(value)
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def _slugify(text: str) -> str:
    """Create a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return slug or "untitled"


def _paper_key(*, doi: str, title: str, authors_text: str, year: Optional[int]) -> str:
    """Build synthetic paper_path key for imported rows."""
    doi_clean = _clean_spaces(doi).lower()
    if doi_clean:
        return f"openalex-import/doi/{doi_clean}"
    seed = f"{title}|{authors_text}|{year if year is not None else ''}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"openalex-import/title/{_slugify(title)}-{year if year is not None else 'na'}-{digest}"


def parse_plaintext_records(path: Path) -> List[Dict[str, List[str]]]:
    """Parse OpenAlex plaintext records from file.

    Args:
        path: Plaintext export path.

    Returns:
        Parsed records where each tag maps to a list of values.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    records: List[Dict[str, List[str]]] = []
    current: Dict[str, List[str]] = {}
    current_tag: Optional[str] = None

    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue

        tag_match = re.match(r"^([A-Z0-9]{2})(?:\s(.*))?$", line)
        if tag_match:
            tag = tag_match.group(1)
            value = _clean_spaces(tag_match.group(2) or "")
            current.setdefault(tag, [])
            if value:
                current[tag].append(value)
            current_tag = tag
            if tag == "ER":
                if current:
                    records.append(current)
                current = {}
                current_tag = None
            continue

        if line.startswith("   ") and current_tag:
            value = _clean_spaces(line)
            if value:
                current.setdefault(current_tag, []).append(value)

    if current:
        records.append(current)
    return records


def _build_row_payload(record: Dict[str, List[str]], source_label: str) -> Optional[Dict[str, Any]]:
    """Build database payload for one parsed record."""
    title = _first(record, "TI")
    if not title:
        return None

    authors = [_clean_spaces(x) for x in (record.get("AU") or []) if _clean_spaces(x)]
    if not authors:
        authors = [_clean_spaces(x) for x in (record.get("AF") or []) if _clean_spaces(x)]
    authors_text = ", ".join(authors)

    year = _as_int(_first(record, "PY"))
    doi = _first(record, "DI")
    cited_by = _as_int(_first(record, "CT"))
    referenced_count = _as_int(_first(record, "NR"))
    venue = _first(record, "SO")

    authorships = [{"author": {"display_name": name}} for name in authors]
    openalex_json: Dict[str, Any] = {
        "id": None,
        "display_name": title,
        "publication_year": year,
        "doi": doi or None,
        "authorships": authorships,
        "cited_by_count": cited_by,
        "referenced_works_count": referenced_count,
        "venue": venue or None,
        "source_label": source_label,
        "raw_tags": record,
    }

    paper_path = _paper_key(doi=doi, title=title, authors_text=authors_text, year=year)
    now = datetime.now(timezone.utc)
    return {
        "paper_path": paper_path,
        "title": title,
        "authors": authors_text or "Unknown",
        "query_title": title,
        "query_authors": authors_text or "Unknown",
        "query_year": year,
        "openalex_id": None,
        "openalex_doi": doi or None,
        "openalex_title": title,
        "openalex_publication_year": year,
        "openalex_authors_json": authors,
        "openalex_json": openalex_json,
        "match_status": "matched",
        "error_text": None,
        "created_at": now,
        "updated_at": now,
    }


def upsert_rows(db_url: str, rows: List[Dict[str, Any]]) -> tuple[int, int]:
    """Upsert parsed rows into paper_openalex_metadata.

    Returns:
        Tuple of `(inserted_count, updated_count)`.
    """
    if not rows:
        return (0, 0)

    sql = """
        INSERT INTO enrichment.paper_openalex_metadata (
            paper_path, title, authors, query_title, query_authors, query_year,
            openalex_id, openalex_doi, openalex_title, openalex_publication_year,
            openalex_authors_json, openalex_json, match_status, error_text, created_at, updated_at
        ) VALUES (
            %(paper_path)s, %(title)s, %(authors)s, %(query_title)s, %(query_authors)s, %(query_year)s,
            %(openalex_id)s, %(openalex_doi)s, %(openalex_title)s, %(openalex_publication_year)s,
            %(openalex_authors_json)s, %(openalex_json)s, %(match_status)s, %(error_text)s, %(created_at)s, %(updated_at)s
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
            updated_at = EXCLUDED.updated_at
        RETURNING (xmax = 0) AS inserted
    """

    inserted = 0
    updated = 0
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            for row in rows:
                row_payload = dict(row)
                row_payload["openalex_authors_json"] = Jsonb(row_payload["openalex_authors_json"])
                row_payload["openalex_json"] = Jsonb(row_payload["openalex_json"])
                cur.execute(sql, row_payload)
                result = cur.fetchone()
                if result and result[0]:
                    inserted += 1
                else:
                    updated += 1
        conn.commit()
    return inserted, updated


def main() -> int:
    """CLI entrypoint."""
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[error] Input file not found: {input_path}")
        return 1

    records = parse_plaintext_records(input_path)
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for record in records:
        payload = _build_row_payload(record, source_label=args.source_label)
        if payload is None:
            skipped += 1
            continue
        rows.append(payload)

    print(f"[ok] Parsed records: {len(records)}")
    print(f"[ok] Valid rows: {len(rows)}")
    if skipped:
        print(f"[warn] Skipped rows without title: {skipped}")

    if args.dry_run:
        print("[ok] Dry run complete; no DB writes.")
        return 0

    inserted, updated = upsert_rows(args.db_url, rows)
    print(f"[ok] Upsert complete: inserted={inserted}, updated={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
