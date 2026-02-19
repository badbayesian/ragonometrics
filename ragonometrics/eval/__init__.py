"""Evaluation and benchmarking utilities for retrieval quality and pipeline performance."""

from .eval import (
    GoldenExample,
    load_golden_set,
    recall_at_k,
    mrr_at_k,
    evaluate_retrieval,
    evaluate_answers,
    answer_has_citation,
)

from .benchmark import benchmark_chunking, benchmark_indexing, bench_papers
from .web_cache_benchmark import (
    benchmark_web_cached_structured_questions,
    benchmark_web_chat_turns,
    benchmark_web_tabs,
    write_web_cache_benchmark_report,
)

__all__ = [
    "GoldenExample",
    "load_golden_set",
    "recall_at_k",
    "mrr_at_k",
    "evaluate_retrieval",
    "evaluate_answers",
    "answer_has_citation",
    "benchmark_chunking",
    "benchmark_indexing",
    "bench_papers",
    "benchmark_web_cached_structured_questions",
    "benchmark_web_tabs",
    "benchmark_web_chat_turns",
    "write_web_cache_benchmark_report",
]
