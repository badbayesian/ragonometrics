"""Deterministic provenance scoring for assistant answers."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ragonometrics.services.papers import PaperRef, load_prepared


def _tokens(text: Any) -> Set[str]:
    """Internal helper for tokens."""
    raw = str(text or "").lower()
    vals = re.findall(r"[a-z0-9]+", raw)
    return {item for item in vals if len(item) >= 4}


def _safe_int(value: Any) -> Optional[int]:
    """Internal helper for safe int."""
    try:
        return int(value)
    except Exception:
        return None


def _chunk_meta(chunk: Any) -> Tuple[Optional[int], Optional[int], Optional[int], str]:
    """Internal helper for chunk meta."""
    if not isinstance(chunk, dict):
        return None, None, None, str(chunk or "")
    page = _safe_int(chunk.get("page"))
    start_word = _safe_int(chunk.get("start_word"))
    end_word = _safe_int(chunk.get("end_word"))
    text = str(chunk.get("text") or "")
    return page, start_word, end_word, text


def _ranges_overlap(start_a: Optional[int], end_a: Optional[int], start_b: Optional[int], end_b: Optional[int]) -> bool:
    """Internal helper for ranges overlap."""
    if start_a is None or end_a is None or start_b is None or end_b is None:
        return False
    if start_a > end_a or start_b > end_b:
        return False
    return not (end_a < start_b or end_b < start_a)


def _warning(code: str, message: str) -> Dict[str, str]:
    """Internal helper for warning."""
    return {"code": str(code or ""), "message": str(message or "")}


def score_answer_provenance(
    *,
    paper_ref: PaperRef,
    question: str,
    answer: str,
    citations: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Score answer provenance quality using deterministic lexical + anchor checks."""
    paper, chunks, _, _ = load_prepared(paper_ref)
    chunk_rows: List[Tuple[Optional[int], Optional[int], Optional[int], str]] = [_chunk_meta(item) for item in (chunks or [])]
    page_set: Set[int] = {int(pg) for pg, _, _, _ in chunk_rows if isinstance(pg, int) and pg > 0}
    max_page = max(page_set) if page_set else 0

    answer_tokens = _tokens(answer)
    citations_list = [item for item in citations if isinstance(item, dict)]
    citation_count = len(citations_list)
    with_page = 0
    page_missing = 0
    anchor_valid = 0
    anchor_invalid = 0
    text_present = 0
    mapped_chunks = 0
    overlap_hits = 0
    overlap_ratio_sum = 0.0

    for citation in citations_list:
        page = _safe_int(citation.get("page"))
        start_word = _safe_int(citation.get("start_word"))
        end_word = _safe_int(citation.get("end_word"))
        ctext = str(citation.get("text") or "").strip()
        if ctext:
            text_present += 1
        if page is None:
            page_missing += 1
        else:
            with_page += 1
            if page_set and page not in page_set:
                page_missing += 1
        if start_word is not None and end_word is not None and start_word >= 0 and end_word >= 0 and start_word <= end_word:
            anchor_valid += 1
        elif start_word is not None or end_word is not None:
            anchor_invalid += 1

        mapped = False
        if page is not None:
            for chunk_page, chunk_start, chunk_end, chunk_text in chunk_rows:
                if chunk_page != page:
                    continue
                if start_word is not None and end_word is not None:
                    if _ranges_overlap(start_word, end_word, chunk_start, chunk_end):
                        mapped = True
                        break
                elif ctext and ctext[:64] and ctext[:64].lower() in str(chunk_text or "").lower():
                    mapped = True
                    break
        if mapped:
            mapped_chunks += 1

        citation_tokens = _tokens(ctext)
        if answer_tokens and citation_tokens:
            overlap = len(answer_tokens.intersection(citation_tokens))
            ratio = float(overlap) / float(max(1, len(answer_tokens)))
            overlap_ratio_sum += ratio
            if overlap > 0:
                overlap_hits += 1

    if citation_count == 0:
        citation_coverage = 0.0
        anchor_ratio = 0.0
        lexical_overlap_ratio = 0.0
    else:
        citation_coverage = float(text_present) / float(citation_count)
        anchor_ratio = float(anchor_valid) / float(citation_count)
        lexical_overlap_ratio = overlap_ratio_sum / float(citation_count)

    score = (0.45 * citation_coverage) + (0.35 * lexical_overlap_ratio) + (0.20 * anchor_ratio)
    score = max(0.0, min(1.0, round(score, 3)))
    if score >= 0.75:
        status = "high"
    elif score >= 0.45:
        status = "medium"
    else:
        status = "low"

    warnings: List[Dict[str, str]] = []
    if citation_count == 0:
        warnings.append(_warning("no_citations", "Answer includes no citation anchors."))
    if page_missing > 0:
        warnings.append(_warning("citation_page_missing", f"{page_missing} citations have missing or out-of-document page numbers."))
    if anchor_invalid > 0:
        warnings.append(_warning("anchor_out_of_range", f"{anchor_invalid} citations have invalid word ranges."))
    if citation_count > 0 and text_present == 0:
        warnings.append(_warning("citation_text_missing", "Citations do not include text snippets."))
    if citation_count > 0 and mapped_chunks == 0:
        warnings.append(_warning("unmapped_citations", "Citations could not be mapped to the selected paper chunks."))
    if citation_count > 0 and lexical_overlap_ratio < 0.05:
        warnings.append(_warning("low_lexical_overlap", "Answer text has low lexical overlap with cited snippets."))

    return {
        "paper_id": paper_ref.paper_id,
        "paper_path": paper_ref.path,
        "paper_title": str(getattr(paper, "title", "") or ""),
        "question": str(question or ""),
        "score": score,
        "status": status,
        "warnings": warnings,
        "metrics": {
            "citation_count": citation_count,
            "citation_with_text_count": text_present,
            "citation_with_page_count": with_page,
            "page_missing_count": page_missing,
            "anchor_valid_count": anchor_valid,
            "anchor_invalid_count": anchor_invalid,
            "mapped_chunk_count": mapped_chunks,
            "lexical_overlap_hit_count": overlap_hits,
            "citation_coverage_ratio": round(citation_coverage, 3),
            "anchor_valid_ratio": round(anchor_ratio, 3),
            "lexical_overlap_ratio": round(lexical_overlap_ratio, 3),
            "max_page_in_paper": int(max_page or 0),
        },
    }

