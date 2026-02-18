"""Evaluation helpers for retrieval and answer-quality metrics. Used to assess pipeline output against golden examples."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class GoldenExample:
    """Represents a single golden evaluation example.

    Attributes:
        paper_path: Path to the paper file.
        question: Question to evaluate.
        expected_pages: Expected relevant page numbers.
        expected_chunk_ids: Optional expected chunk identifiers.
        expected_citations: Optional expected citation identifiers.
    """

    paper_path: str
    question: str
    expected_pages: List[int]
    expected_chunk_ids: List[str] | None = None
    expected_citations: List[str] | None = None


def load_golden_set(path: Path) -> List[GoldenExample]:
    """Load a golden set from a JSON list file.

    Args:
        path (Path): Filesystem path value.

    Returns:
        List[GoldenExample]: List result produced by the operation.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: List[GoldenExample] = []
    for row in data:
        out.append(
            GoldenExample(
                paper_path=str(row.get("paper_path", "")),
                question=str(row.get("question", "")),
                expected_pages=[int(x) for x in row.get("expected_pages", [])],
                expected_chunk_ids=row.get("expected_chunk_ids"),
                expected_citations=row.get("expected_citations"),
            )
        )
    return out


def recall_at_k(retrieved_ids: List[str], expected_ids: List[str], k: int) -> float:
    """Compute recall@k.

    Args:
        retrieved_ids (List[str]): Collection of retrieved ids.
        expected_ids (List[str]): Collection of expected ids.
        k (int): Input value for k.

    Returns:
        float: Computed numeric result.
    """
    if not expected_ids or k <= 0:
        return 0.0
    retrieved_k = set(retrieved_ids[:k])
    hits = sum(1 for eid in expected_ids if eid in retrieved_k)
    return hits / len(expected_ids)


def mrr_at_k(retrieved_ids: List[str], expected_ids: List[str], k: int) -> float:
    """Compute mean reciprocal rank at k.

    Args:
        retrieved_ids (List[str]): Collection of retrieved ids.
        expected_ids (List[str]): Collection of expected ids.
        k (int): Input value for k.

    Returns:
        float: Computed numeric result.
    """
    if not expected_ids or k <= 0:
        return 0.0
    for rank, rid in enumerate(retrieved_ids[:k], start=1):
        if rid in expected_ids:
            return 1.0 / rank
    return 0.0


def evaluate_retrieval(
    retrieved_meta: List[Dict],
    *,
    expected_pages: List[int],
    expected_chunk_ids: Optional[List[str]] = None,
    k: int = 6,
) -> Dict[str, float]:
    """Compute retrieval metrics from retrieved chunk metadata.

    Args:
        retrieved_meta (List[Dict]): Mapping containing retrieved meta.
        expected_pages (List[int]): Collection of expected pages.
        expected_chunk_ids (Optional[List[str]]): Collection of expected chunk ids.
        k (int): Input value for k.

    Returns:
        Dict[str, float]: Dictionary containing the computed result payload.
    """
    retrieved_page_ids = [str(m.get("page")) for m in retrieved_meta if m.get("page") is not None]
    expected_page_ids = [str(p) for p in expected_pages]

    metrics = {
        "recall_at_k": recall_at_k(retrieved_page_ids, expected_page_ids, k),
        "mrr_at_k": mrr_at_k(retrieved_page_ids, expected_page_ids, k),
    }
    if expected_chunk_ids:
        retrieved_chunk_ids = [str(m.get("chunk_id")) for m in retrieved_meta if m.get("chunk_id") is not None]
        metrics["recall_at_k_chunks"] = recall_at_k(retrieved_chunk_ids, expected_chunk_ids, k)
        metrics["mrr_at_k_chunks"] = mrr_at_k(retrieved_chunk_ids, expected_chunk_ids, k)
    return metrics


_CITATION_PATTERNS = [
    re.compile(r"\(page\s+\d+", re.IGNORECASE),
    re.compile(r"\bpage\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bwords\s+\d+\s*-\s*\d+\b", re.IGNORECASE),
]


def answer_has_citation(answer: str) -> bool:
    """Return True if the answer appears to contain a provenance citation.

    Args:
        answer (str): Input value for answer.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    return any(p.search(answer or "") for p in _CITATION_PATTERNS)


def normalize_answer(text: str) -> str:
    """Normalize an answer for comparison.

    Args:
        text (str): Input text value.

    Returns:
        str: Computed string result.
    """
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def self_consistency_rate(answers: Iterable[str]) -> float:
    """Compute a simple self-consistency rate.

    Args:
        answers (Iterable[str]): Input value for answers.

    Returns:
        float: Computed numeric result.
    """
    answers_list = [normalize_answer(a) for a in answers if a]
    if not answers_list:
        return 0.0
    counts: Dict[str, int] = {}
    for ans in answers_list:
        counts[ans] = counts.get(ans, 0) + 1
    return max(counts.values()) / len(answers_list)


def evaluate_answers(answers: Iterable[str]) -> Dict[str, float]:
    """Compute answer-quality proxy metrics.

    Args:
        answers (Iterable[str]): Input value for answers.

    Returns:
        Dict[str, float]: Dictionary containing the computed result payload.
    """
    answers_list = [a for a in answers if a is not None]
    if not answers_list:
        return {"citation_coverage": 0.0, "hallucination_rate_proxy": 0.0, "self_consistency": 0.0}

    cited = sum(1 for a in answers_list if answer_has_citation(a))
    coverage = cited / len(answers_list)
    return {
        "citation_coverage": coverage,
        "hallucination_rate_proxy": 1.0 - coverage,
        "self_consistency": self_consistency_rate(answers_list),
    }
