"""Core pipeline primitives for settings, ingestion, embeddings, retrieval, and summarization. Shared by CLI, indexing, and the Streamlit UI to build end-to-end RAG runs."""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from typing import Optional
from tqdm import tqdm

from ragonometrics.core.config import (
    DEFAULT_CONFIG_PATH,
    apply_config_env_overrides,
    build_effective_config,
    hash_config_dict,
    load_config,
)
from ragonometrics.core.io_loaders import (
    chunk_pages,
    normalize_text,
    run_pdftotext_pages,
)
from ragonometrics.core.prompts import MAIN_SUMMARY_PROMPT, QUERY_EXPANSION_PROMPT, RERANK_PROMPT
from ragonometrics.integrations.openalex import (
    fetch_openalex_metadata,
    format_openalex_context,
)
from ragonometrics.integrations.citec import fetch_citec_plain, format_citec_context
from ragonometrics.db.connection import connect
from ragonometrics.llm.runtime import build_llm_runtime
from ragonometrics.pipeline import call_llm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for ingestion, chunking, and retrieval.

    Attributes:
        papers_dir: Directory containing source PDF files.
        max_papers: Maximum number of PDFs to process.
        max_words: Maximum words from a paper to consider for summarization.
        chunk_words: Target number of words per chunk.
        chunk_overlap: Number of overlapping words between chunks.
        top_k: Number of top chunks to use for context.
        batch_size: Embedding batch size.
        embedding_model: Embedding model name.
        chat_model: Chat completion model name.
        config_effective: Effective config dict used for hashing and manifests.
    """

    papers_dir: Path
    max_papers: int
    max_words: int
    chunk_words: int
    chunk_overlap: int
    top_k: int
    batch_size: int
    embedding_model: str
    chat_model: str
    llm_provider: str = "openai"
    llm_base_url: str = ""
    llm_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_compatible_api_key: str = ""
    chat_provider: str = ""
    embedding_provider: str = ""
    rerank_provider: str = ""
    query_expand_provider: str = ""
    metadata_title_provider: str = ""
    chat_provider_fallback: str = ""
    embedding_provider_fallback: str = ""
    rerank_provider_fallback: str = ""
    query_expand_provider_fallback: str = ""
    metadata_title_provider_fallback: str = ""
    reranker_model: str = ""
    query_expand_model: str = ""
    metadata_title_model: str = ""
    query_expansion: str = ""
    config_path: Path | None = None
    config_hash: str | None = None
    config_effective: Dict[str, Any] | None = None


@dataclass(frozen=True)
class Paper:
    """Represents a paper's metadata and extracted text.

    Attributes:
        path: Filesystem path to the PDF.
        title: Paper title.
        author: Paper author(s).
        text: Full extracted text, normalized.
        pages: Optional per-page text list.
        openalex: Optional OpenAlex metadata dict.
        citec: Optional CitEc citation metadata dict.
    """

    path: Path
    title: str
    author: str
    text: str
    pages: List[str] | None = None
    openalex: Dict[str, Any] | None = None
    citec: Dict[str, Any] | None = None


def load_env(path: Path) -> None:
    """Load environment variables from a .env-style file.

    Args:
        path (Path): Filesystem path value.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(config_path: Path | None = None) -> Settings:
    """Load runtime settings from config file, environment variables, and defaults.

    Args:
        config_path (Path | None): Path to the configuration file.

    Returns:
        Settings: List result produced by the operation.
    """
    load_env(DOTENV_PATH)
    cfg_path = config_path or Path(os.getenv("RAG_CONFIG", DEFAULT_CONFIG_PATH))
    cfg = load_config(cfg_path)
    apply_config_env_overrides(cfg, os.environ)
    effective = build_effective_config(cfg, os.environ, project_root=PROJECT_ROOT)
    return Settings(
        papers_dir=Path(effective["papers_dir"]),
        max_papers=int(effective["max_papers"]),
        max_words=int(effective["max_words"]),
        chunk_words=int(effective["chunk_words"]),
        chunk_overlap=int(effective["chunk_overlap"]),
        top_k=int(effective["top_k"]),
        batch_size=int(effective["batch_size"]),
        embedding_model=str(effective["embedding_model"]),
        chat_model=str(effective["chat_model"]),
        llm_provider=str(effective.get("llm_provider") or "openai"),
        llm_base_url=str(effective.get("llm_base_url") or ""),
        llm_api_key=str(effective.get("llm_api_key") or ""),
        openai_api_key=str(effective.get("openai_api_key") or ""),
        anthropic_api_key=str(effective.get("anthropic_api_key") or ""),
        openai_compatible_api_key=str(effective.get("openai_compatible_api_key") or ""),
        chat_provider=str(effective.get("chat_provider") or ""),
        embedding_provider=str(effective.get("embedding_provider") or ""),
        rerank_provider=str(effective.get("rerank_provider") or ""),
        query_expand_provider=str(effective.get("query_expand_provider") or ""),
        metadata_title_provider=str(effective.get("metadata_title_provider") or ""),
        chat_provider_fallback=str(effective.get("chat_provider_fallback") or ""),
        embedding_provider_fallback=str(effective.get("embedding_provider_fallback") or ""),
        rerank_provider_fallback=str(effective.get("rerank_provider_fallback") or ""),
        query_expand_provider_fallback=str(effective.get("query_expand_provider_fallback") or ""),
        metadata_title_provider_fallback=str(effective.get("metadata_title_provider_fallback") or ""),
        reranker_model=str(effective.get("reranker_model") or ""),
        query_expand_model=str(effective.get("query_expand_model") or ""),
        metadata_title_model=str(effective.get("metadata_title_model") or ""),
        query_expansion=str(effective.get("query_expansion") or ""),
        config_path=cfg_path if cfg_path.exists() else None,
        config_hash=hash_config_dict(effective),
        config_effective=effective,
    )


def run_pdfinfo(path: Path) -> Dict[str, str]:
    """Extract basic PDF metadata using `pdfinfo`.

    Args:
        path (Path): Filesystem path value.

    Returns:
        Dict[str, str]: Dictionary containing the computed result payload.
    """
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {"title": path.stem, "author": "Unknown"}

    title = ""
    author = ""
    for raw_line in result.stdout.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "title":
            title = value
        elif key == "author":
            author = value

    if not title:
        title = path.stem
    if not author or author.lower() in {"unknown", "none"}:
        author = "Unknown"
    return {"title": title, "author": author}


_UNKNOWN_AUTHOR_VALUES = {"", "unknown", "none", "n/a", "na"}
_AUTHOR_NOISE_TOKENS = {
    "journal",
    "association",
    "university",
    "department",
    "school",
    "institute",
    "working",
    "paper",
    "abstract",
    "stable",
    "url",
    "copyright",
    "http",
    "https",
    "doi",
    "volume",
    "issue",
    "pp",
    "terms",
    "conditions",
    "nber",
}
_AUTHOR_NAME_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z'`-]+|[A-Z]\.)"
    r"(?:\s+(?:[A-Z][A-Za-z'`-]+|[A-Z]\.)){1,5}\b"
)
_AUTHOR_FOOTNOTE_PATTERN = re.compile(r"[\*\u2020\u2021\u00a7\u00b6]+")
_AUTHOR_JOIN_WORDS = {"and", "et", "al"}
_AUTHOR_SCAN_STOP_MARKERS = ("abstract", "introduction", "keywords", "jel")
_PAPER_METADATA_OVERRIDES: List[Tuple[str, Dict[str, str]]] = [
    (
        "bundle size pricing as an approximation to mixed bundling",
        {
            "author": "Chenghuan Sean Chu, Phillip Leslie, and Alan Sorensen",
        },
    ),
    (
        "incomplex alternatives to mixed bundling",
        {
            "author": "Chenghuan Sean Chu, Phillip Leslie, Alan Sorensen",
        },
    ),
    (
        "calorie posting in chain restaurants",
        {
            "author": "Bryan Bollinger, Phillip Leslie, and Alan Sorensen",
        },
    ),
    (
        "information entrepreneurs and competition in procurement auctions",
        {
            "author": "Phillip Leslie and Pablo Zoido",
        },
    ),
    (
        "managerial incentives and strategic change evidence from private equity",
        {
            "author": "Phillip Leslie and Paul Oyer",
        },
    ),
    (
        "nearly optimal pricing for multiproduct firms",
        {
            "author": "Chenghuan Sean Chu, Phillip Leslie, and Alan Sorensen",
        },
    ),
    (
        "the welfare effects of ticket resale",
        {
            "author": "Phillip Leslie and Alan Sorensen",
        },
    ),
    (
        "ticket resale",
        {
            "author": "Phillip Leslie and Alan Sorensen",
        },
    ),
]


def _normalize_spaces(text: str) -> str:
    """Normalize spaces.

    Args:
        text (str): Input text value.

    Returns:
        str: Computed string result.
    """
    return re.sub(r"\s+", " ", (text or "")).strip()


def _normalize_title_key(text: str) -> str:
    """Normalize title key.

    Args:
        text (str): Input text value.

    Returns:
        str: Computed string result.
    """
    value = _normalize_spaces(text).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return _normalize_spaces(value)


def _is_unknown_author(value: str | None) -> bool:
    """Is unknown author.

    Args:
        value (str | None): Value to serialize, store, or compare.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    if value is None:
        return True
    return _normalize_spaces(value).lower() in _UNKNOWN_AUTHOR_VALUES


def _clean_author_blob(blob: str) -> str:
    """Clean author blob.

    Args:
        blob (str): Input value for blob.

    Returns:
        str: Computed string result.
    """
    text = _normalize_spaces(blob)
    text = _AUTHOR_FOOTNOTE_PATTERN.sub(" ", text)
    text = re.sub(r"\bby\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    return text


def _is_probable_person_name(name: str) -> bool:
    """Is probable person name.

    Args:
        name (str): Input value for name.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    text = _normalize_spaces(name)
    if not text:
        return False
    low = text.lower()
    if any(token in low for token in _AUTHOR_NOISE_TOKENS):
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 6:
        return False
    for word in words:
        cleaned = word.strip(".")
        if not cleaned:
            return False
        if cleaned.lower() in _AUTHOR_JOIN_WORDS:
            return False
        if cleaned.isupper() and len(cleaned) > 1:
            return False
        if len(cleaned) == 1 and word.endswith(".") and cleaned.isalpha():
            continue
        if not cleaned[0].isupper():
            return False
    return True


def extract_author_names(text: str) -> List[str]:
    """Extract likely person names from a short author text blob.

    Args:
        text (str): Input text value.

    Returns:
        List[str]: List result produced by the operation.
    """
    blob = _clean_author_blob(text)
    if not blob:
        return []
    names: List[str] = []
    seen = set()
    for match in _AUTHOR_NAME_PATTERN.finditer(blob):
        candidate = _normalize_spaces(match.group(0)).strip(" ,;")
        if not _is_probable_person_name(candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(candidate)
    return names


def _looks_like_author_line(line: str) -> bool:
    """Looks like author line.

    Args:
        line (str): Input value for line.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    text = _normalize_spaces(line)
    if not text:
        return False
    low = text.lower()
    if ":" in text and not low.startswith("by "):
        return False
    if any(token in low for token in ("http", "doi", "abstract", "keywords", "jel", "copyright", "@")):
        return False
    if sum(1 for ch in text if ch.isdigit()) > 6:
        return False
    if len(text.split()) > 24:
        return False
    if low.startswith("by "):
        return True
    if ";" in text or "," in text or " and " in low or " & " in text:
        return True
    return False


def _name_token_ratio(text: str) -> float:
    """Name token ratio.

    Args:
        text (str): Input text value.

    Returns:
        float: Computed numeric result.
    """
    tokens = re.findall(r"[A-Za-z][A-Za-z.'`-]*", text or "")
    if not tokens:
        return 0.0
    good = 0
    for token in tokens:
        cleaned = token.strip(".")
        low = cleaned.lower()
        if low in _AUTHOR_JOIN_WORDS:
            good += 1
            continue
        if token[0].isupper() and (not cleaned.isupper() or len(cleaned) == 1):
            good += 1
    return good / len(tokens)


def _looks_like_author_continuation(line: str) -> bool:
    """Looks like author continuation.

    Args:
        line (str): Input value for line.

    Returns:
        bool: True when the operation succeeds; otherwise False.
    """
    text = _normalize_spaces(line)
    if not _looks_like_author_line(text):
        return False
    if sum(1 for ch in text if ch.isdigit()) > 0:
        return False
    return _name_token_ratio(text) >= 0.75


def _author_candidate_score(blob: str, names_count: int) -> Tuple[int, int]:
    """Author candidate score.

    Args:
        blob (str): Input value for blob.
        names_count (int): Input value for names count.

    Returns:
        Tuple[int, int]: Tuple of result values produced by the operation.
    """
    low = blob.lower()
    signal = 0
    if low.startswith("by "):
        signal += 3
    if ";" in blob or "," in blob:
        signal += 2
    if " and " in low or " & " in blob:
        signal += 1
    return names_count, signal


def infer_author_names_from_pages(page_texts: List[str]) -> List[str]:
    """Infer author names from early page text when PDF metadata is weak.

    Args:
        page_texts (List[str]): Collection of page texts.

    Returns:
        List[str]: List result produced by the operation.
    """
    if not page_texts:
        return []
    first_page = page_texts[0] or ""
    lines = [_normalize_spaces(raw) for raw in first_page.splitlines() if _normalize_spaces(raw)]
    if not lines:
        return []

    candidates: List[str] = []
    scan_lines = lines[:70]
    for idx, line in enumerate(scan_lines):
        low = line.lower()
        if any(low.startswith(marker) for marker in _AUTHOR_SCAN_STOP_MARKERS):
            scan_lines = scan_lines[:idx]
            break
    for idx, line in enumerate(scan_lines):
        if not _looks_like_author_line(line):
            continue
        blob = line
        if line.lower().startswith("by "):
            joined = [line]
            for j in range(idx + 1, min(len(scan_lines), idx + 4)):
                nxt = scan_lines[j]
                if not _looks_like_author_continuation(nxt):
                    break
                joined.append(nxt)
            blob = " ".join(joined)
        elif line.endswith(",") and idx + 1 < len(scan_lines) and _looks_like_author_line(scan_lines[idx + 1]):
            blob = f"{line} {scan_lines[idx + 1]}"
        candidates.append(blob)

    best: List[str] = []
    best_score = (-1, -1)
    for blob in candidates:
        names = extract_author_names(blob)
        score = _author_candidate_score(blob, len(names))
        if score > best_score:
            best = names
            best_score = score

    if best:
        return best

    for line in scan_lines[:20]:
        names = extract_author_names(line)
        if len(names) >= 2 and len(names) > len(best):
            best = names
    return best


def _openalex_author_names(openalex_meta: Dict[str, Any] | None) -> List[str]:
    """Openalex author names.

    Args:
        openalex_meta (Dict[str, Any] | None): OpenAlex metadata payload for the paper.

    Returns:
        List[str]: List result produced by the operation.
    """
    names: List[str] = []
    seen = set()
    if not openalex_meta:
        return names
    for author_entry in openalex_meta.get("authorships") or []:
        if not isinstance(author_entry, dict):
            continue
        author_obj = author_entry.get("author") or {}
        name = _normalize_spaces(str(author_obj.get("display_name") or ""))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _select_best_author_names(candidates: List[Tuple[str, List[str]]]) -> List[str]:
    """Select best author names.

    Args:
        candidates (List[Tuple[str, List[str]]]): Collection of candidates.

    Returns:
        List[str]: List result produced by the operation.
    """
    priority = {"openalex": 3, "page_text": 2, "pdfinfo": 1}
    best_names: List[str] = []
    best_score = (-1, -1)
    for source, names in candidates:
        if not names:
            continue
        score = (len(names), priority.get(source, 0))
        if score > best_score:
            best_score = score
            best_names = names
    return best_names


def _format_author_names(names: List[str], *, max_names: int = 8) -> str:
    """Format author names.

    Args:
        names (List[str]): Collection of names.
        max_names (int): Input value for max names.

    Returns:
        str: Computed string result.
    """
    if not names:
        return "Unknown"
    if len(names) > max_names:
        return ", ".join(names[:max_names]) + " et al."
    return ", ".join(names)


def _apply_paper_metadata_overrides(path: Path, title: str, author: str) -> Tuple[str, str]:
    """Apply deterministic metadata corrections for known problematic files.

    Args:
        path (Path): Filesystem path value.
        title (str): Paper title text.
        author (str): Author name text.

    Returns:
        Tuple[str, str]: Tuple of result values produced by the operation.
    """
    title_key = _normalize_title_key(title)
    stem_key = _normalize_title_key(path.stem)
    for title_fragment, patch in _PAPER_METADATA_OVERRIDES:
        needle = _normalize_title_key(title_fragment)
        if not needle:
            continue
        if needle in title_key or needle in stem_key:
            patched_title = str(patch.get("title") or title).strip() or title
            patched_author = str(patch.get("author") or author).strip() or author
            return patched_title, patched_author
    return title, author




def extract_dois_from_text(text: str) -> List[str]:
    """Extract and normalize DOIs from text.

    Args:
        text (str): Input text value.

    Returns:
        List[str]: List result produced by the operation.
    """
    if not text:
        return []
    # DOI regex (per Crossref guidance, simplified)
    doi_regex = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
    url_regex = re.compile(r"https?://(?:dx\.)?doi\.org/([^\s)]+)", re.IGNORECASE)

    dois = set()
    for m in doi_regex.finditer(text):
        doi = m.group(0).rstrip(". ,;)")
        dois.add(doi.lower())

    for m in url_regex.finditer(text):
        doi = m.group(1).rstrip(". ,;)")
        # handle URL-encoded slashes
        try:
            from urllib.parse import unquote

            doi = unquote(doi)
        except Exception:
            pass
        dois.add(doi.lower())

    return sorted(dois)


def extract_repec_handles_from_text(text: str) -> List[str]:
    """Extract RePEc handles from text.

    Args:
        text (str): Input text value.

    Returns:
        List[str]: List result produced by the operation.
    """
    if not text:
        return []
    pattern = re.compile(r"\bRePEc:[A-Za-z0-9]+:[A-Za-z0-9]+:[A-Za-z0-9./_-]+", re.IGNORECASE)
    handles: List[str] = []
    for match in pattern.finditer(text):
        handle = match.group(0).rstrip(").,;]")
        if handle.lower().startswith("repec:"):
            handle = "RePEc:" + handle[6:]
        if handle not in handles:
            handles.append(handle)
    return handles


def embed_texts(
    client: Any,
    texts: List[str],
    model: str,
    batch_size: int,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    step: str | None = None,
    question_id: str | None = None,
    meta: Dict[str, Any] | None = None,
) -> List[List[float]]:
    """Embed a list of texts in batches.

    Args:
        client (OpenAI): Provider client instance.
        texts (List[str]): Collection of texts.
        model (str): Model name used for this operation.
        batch_size (int): Input value for batch size.
        session_id (str | None): Session identifier.
        request_id (str | None): Request identifier.
        run_id (str | None): Unique workflow run identifier.
        step (str | None): Pipeline step name.
        question_id (str | None): Structured question identifier.
        meta (Dict[str, Any] | None): Additional metadata dictionary.

    Returns:
        List[List[float]]: List result produced by the operation.
    """
    def _usage_counts(usage_obj: Any) -> tuple[int, int, int]:
        """Internal helper for usage counts."""
        if usage_obj is None:
            return 0, 0, 0
        if isinstance(usage_obj, dict):
            input_tokens = int(usage_obj.get("input_tokens") or 0)
            output_tokens = int(usage_obj.get("output_tokens") or 0)
            total_tokens = int(usage_obj.get("total_tokens") or 0)
        else:
            input_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage_obj, "output_tokens", 0) or 0)
            total_tokens = int(getattr(usage_obj, "total_tokens", 0) or 0)
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        return input_tokens, output_tokens, total_tokens

    def _embed_batch(provider_obj: Any, batch_items: List[str]) -> tuple[List[List[float]], int, int, int, str | None]:
        # New provider runtime wrapper.
        """Internal helper for embed batch."""
        if hasattr(provider_obj, "embed"):
            result = provider_obj.embed(texts=batch_items, model=model, metadata=meta or {})
            return (
                [list(v) for v in result.embeddings],
                int(getattr(result, "input_tokens", 0) or 0),
                int(getattr(result, "output_tokens", 0) or 0),
                int(getattr(result, "total_tokens", 0) or 0),
                str(getattr(result, "provider", "") or "") or None,
            )
        # Runtime container exposing .embeddings capability.
        if hasattr(provider_obj, "embeddings") and hasattr(provider_obj.embeddings, "embed"):
            result = provider_obj.embeddings.embed(texts=batch_items, model=model, metadata=meta or {})
            return (
                [list(v) for v in result.embeddings],
                int(getattr(result, "input_tokens", 0) or 0),
                int(getattr(result, "output_tokens", 0) or 0),
                int(getattr(result, "total_tokens", 0) or 0),
                str(getattr(result, "provider", "") or "") or None,
            )
        # Legacy OpenAI-like client shape.
        if hasattr(provider_obj, "embeddings") and hasattr(provider_obj.embeddings, "create"):
            resp = provider_obj.embeddings.create(model=model, input=batch_items)
            emb_list = [list(item.embedding) for item in (getattr(resp, "data", None) or [])]
            in_tok, out_tok, total_tok = _usage_counts(getattr(resp, "usage", None))
            return emb_list, in_tok, out_tok, total_tok, None
        raise RuntimeError("Embedding provider does not support embeddings")

    embeddings: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        emb_batch, input_tokens, output_tokens, total_tokens, provider_name = _embed_batch(client, batch)
        try:
            from ragonometrics.pipeline.token_usage import record_usage

            usage_meta = dict(meta or {})
            if provider_name:
                usage_meta.setdefault("provider", provider_name)
                usage_meta.setdefault("capability", "embeddings")
            record_usage(
                model=model,
                operation="embeddings",
                step=step,
                question_id=question_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                session_id=session_id,
                request_id=request_id,
                run_id=run_id,
                meta=usage_meta,
            )
        except Exception:
            pass
        embeddings.extend(emb_batch)
    return embeddings


def cosine(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a (List[float]): Collection of a.
        b (List[float]): Collection of b.

    Returns:
        float: Computed numeric result.
    """
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def expand_queries(
    query: str,
    client: Any,
    settings: Settings,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    step: str | None = None,
    question_id: str | None = None,
) -> List[str]:
    """Optionally expand a query using a lightweight LLM prompt.

    Args:
        query (str): Input query text.
        client (OpenAI): Provider client instance.
        settings (Settings): Loaded application settings.
        session_id (str | None): Session identifier.
        request_id (str | None): Request identifier.
        run_id (str | None): Unique workflow run identifier.
        step (str | None): Pipeline step name.
        question_id (str | None): Structured question identifier.

    Returns:
        List[str]: List result produced by the operation.
    """
    mode = os.environ.get("QUERY_EXPANSION", "").strip().lower()
    if not mode:
        return [query]
    model = os.environ.get("QUERY_EXPAND_MODEL") or settings.query_expand_model or settings.chat_model
    try:
        raw = call_llm(
            client,
            model=model,
            instructions=QUERY_EXPANSION_PROMPT,
            user_input=query,
            max_output_tokens=200,
            usage_context="query_expansion",
            session_id=session_id,
            request_id=request_id,
            run_id=run_id,
            step=step,
            question_id=question_id,
        )
    except Exception:
        return [query]
    candidates: List[str] = [query]
    for line in raw.splitlines():
        line = line.strip().lstrip("-*â€¢").strip()
        if not line:
            continue
        if line not in candidates:
            candidates.append(line)
        if len(candidates) >= 4:
            break
    return candidates


def rerank_with_llm(
    *,
    query: str,
    items: List[Dict[str, str]],
    client: Any,
    settings: Settings,
    session_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    step: str | None = None,
    question_id: str | None = None,
) -> List[str] | None:
    """Use an LLM to rerank items by relevance.

    Args:
        query (str): Input query text.
        items (List[Dict[str, str]]): Mapping containing items.
        client (OpenAI): Provider client instance.
        settings (Settings): Loaded application settings.
        session_id (str | None): Session identifier.
        request_id (str | None): Request identifier.
        run_id (str | None): Unique workflow run identifier.
        step (str | None): Pipeline step name.
        question_id (str | None): Structured question identifier.

    Returns:
        List[str] | None: Computed result, or `None` when unavailable.
    """
    model = os.environ.get("RERANKER_MODEL") or settings.reranker_model
    if not model:
        return None
    payload_lines = [f"{it['id']}: {it['text']}" for it in items]
    payload = "\n\n".join(payload_lines)
    try:
        raw = call_llm(
            client,
            model=model,
            instructions=RERANK_PROMPT,
            user_input=f"Query:\n{query}\n\nChunks:\n{payload}",
            max_output_tokens=300,
            usage_context="rerank",
            session_id=session_id,
            request_id=request_id,
            run_id=run_id,
            step=step,
            question_id=question_id,
        )
    except Exception:
        return None
    # parse JSON-ish list of ids
    ids: List[str] = []
    for tok in re.findall(r"[A-Za-z0-9_-]+", raw):
        if tok in {it["id"] for it in items} and tok not in ids:
            ids.append(tok)
    return ids or None


def top_k_context(
    chunks: List[str],
    chunk_embeddings: List[List[float]],
    query: str,
    client: Any,
    settings: Settings,
    *,
    paper_path: str | Path | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    step: str | None = None,
    question_id: str | None = None,
    return_stats: bool = False,
) -> str | tuple[str, Dict[str, float | str | int]]:
    """Select top-k relevant chunk text for a query.

    Args:
        chunks (List[str]): Collection of chunks.
        chunk_embeddings (List[List[float]]): Collection of chunk embeddings.
        query (str): Input query text.
        client (OpenAI): Provider client instance.
        settings (Settings): Loaded application settings.
        paper_path (str | Path | None): Optional path to scope retrieval to one paper.
        session_id (str | None): Session identifier.
        request_id (str | None): Request identifier.
        run_id (str | None): Unique workflow run identifier.
        step (str | None): Pipeline step name.
        question_id (str | None): Structured question identifier.
        return_stats (bool): Whether to enable return stats.

    Returns:
        str | tuple[str, Dict[str, float | str | int]]: Dictionary containing the computed result payload.
    """
    queries = expand_queries(
        query,
        client,
        settings,
        session_id=session_id,
        request_id=request_id,
        run_id=run_id,
        step=step,
        question_id=question_id,
    )

    # If a Postgres-backed retriever is available, prefer hybrid retrieval
    db_url = os.environ.get("DATABASE_URL")
    stats: Dict[str, float | str | int] = {"method": "local"}
    normalized_paper_path = str(paper_path).replace("\\", "/") if paper_path else None
    if db_url:
        try:
            from ragonometrics.indexing.retriever import hybrid_search
        except Exception:
            hybrid_search = None

        if hybrid_search:
            try:
                # allow runtime tuning of BM25 weight via env var
                try:
                    bm25_weight = float(os.environ.get("BM25_WEIGHT", "0.5"))
                except Exception:
                    bm25_weight = 0.5

                combined: Dict[int, float] = {}
                for q in queries:
                    hits = hybrid_search(
                        q,
                        embedding_client=client,
                        embedding_model=settings.embedding_model,
                        db_url=db_url,
                        top_k=settings.top_k * 5,
                        bm25_weight=bm25_weight,
                        paper_path=normalized_paper_path,
                    )
                    for vid, score in hits:
                        combined[vid] = max(combined.get(vid, float("-inf")), float(score))
                hits = sorted(combined.items(), key=lambda x: x[1], reverse=True)[: settings.top_k * 5]
                if hits:
                    top_scores = [float(score) for _, score in hits[: settings.top_k]]
                    stats = {
                        "method": "hybrid",
                        "score_mean": float(sum(top_scores) / len(top_scores)) if top_scores else 0.0,
                        "score_max": float(max(top_scores)) if top_scores else 0.0,
                        "score_min": float(min(top_scores)) if top_scores else 0.0,
                        "top_k": int(len(top_scores)),
                    }
                    if top_scores:
                        score_min = stats["score_min"]
                        score_max = stats["score_max"]
                        if score_max != score_min:
                            normed = [(s - score_min) / (score_max - score_min) for s in top_scores]
                        else:
                            normed = [1.0 for _ in top_scores]
                        stats["score_mean_norm"] = float(sum(normed) / len(normed))

                    # fetch rows by id and return ordered context
                    conn = connect(db_url, require_migrated=True)
                    cur = conn.cursor()
                    ids = [h[0] for h in hits]
                    placeholders = ",".join(["%s"] * len(ids))
                    sql = (
                        f"SELECT id, text, page, start_word, end_word FROM indexing.vectors WHERE id IN ({placeholders})"
                    )
                    params: List[Any] = list(ids)
                    if normalized_paper_path:
                        sql += " AND lower(replace(COALESCE(paper_path, ''), '\\\\', '/')) = lower(%s)"
                        params.append(normalized_paper_path)
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
                    conn.close()
                    id_to_row = {r[0]: r for r in rows}
                    # optional rerank over top-N
                    rerank_top_n = int(os.environ.get("RERANK_TOP_N", "30"))
                    if os.environ.get("RERANKER_MODEL") and rerank_top_n > 0:
                        candidates = []
                        for vid, _ in hits[:rerank_top_n]:
                            r = id_to_row.get(vid)
                            if not r:
                                continue
                            _, text, page, start_word, end_word = r
                            candidates.append({"id": str(vid), "text": text[:800]})
                        order = rerank_with_llm(
                            query=query,
                            items=candidates,
                            client=client,
                            settings=settings,
                            session_id=session_id,
                            request_id=request_id,
                            run_id=run_id,
                            step=step,
                            question_id=question_id,
                        )
                        if order:
                            order_map = {oid: i for i, oid in enumerate(order)}
                            hits = sorted(hits, key=lambda x: order_map.get(str(x[0]), 999999))

                    out_parts: List[str] = []
                    for vid, score in hits[: settings.top_k]:
                        r = id_to_row.get(vid)
                        if not r:
                            continue
                        _, text, page, start_word, end_word = r
                        meta = f"(page {page} words {start_word}-{end_word})"
                        out_parts.append(f"{meta}\n{text}")
                    if out_parts:
                        context = "\n\n".join(out_parts)
                        if return_stats:
                            return context, stats
                        return context
            except Exception:
                # fall back to local embedding retrieval if DB is unreachable
                pass

    # fallback: support chunks as list of dicts with provenance metadata or simple strings
    query_embeddings = embed_texts(
        client,
        queries,
        settings.embedding_model,
        batch_size=max(1, len(queries)),
        session_id=session_id,
        request_id=request_id,
        run_id=run_id,
        step=step,
        question_id=question_id,
        meta={"capability": "query_embedding"},
    )
    scored = [(idx, max(cosine(qemb, emb) for qemb in query_embeddings)) for idx, emb in enumerate(chunk_embeddings)]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [idx for idx, _ in scored[: settings.top_k * 5]]
    top_scores = [float(score) for _, score in scored[: settings.top_k]]
    stats = {
        "method": "local",
        "score_mean": float(sum(top_scores) / len(top_scores)) if top_scores else 0.0,
        "score_max": float(max(top_scores)) if top_scores else 0.0,
        "score_min": float(min(top_scores)) if top_scores else 0.0,
        "top_k": int(len(top_scores)),
    }
    if top_scores:
        score_min = stats["score_min"]
        score_max = stats["score_max"]
        if score_max != score_min:
            normed = [(s - score_min) / (score_max - score_min) for s in top_scores]
        else:
            normed = [1.0 for _ in top_scores]
        stats["score_mean_norm"] = float(sum(normed) / len(normed))

    # optional rerank via LLM
    rerank_top_n = int(os.environ.get("RERANK_TOP_N", "30"))
    if os.environ.get("RERANKER_MODEL") and rerank_top_n > 0:
        candidates = []
        for idx in top[:rerank_top_n]:
            chunk = chunks[idx]
            text = chunk["text"] if isinstance(chunk, dict) else str(chunk)
            candidates.append({"id": str(idx), "text": text[:800]})
        order = rerank_with_llm(
            query=query,
            items=candidates,
            client=client,
            settings=settings,
            session_id=session_id,
            request_id=request_id,
            run_id=run_id,
            step=step,
            question_id=question_id,
        )
        if order:
            order_map = {oid: i for i, oid in enumerate(order)}
            top = sorted(top, key=lambda x: order_map.get(str(x), 999999))

    top_sorted = sorted(top[: settings.top_k])

    out_parts: List[str] = []
    for i in top_sorted:
        chunk = chunks[i]
        if isinstance(chunk, dict):
            section = chunk.get("section")
            section_txt = f" section {section}" if section and section != "unknown" else ""
            meta = f"(page {chunk.get('page')} words {chunk.get('start_word')}-{chunk.get('end_word')}{section_txt})"
            out_parts.append(f"{meta}\n{chunk.get('text')}")
        else:
            out_parts.append(str(chunk))
    context = "\n\n".join(out_parts)
    if return_stats:
        return context, stats
    return context


def prepare_chunks_for_paper(paper: Paper, settings: Settings) -> List[Dict]:
    """Prepare provenance-aware chunks for a paper.

    Args:
        paper (Paper): Input value for paper.
        settings (Settings): Loaded application settings.

    Returns:
        List[Dict]: Dictionary containing the computed result payload.
    """
    if paper.pages:
        page_texts = paper.pages
    else:
        # fall back to whole-text split into a single page
        page_texts = [paper.text]
    return chunk_pages(page_texts, settings.chunk_words, settings.chunk_overlap)


def summarize_paper(client: Any, paper: Paper, settings: Settings) -> str:
    """Summarize a paper using retrieved context and a chat model.

    Args:
        client (OpenAI): Provider client instance.
        paper (Paper): Input value for paper.
        settings (Settings): Loaded application settings.

    Returns:
        str: Computed string result.
    """
    chunks = prepare_chunks_for_paper(paper, settings)
    if not chunks:
        return "No text extracted."

    # embed texts (support chunks being dicts)
    chunk_texts = [c["text"] if isinstance(c, dict) else str(c) for c in chunks]
    chunk_embeddings = embed_texts(client, chunk_texts, settings.embedding_model, settings.batch_size)
    context = top_k_context(
        chunks,
        chunk_embeddings,
        query=MAIN_SUMMARY_PROMPT,
        client=client,
        settings=settings,
        paper_path=paper.path,
    )

    openalex_context = format_openalex_context(paper.openalex)
    citec_context = format_citec_context(paper.citec)
    user_input = (
        f"Title: {paper.title}\n"
        f"Author: {paper.author}\n\n"
        f"Context:\n{context}\n\n"
        "Write the summary now."
    )
    prefix_parts = [ctx for ctx in (openalex_context, citec_context) if ctx]
    if prefix_parts:
        prefix = "\n\n".join(prefix_parts)
        user_input = f"{prefix}\n\n{user_input}"
    return call_llm(
        client,
        model=settings.chat_model,
        instructions=MAIN_SUMMARY_PROMPT,
        user_input=user_input,
        max_output_tokens=None,
    ).strip()


def load_papers(
    paths: Iterable[Path],
    *,
    progress: bool = False,
    progress_desc: str = "Loading papers",
) -> List[Paper]:
    """Load and extract text for a collection of PDF files.

    Args:
        paths (Iterable[Path]): Path to paths.
        progress (bool): Whether to enable progress.
        progress_desc (str): Input value for progress desc.

    Returns:
        List[Paper]: List result produced by the operation.
    """
    path_list = list(paths)
    iterator = path_list
    if progress:
        iterator = tqdm(path_list, desc=progress_desc)
    papers: List[Paper] = []
    for path in iterator:
        metadata = run_pdfinfo(path)
        pdfinfo_author = _normalize_spaces(metadata.get("author") or "")
        page_texts = run_pdftotext_pages(path)
        normalized_pages = [normalize_text(p) for p in page_texts if p is not None]
        text = "\n\n".join(p for p in normalized_pages if p)
        page_text_author_names = infer_author_names_from_pages(page_texts)
        pdfinfo_author_names = extract_author_names(pdfinfo_author)
        openalex_meta: Dict[str, Any] | None = None
        citec_meta: Dict[str, Any] | None = None
        openalex_ok = False
        try:
            dois = extract_dois_from_text(text)
            repec_handles = extract_repec_handles_from_text(text)
            openalex_meta = fetch_openalex_metadata(
                title=metadata.get("title"),
                author=metadata.get("author"),
                doi=dois[0] if dois else None,
            )
            if openalex_meta:
                oa_title = openalex_meta.get("display_name") or openalex_meta.get("title")
                authorships = openalex_meta.get("authorships") or []
                openalex_ok = bool(oa_title or authorships)
            if repec_handles and not openalex_ok:
                citec_meta = fetch_citec_plain(repec_handles[0])
        except Exception:
            openalex_meta = None
            openalex_ok = False
            citec_meta = None

        title = metadata.get("title") or path.stem
        author = pdfinfo_author if not _is_unknown_author(pdfinfo_author) else "Unknown"
        openalex_names: List[str] = []
        if openalex_meta:
            oa_title = openalex_meta.get("display_name") or openalex_meta.get("title")
            if (not title or title == path.stem) and oa_title:
                title = oa_title
            openalex_names = _openalex_author_names(openalex_meta)

        selected_author_names = _select_best_author_names(
            [
                ("openalex", openalex_names),
                ("page_text", page_text_author_names),
                ("pdfinfo", pdfinfo_author_names),
            ]
        )
        if selected_author_names:
            author = _format_author_names(selected_author_names)
        elif _is_unknown_author(author):
            author = "Unknown"
        title, author = _apply_paper_metadata_overrides(path, title, author)

        papers.append(
            Paper(
                path=path,
                title=title,
                author=author,
                text=text,
                pages=normalized_pages or None,
                openalex=openalex_meta,
                citec=citec_meta,
            )
        )
    return papers


def main() -> None:
    """Entry point for summarizing economics papers from the papers directory.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    settings = load_settings()
    client = build_llm_runtime(settings)

    if not settings.papers_dir.exists():
        raise SystemExit(f"Papers directory not found: {settings.papers_dir}")

    pdf_files = sorted(settings.papers_dir.glob("*.pdf"))
    if not pdf_files:
        raise SystemExit("No PDF files found in papers directory.")

    selected = pdf_files[: settings.max_papers]
    papers = load_papers(selected)

    print(f"Using {len(papers)} paper(s) from {settings.papers_dir}")
    print("Summarizing papers...\n")

    for paper in papers:
        print("\n" + "=" * 80)
        print(f"{paper.title}  |  {paper.author}  |  {paper.path.name}")
        summary = summarize_paper(client, paper, settings)
        print(summary)


if __name__ == "__main__":
    main()


