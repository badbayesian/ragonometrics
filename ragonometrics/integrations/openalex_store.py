"""Store OpenAlex metadata matched by paper title + authors in Postgres."""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openai import OpenAI

from ragonometrics.db.connection import connect, ensure_schema_ready
from ragonometrics.core.main import load_papers
from ragonometrics.core.io_loaders import run_pdftotext_pages
from ragonometrics.integrations.openalex import (
    fetch_openalex_metadata,
    get_title_override_work_id,
    is_economics_work,
    list_works_for_author,
    search_authors_by_name,
)


TITLE_EXTRACTION_PROMPT = (
    "You extract bibliographic metadata from a paper's first page. "
    "Return only valid JSON in this exact shape: {\"title\": \"...\"}. "
    "Use the full paper title, remove trailing footnote markers like * or daggers, "
    "and do not include authors, abstract text, or extra keys."
)


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


def _normalize_candidate_title(title: str) -> str:
    """Normalize a model- or parser-produced title guess.

    Args:
        title (str): Raw candidate title.

    Returns:
        str: Cleaned title string.
    """
    text = str(title or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip('"').strip("'")
    text = re.sub(r"[\*\u2020\u2021]+$", "", text).strip()
    return text


def _title_key(title: str) -> str:
    """Build a normalized title key for fuzzy matching.

    Args:
        title (str): Title text.

    Returns:
        str: Lowercased alphanumeric title key.
    """
    text = _normalize_candidate_title(title).lower()
    if not text:
        return ""
    text = text.replace("_", " ")
    text = re.sub(r"\bet\s+al\.?\b", " ", text)
    text = re.sub(r"\(\d{4}\)", " ", text)
    text = re.sub(r"\b\d{4}\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _titles_match(query_title: str, candidate_title: str) -> bool:
    """Heuristic title matcher between query and candidate work title.

    Args:
        query_title (str): Query title guess.
        candidate_title (str): Candidate OpenAlex work title.

    Returns:
        bool: Whether titles appear to refer to the same paper.
    """
    left = _title_key(query_title)
    right = _title_key(candidate_title)
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) > 12 and left in right:
        return True
    if len(right) > 12 and right in left:
        return True
    score = SequenceMatcher(a=left, b=right).ratio()
    if score >= 0.82:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if len(left_tokens) >= 4 and len(right_tokens) >= 4:
        overlap = len(left_tokens & right_tokens)
        ratio = overlap / max(1, len(left_tokens))
        if ratio >= 0.75:
            return True
    return False


def _normalize_openalex_work_id(value: Any) -> str:
    """Normalize OpenAlex work id/URL to ``W...`` form.

    Args:
        value (Any): Work identifier or URL.

    Returns:
        str: Normalized work id, or empty string.
    """
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


def _author_name_candidates(authors_text: str) -> List[str]:
    """Split an authors string into candidate person names.

    Args:
        authors_text (str): Combined authors string.

    Returns:
        List[str]: Candidate names to query on OpenAlex authors endpoint.
    """
    text = str(authors_text or "").strip()
    if not text:
        return []
    lowered = text.lower()
    if lowered in {"unknown", "n/a", "none"}:
        return []
    cleaned = re.sub(r"\bet\s+al\.?\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+and\s+", ",", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("&", ",")
    raw_parts = [part.strip() for part in re.split(r"[;,]", cleaned) if part.strip()]
    out: List[str] = []
    seen = set()
    for part in raw_parts:
        normalized = re.sub(r"\s+", " ", part).strip()
        if len(normalized) < 4:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _find_economics_match_via_author_catalog(
    *,
    query_title: str,
    query_authors: str,
    query_year: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Look up author catalogs and find an economics work matching title.

    Args:
        query_title (str): Target paper title.
        query_authors (str): Author list string.
        query_year (Optional[int]): Year hint.

    Returns:
        Optional[Dict[str, Any]]: Matching OpenAlex work, or ``None``.
    """
    author_candidates = _author_name_candidates(query_authors)[:3]
    if not author_candidates:
        return None

    for author_name in author_candidates:
        author_records = search_authors_by_name(author_name, limit=5, timeout=10)
        for author_record in author_records[:3]:
            author_id = str(author_record.get("id") or "").strip()
            if not author_id:
                continue
            works = list_works_for_author(
                author_id,
                per_page=50,
                max_pages=3,
                timeout=10,
            )
            for work in works:
                work_title = str(work.get("display_name") or work.get("title") or "").strip()
                if not _titles_match(query_title, work_title):
                    continue
                work_year = work.get("publication_year")
                if (
                    query_year is not None
                    and work_year is not None
                    and abs(int(work_year) - int(query_year)) > 1
                ):
                    continue
                if not is_economics_work(work):
                    continue
                return work
    return None


def _is_acceptable_openalex_match(
    *,
    query_title: str,
    query_year: Optional[int],
    expected_work_id: Optional[str],
    meta: Optional[Dict[str, Any]],
) -> bool:
    """Check if an OpenAlex work is a plausible match for a target paper.

    Args:
        query_title (str): Target title.
        query_year (Optional[int]): Optional year hint.
        expected_work_id (Optional[str]): Forced work id from overrides table.
        meta (Optional[Dict[str, Any]]): OpenAlex work payload.

    Returns:
        bool: ``True`` when economics classification, title, and year align.
    """
    if not isinstance(meta, dict) or not meta:
        return False
    candidate_title = str(meta.get("display_name") or meta.get("title") or "").strip()
    if not _titles_match(query_title, candidate_title):
        return False
    candidate_work_id = _normalize_openalex_work_id(meta.get("id"))
    work_year = meta.get("publication_year")
    if (
        query_year is not None
        and work_year is not None
        and abs(int(work_year) - int(query_year)) > 1
    ):
        return False
    expected_id = _normalize_openalex_work_id(expected_work_id)
    if expected_id and candidate_work_id == expected_id:
        return True
    if not is_economics_work(meta):
        return False
    return True


def _parse_title_json(text: str) -> str:
    """Parse ``{\"title\": ...}`` from an LLM response.

    Args:
        text (str): Raw response text.

    Returns:
        str: Parsed title, or empty string.
    """
    payload_text = str(text or "").strip()
    if not payload_text:
        return ""
    candidates: List[str] = [payload_text]
    match = re.search(r"\{[\s\S]*\}", payload_text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        title = _normalize_candidate_title(str(data.get("title") or ""))
        if title:
            return title
    return ""


def _extract_title_from_first_page_with_ai(
    *,
    paper_path: Path,
    fallback_title: str,
    first_page_text: Optional[str] = None,
) -> Optional[str]:
    """Infer a paper title from page 1 text using an LLM fallback.

    Args:
        paper_path (Path): PDF path for context.
        fallback_title (str): Existing title guess used for guidance.
        first_page_text (Optional[str]): Already-extracted page 1 text.

    Returns:
        Optional[str]: Model-inferred title, or ``None`` if unavailable.
    """
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    page_text = str(first_page_text or "").strip()
    if not page_text:
        try:
            pages = run_pdftotext_pages(paper_path)
            page_text = str((pages or [""])[0]).strip()
        except Exception:
            page_text = ""
    if not page_text:
        return None

    model = (os.environ.get("OPENALEX_TITLE_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5-nano").strip()
    client = OpenAI(api_key=api_key)
    user_input = (
        f"File name: {paper_path.name}\n"
        f"Current extracted title guess: {fallback_title}\n\n"
        "First page text:\n"
        f"{page_text[:7000]}"
    )
    try:
        response = client.responses.create(
            model=model,
            instructions=TITLE_EXTRACTION_PROMPT,
            input=user_input,
            max_output_tokens=180,
        )
    except Exception:
        return None

    output_text = str(getattr(response, "output_text", "") or "").strip()
    if not output_text:
        try:
            chunks: List[str] = []
            for item in getattr(response, "output", []) or []:
                if getattr(item, "type", None) != "message":
                    continue
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", None) == "output_text":
                        chunk = str(getattr(content, "text", "") or "").strip()
                        if chunk:
                            chunks.append(chunk)
            output_text = "\n".join(chunks).strip()
        except Exception:
            output_text = ""
    title = _parse_title_json(output_text)
    if not title:
        return None
    return title


def _resolve_openalex_metadata_for_paper(
    *,
    paper_path: Path,
    query_title: str,
    query_authors: str,
    query_year: Optional[int],
    first_page_text: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    """Resolve an economics OpenAlex match with AI title fallback.

    Args:
        paper_path (Path): PDF path.
        query_title (str): Initial title guess.
        query_authors (str): Author string used in lookup query.
        query_year (Optional[int]): Year hint.
        first_page_text (Optional[str]): First page text for title fallback.

    Returns:
        Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
            ``(metadata, effective_query_title, note)`` where ``metadata`` is
            ``None`` when no economics-classified match is found.
    """
    attempted_titles: List[str] = []
    initial_title = str(query_title or "").strip() or paper_path.stem
    attempted_titles.append(initial_title)
    initial_expected_work_id = get_title_override_work_id(initial_title)

    first_meta = fetch_openalex_metadata(
        title=initial_title,
        author=query_authors,
        year=query_year,
        doi=None,
    )
    if _is_acceptable_openalex_match(
        query_title=initial_title,
        query_year=query_year,
        expected_work_id=initial_expected_work_id,
        meta=first_meta,
    ):
        return first_meta, initial_title, None

    author_meta = _find_economics_match_via_author_catalog(
        query_title=initial_title,
        query_authors=query_authors,
        query_year=query_year,
    )
    if author_meta:
        return author_meta, initial_title, "Resolved via author-catalog fallback."

    has_openai_key = bool((os.environ.get("OPENAI_API_KEY") or "").strip())

    ai_title = _extract_title_from_first_page_with_ai(
        paper_path=paper_path,
        fallback_title=initial_title,
        first_page_text=first_page_text,
    )
    ai_title_clean = _normalize_candidate_title(ai_title or "")
    if ai_title_clean and ai_title_clean.lower() != initial_title.lower():
        attempted_titles.append(ai_title_clean)
        second_meta = fetch_openalex_metadata(
            title=ai_title_clean,
            author=query_authors,
            year=query_year,
            doi=None,
        )
        if _is_acceptable_openalex_match(
            query_title=ai_title_clean,
            query_year=query_year,
            expected_work_id=get_title_override_work_id(ai_title_clean),
            meta=second_meta,
        ):
            return second_meta, ai_title_clean, "Resolved via first-page AI title fallback."
        author_meta_ai = _find_economics_match_via_author_catalog(
            query_title=ai_title_clean,
            query_authors=query_authors,
            query_year=query_year,
        )
        if author_meta_ai:
            return author_meta_ai, ai_title_clean, "Resolved via AI-title + author-catalog fallback."

    note = "No economics OpenAlex match."
    if len(attempted_titles) > 1:
        note = f"{note} Tried titles: {attempted_titles[0]!r} -> {attempted_titles[1]!r}."
    elif not has_openai_key:
        note = (
            f"{note} AI first-page title fallback skipped because OPENAI_API_KEY is not set. "
            f"Could not find economics match for title {attempted_titles[0]!r}."
        )
    elif first_meta and not _is_acceptable_openalex_match(
        query_title=attempted_titles[0],
        query_year=query_year,
        expected_work_id=initial_expected_work_id,
        meta=first_meta,
    ):
        note = (
            f"{note} OpenAlex title search returned a non-matching or non-economics work "
            f"for {attempted_titles[0]!r}."
        )
    else:
        note = f"{note} Could not find OpenAlex record for title {attempted_titles[0]!r}."
    return None, attempted_titles[-1], note


def _ensure_table(conn) -> None:
    """Ensure table.

    Args:
        conn (Any): Description.
    """
    ensure_schema_ready(conn)


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
    conn = connect(resolved_db_url, require_migrated=True)
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

            source_title = str(paper.title or "").strip() or paper.path.stem
            query_authors = str(paper.author or "").strip() or "Unknown"
            query_year = _year_from_path(paper.path)
            try:
                first_page_text: Optional[str] = None
                pages = getattr(paper, "pages", None)
                if isinstance(pages, list) and pages:
                    first_page_text = str(pages[0] or "")
                meta, effective_query_title, note = _resolve_openalex_metadata_for_paper(
                    paper_path=paper.path,
                    query_title=source_title,
                    query_authors=query_authors,
                    query_year=query_year,
                    first_page_text=first_page_text,
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
                    title=source_title,
                    authors=query_authors,
                    query_title=effective_query_title,
                    query_authors=query_authors,
                    query_year=query_year,
                    openalex_meta=meta,
                    status=status,
                    error_text=note,
                )
            except Exception as exc:  # noqa: BLE001
                stats["error"] += 1
                _upsert_row(
                    conn,
                    paper_path=path_text,
                    title=source_title,
                    authors=query_authors,
                    query_title=source_title,
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
