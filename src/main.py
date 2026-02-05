from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

import requests
import psycopg2
from datetime import datetime
from typing import Optional

from openai import OpenAI

from ragonomics.io_loaders import (
    chunk_pages,
    chunk_words,
    normalize_text,
    run_pdftotext,
    run_pdftotext_pages,
    trim_words,
)
from ragonomics.prompts import MAIN_SUMMARY_PROMPT
from ragonomics.pipeline import call_openai


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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


@dataclass(frozen=True)
class Paper:
    """Represents a paper's metadata and extracted text.

    Attributes:
        path: Filesystem path to the PDF.
        title: Paper title.
        author: Paper author(s).
        text: Full extracted text, normalized.
        pages: Optional per-page text list.
    """

    path: Path
    title: str
    author: str
    text: str
    pages: List[str] | None = None


def load_env(path: Path) -> None:
    """Load environment variables from a .env-style file.

    Existing environment variables are not overwritten.

    Args:
        path: Path to the .env file.
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


def load_settings() -> Settings:
    """Load runtime settings from environment variables and defaults.

    Returns:
        Settings: Resolved configuration.
    """
    load_env(DOTENV_PATH)
    papers_dir = Path(os.getenv("PAPERS_DIR", PROJECT_ROOT / "papers"))
    return Settings(
        papers_dir=papers_dir,
        max_papers=int(os.getenv("MAX_PAPERS", "3")),
        max_words=int(os.getenv("MAX_WORDS", "12000")),
        chunk_words=int(os.getenv("CHUNK_WORDS", "350")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "50")),
        top_k=int(os.getenv("TOP_K", "6")),
        batch_size=int(os.getenv("EMBED_BATCH", "64")),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        chat_model=os.getenv("OPENAI_MODEL") or os.getenv("CHAT_MODEL", "gpt-5-nano"),
    )


def run_pdfinfo(path: Path) -> Dict[str, str]:
    """Extract basic PDF metadata using `pdfinfo`.

    Falls back to the filename and "Unknown" if metadata is unavailable.

    Args:
        path: Path to the PDF file.

    Returns:
        Dict[str, str]: Mapping with "title" and "author" keys.
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




def extract_dois_from_text(text: str) -> List[str]:
    """Extract and normalize DOIs from text.

    Args:
        text: Input text to scan.

    Returns:
        List[str]: Unique DOIs (lowercased, without URL prefixes).
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


def fetch_references_from_crossref(doi: str, timeout: int = 10, cache_db_url: str | None = None) -> List[str]:
    """Query Crossref for a DOI and return referenced DOIs.

    If Crossref returns `message.reference`, this extracts "DOI" fields.

    Args:
        doi: Source DOI.
        timeout: Request timeout in seconds.
        cache_db_url: Optional database URL for cached Crossref responses.

    Returns:
        List[str]: Referenced DOIs, lowercased. Empty on failure.
    """
    if not doi:
        return []
    # Crossref expects DOI path-encoded (slashes allowed) — use as-is but URL-quote
    # Optionally use cached Crossref responses with backoff
    if cache_db_url:
        from .crossref_cache import fetch_crossref_with_cache

        raw = fetch_crossref_with_cache(doi, cache_db_url)
        if not raw:
            return []
        data = requests.utils.json.loads(raw)
    else:
        url = f"https://api.crossref.org/works/{requests.utils.requote_uri(doi)}"
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Ragonomics/0.1 (mailto:example@example.com)"})
            resp.raise_for_status()
        except requests.RequestException:
            return []
        data = resp.json()
    message = data.get("message") or {}
    references = message.get("reference") or []
    cited = []
    for ref in references:
        ref_doi = ref.get("DOI") or ref.get("doi")
        if ref_doi:
            cited.append(ref_doi.lower())
    return cited


def build_doi_network_from_paper(paper: Paper, max_fetch: int = 20) -> Dict[str, List[str]]:
    """Build a DOI citation network from a paper's text.

    Args:
        paper: Paper to analyze.
        max_fetch: Maximum number of source DOIs to query in Crossref.

    Returns:
        Dict[str, List[str]]: Mapping of source DOI to cited DOIs.
    """
    network: Dict[str, List[str]] = {}
    text = paper.text
    source_dois = extract_dois_from_text(text)
    if not source_dois:
        return network

    for doi in source_dois[:max_fetch]:
        cited = fetch_references_from_crossref(doi)
        network[doi] = cited

    return network


def init_doi_db(db_url: str):
    """Initialize Postgres tables for DOI networks.

    Args:
        db_url: libpq-style database URL (e.g., from `DATABASE_URL`).

    Returns:
        connection: Open psycopg2 connection.
    """
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id SERIAL PRIMARY KEY,
            path TEXT,
            title TEXT,
            author TEXT,
            extracted_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dois (
            doi TEXT PRIMARY KEY
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS citations (
            source_doi TEXT,
            target_doi TEXT,
            PRIMARY KEY (source_doi, target_doi)
        )
        """
    )
    conn.commit()
    return conn


def store_network_in_db(conn, paper: Paper, network: Dict[str, List[str]]) -> None:
    """Persist a DOI network for a paper into Postgres.

    Args:
        conn: Open psycopg2 connection.
        paper: Paper metadata to store.
        network: Mapping of source DOI to cited DOIs.
    """
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO papers (path, title, author, extracted_at) VALUES (%s, %s, %s, %s)",
        (str(paper.path), paper.title, paper.author, datetime.utcnow().isoformat()),
    )

    # Upsert DOIs and insert citation edges
    for src, targets in network.items():
        cur.execute("INSERT INTO dois (doi) VALUES (%s) ON CONFLICT (doi) DO NOTHING", (src,))
        for tgt in targets:
            cur.execute("INSERT INTO dois (doi) VALUES (%s) ON CONFLICT (doi) DO NOTHING", (tgt,))
            cur.execute(
                "INSERT INTO citations (source_doi, target_doi) VALUES (%s, %s) ON CONFLICT (source_doi, target_doi) DO NOTHING",
                (src, tgt),
            )

    conn.commit()


def build_and_store_doi_network(paper: Paper, db_url: Optional[str] = None, max_fetch: int = 20) -> Dict[str, List[str]]:
    """Build a DOI network and optionally persist it.

    Args:
        paper: Paper to analyze.
        db_url: Optional Postgres database URL for persistence.
        max_fetch: Maximum number of source DOIs to query in Crossref.

    Returns:
        Dict[str, List[str]]: In-memory network mapping.
    """
    network = build_doi_network_from_paper(paper, max_fetch=max_fetch)
    if db_url:
        conn = init_doi_db(db_url)
        try:
            store_network_in_db(conn, paper, network)
        finally:
            conn.close()
    return network


def embed_texts(client: OpenAI, texts: List[str], model: str, batch_size: int) -> List[List[float]]:
    """Embed a list of texts in batches.

    Args:
        client: OpenAI client.
        texts: Texts to embed.
        model: Embedding model name.
        batch_size: Number of texts per batch.

    Returns:
        List[List[float]]: Embedding vectors.
    """
    embeddings: List[List[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=model, input=batch)
        embeddings.extend([item.embedding for item in resp.data])
    return embeddings


def cosine(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        float: Cosine similarity in [0, 1] when inputs are non-negative.
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


def top_k_context(
    chunks: List[str],
    chunk_embeddings: List[List[float]],
    query: str,
    client: OpenAI,
    settings: Settings,
) -> str:
    """Select top-k relevant chunk text for a query.

    Uses hybrid retrieval when a Postgres-backed retriever is configured,
    otherwise falls back to embedding cosine similarity.

    Args:
        chunks: Chunk texts or dicts with provenance metadata.
        chunk_embeddings: Embedding vectors for each chunk.
        query: Query string.
        client: OpenAI client.
        settings: Runtime settings.

    Returns:
        str: Concatenated context string.
    """
    # If a Postgres-backed retriever is available, prefer hybrid retrieval
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            from .retriever import hybrid_search
        except Exception:
            hybrid_search = None

        if hybrid_search:
            # allow runtime tuning of BM25 weight via env var
            try:
                bm25_weight = float(os.environ.get("BM25_WEIGHT", "0.5"))
            except Exception:
                bm25_weight = 0.5

            hits = hybrid_search(query, client=client, db_url=db_url, top_k=settings.top_k, bm25_weight=bm25_weight)
            if hits:
                # fetch rows by id and return ordered context
                conn = psycopg2.connect(db_url)
                cur = conn.cursor()
                ids = [h[0] for h in hits]
                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(f"SELECT id, text, page, start_word, end_word FROM vectors WHERE id IN ({placeholders})", tuple(ids))
                rows = cur.fetchall()
                conn.close()
                id_to_row = {r[0]: r for r in rows}
                out_parts: List[str] = []
                for vid, score in hits:
                    r = id_to_row.get(vid)
                    if not r:
                        continue
                    _, text, page, start_word, end_word = r
                    meta = f"(page {page} words {start_word}-{end_word})"
                    out_parts.append(f"{meta}\n{text}")
                return "\n\n".join(out_parts)

    # fallback: support chunks as list of dicts with provenance metadata or simple strings
    query_embedding = client.embeddings.create(model=settings.embedding_model, input=query).data[0].embedding
    scored = [(idx, cosine(query_embedding, emb)) for idx, emb in enumerate(chunk_embeddings)]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [idx for idx, _ in scored[: settings.top_k]]
    top_sorted = sorted(top)

    out_parts: List[str] = []
    for i in top_sorted:
        chunk = chunks[i]
        if isinstance(chunk, dict):
            meta = f"(page {chunk.get('page')} words {chunk.get('start_word')}-{chunk.get('end_word')})"
            out_parts.append(f"{meta}\n{chunk.get('text')}")
        else:
            out_parts.append(str(chunk))
    return "\n\n".join(out_parts)


def prepare_chunks_for_paper(paper: Paper, settings: Settings) -> List[Dict]:
    """Prepare provenance-aware chunks for a paper.

    Args:
        paper: Paper to chunk.
        settings: Runtime settings.

    Returns:
        List[Dict]: Chunk dicts with provenance metadata.
    """
    if paper.pages:
        page_texts = paper.pages
    else:
        # fall back to whole-text split into a single page
        page_texts = [paper.text]
    return chunk_pages(page_texts, settings.chunk_words, settings.chunk_overlap)


def summarize_paper(client: OpenAI, paper: Paper, settings: Settings) -> str:
    """Summarize a paper using retrieved context and a chat model.

    Args:
        client: OpenAI client.
        paper: Paper to summarize.
        settings: Runtime settings.

    Returns:
        str: Model-generated summary.
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
        query="Summarize the research question, methods, key findings, and implications.",
        client=client,
        settings=settings,
    )

    user_input = (
        f"Title: {paper.title}\n"
        f"Author: {paper.author}\n\n"
        f"Context:\n{context}\n\n"
        "Write the summary now."
    )
    return call_openai(
        client,
        model=settings.chat_model,
        instructions=MAIN_SUMMARY_PROMPT,
        user_input=user_input,
        max_output_tokens=None,
    ).strip()


def load_papers(paths: Iterable[Path]) -> List[Paper]:
    """Load and extract text for a collection of PDF files.

    Args:
        paths: Iterable of PDF paths.

    Returns:
        List[Paper]: Extracted paper objects.
    """
    papers: List[Paper] = []
    for path in paths:
        metadata = run_pdfinfo(path)
        page_texts = run_pdftotext_pages(path)
        normalized_pages = [normalize_text(p) for p in page_texts if p is not None]
        text = "\n\n".join(p for p in normalized_pages if p)
        papers.append(
            Paper(
                path=path,
                title=metadata["title"],
                author=metadata["author"],
                text=text,
                pages=normalized_pages or None,
            )
        )
    return papers


def main() -> None:
    """Entry point for summarizing economics papers from the papers directory."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    settings = load_settings()
    client = OpenAI()

    if not settings.papers_dir.exists():
        raise SystemExit(f"Papers directory not found: {settings.papers_dir}")

    pdf_files = sorted(settings.papers_dir.glob("*.pdf"))
    if not pdf_files:
        raise SystemExit("No PDF files found in papers directory.")

    selected = pdf_files[: settings.max_papers]
    papers = load_papers(selected)

    print("Hello world RAG summary for economics papers")
    print(f"Using {len(papers)} paper(s) from {settings.papers_dir}")

    for paper in papers:
        print("\n" + "=" * 80)
        print(f"{paper.title}  |  {paper.author}  |  {paper.path.name}")
        summary = summarize_paper(client, paper, settings)
        print(summary)


if __name__ == "__main__":
    main()
