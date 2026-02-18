"""Hybrid retrieval over Postgres metadata and vector indexes (BM25 + embeddings). Used by top_k_context when DATABASE_URL is configured."""

from __future__ import annotations

from typing import List, Tuple, Dict
import json
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from ragonometrics.db.connection import connect


def _normalize(vec: np.ndarray) -> np.ndarray:
    """Normalize.

    Args:
        vec (np.ndarray): Input value for vec.

    Returns:
        np.ndarray: NumPy array containing the computed values.
    """
    norm = np.linalg.norm(vec, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return vec / norm


def _to_pgvector_literal(vec: np.ndarray) -> str:
    """To pgvector literal.

    Args:
        vec (np.ndarray): Input value for vec.

    Returns:
        str: Computed string result.
    """
    values = vec[0].tolist()
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


def _load_index_sidecar(path: str) -> Dict:
    """Load index sidecar.

    Args:
        path (str): Filesystem path value.

    Returns:
        Dict: Dictionary containing the computed result payload.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    sidecar = Path(path).with_suffix(".index.version.json")
    if not sidecar.exists():
        raise RuntimeError(f"Index sidecar not found: {sidecar}")
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read index sidecar: {sidecar}") from exc


def _verify_index_version(path: str, db_index_id: str | None) -> None:
    """Verify index version.

    Args:
        path (str): Filesystem path value.
        db_index_id (str | None): Input value for db index id.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    if os.environ.get("ALLOW_UNVERIFIED_INDEX"):
        return
    sidecar = _load_index_sidecar(path)
    sidecar_id = sidecar.get("index_id")
    if not sidecar_id:
        raise RuntimeError(f"Index sidecar missing index_id: {path}")
    if not db_index_id:
        raise RuntimeError(f"DB index_id missing for shard: {path}")
    if sidecar_id != db_index_id:
        raise RuntimeError(f"Index id mismatch for shard {path}: sidecar={sidecar_id} db={db_index_id}")


def _load_active_indexes(db_url: str) -> List[Tuple[str, faiss.Index]]:
    """Load active FAISS indexes from metadata.

    Args:
        db_url (str): Postgres connection URL.

    Returns:
        List[Tuple[str, faiss.Index]]: List result produced by the operation.
    """
    conn = connect(db_url, require_migrated=True)
    cur = conn.cursor()
    cur.execute("SELECT shard_name, path, index_id FROM indexing.index_shards WHERE is_active = TRUE ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    res = []
    for shard_name, path, index_id in rows:
        _verify_index_version(path, index_id)
        idx = faiss.read_index(path)
        res.append((shard_name, idx))
    return res


def _normalize_paper_path(paper_path: str | None) -> str | None:
    """Normalize a paper path for cross-platform DB comparisons."""
    if paper_path is None:
        return None
    normalized = str(paper_path).strip().replace("\\", "/")
    return normalized or None


def _load_texts_for_shards(db_url: str, *, paper_path: str | None = None) -> Tuple[List[str], List[int]]:
    """Load vector texts and ids from Postgres.

    Args:
        db_url (str): Postgres connection URL.

    Returns:
        Tuple[List[str], List[int]]: List result produced by the operation.
    """
    conn = connect(db_url, require_migrated=True)
    cur = conn.cursor()
    normalized_paper_path = _normalize_paper_path(paper_path)
    if normalized_paper_path:
        cur.execute(
            """
            SELECT id, text
            FROM indexing.vectors
            WHERE lower(replace(COALESCE(paper_path, ''), '\\', '/')) = lower(%s)
            ORDER BY id
            """,
            (normalized_paper_path,),
        )
    else:
        cur.execute("SELECT id, text FROM indexing.vectors ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    return texts, ids


def _set_diskann_runtime_knobs(cur) -> None:
    # Query-time tuning knobs; ignored where unsupported.
    """Set diskann runtime knobs.

    Args:
        cur (Any): Open database cursor.
    """
    try:
        if os.environ.get("DISKANN_QUERY_RESCORE"):
            cur.execute(f"SET LOCAL diskann.query_rescore = {int(os.environ['DISKANN_QUERY_RESCORE'])}")
        if os.environ.get("DISKANN_QUERY_SEARCH_LIST_SIZE"):
            cur.execute(
                f"SET LOCAL diskann.query_search_list_size = {int(os.environ['DISKANN_QUERY_SEARCH_LIST_SIZE'])}"
            )
    except Exception:
        pass


def _embedding_search_pg(
    db_url: str,
    vec: np.ndarray,
    top_k: int,
    *,
    paper_path: str | None = None,
) -> List[Tuple[int, float]]:
    """Embedding search pg.

    Args:
        db_url (str): Postgres connection URL.
        vec (np.ndarray): Input value for vec.
        top_k (int): Input value for top k.

    Returns:
        List[Tuple[int, float]]: List result produced by the operation.
    """
    conn = connect(db_url, require_migrated=True)
    cur = conn.cursor()
    vector_literal = _to_pgvector_literal(vec)
    normalized_paper_path = _normalize_paper_path(paper_path)
    rows = []
    try:
        _set_diskann_runtime_knobs(cur)
        sql = """
            SELECT id, (1 - (embedding <=> %s::vector)) AS score
            FROM indexing.vectors
            WHERE embedding IS NOT NULL
        """
        params: list[Any] = [vector_literal]
        if normalized_paper_path:
            sql += " AND lower(replace(COALESCE(paper_path, ''), '\\\\', '/')) = lower(%s)"
            params.append(normalized_paper_path)
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([vector_literal, top_k * 5])
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    finally:
        conn.close()
    return [(int(r[0]), float(r[1])) for r in rows if r and r[0] is not None]


def _embedding_search_faiss(db_url: str, vec: np.ndarray, top_k: int) -> List[Tuple[int, float]]:
    """Embedding search faiss.

    Args:
        db_url (str): Postgres connection URL.
        vec (np.ndarray): Input value for vec.
        top_k (int): Input value for top k.

    Returns:
        List[Tuple[int, float]]: List result produced by the operation.
    """
    indexes = _load_active_indexes(db_url)
    if not indexes:
        return []
    _, index = indexes[0]
    D, I = index.search(vec, top_k * 5)
    hits = list(zip(I[0].tolist(), D[0].tolist()))
    return [(int(doc_id), float(score)) for doc_id, score in hits if int(doc_id) >= 0]


def _embed_query(
    *,
    query: str,
    embedding_client: Any,
    embedding_model: str,
) -> List[float]:
    """Embed one query using runtime/provider or legacy OpenAI-like client."""
    # Bound embeddings capability from runtime.
    if hasattr(embedding_client, "embed"):
        response = embedding_client.embed(texts=[query], model=embedding_model, metadata={"capability": "embeddings"})
        if response.embeddings:
            return list(response.embeddings[0])
    # Runtime container exposing .embeddings capability.
    if hasattr(embedding_client, "embeddings") and hasattr(embedding_client.embeddings, "embed"):
        response = embedding_client.embeddings.embed(
            texts=[query],
            model=embedding_model,
            metadata={"capability": "embeddings"},
        )
        if response.embeddings:
            return list(response.embeddings[0])
    # Legacy OpenAI-like client.
    if hasattr(embedding_client, "embeddings") and hasattr(embedding_client.embeddings, "create"):
        resp = embedding_client.embeddings.create(model=embedding_model, input=[query])
        data = getattr(resp, "data", None) or []
        if data:
            return list(getattr(data[0], "embedding", []) or [])
    raise RuntimeError("No compatible embedding interface found for hybrid retrieval")


def hybrid_search(
    query: str,
    *,
    db_url: str,
    top_k: int = 6,
    bm25_weight: float = 0.5,
    embedding_client: Any = None,
    embedding_model: str | None = None,
    client: Any = None,
    paper_path: str | None = None,
) -> List[Tuple[int, float]]:
    """Perform hybrid BM25 + embedding search over stored vectors.

    Args:
        query (str): Input query text.
        db_url (str): Postgres connection URL.
        top_k (int): Input value for top k.
        bm25_weight (float): Input value for bm25 weight.
        embedding_client (Any): Provider/runtime or legacy client for query embeddings.
        embedding_model (str | None): Embedding model override.
        client (Any): Backward-compatible alias for ``embedding_client``.
        paper_path (str | None): Optional absolute/normalized paper path for strict retrieval scoping.

    Returns:
        List[Tuple[int, float]]: List result produced by the operation.
    """
    # 1. BM25 over stored texts
    normalized_paper_path = _normalize_paper_path(paper_path)
    texts, ids = _load_texts_for_shards(db_url, paper_path=normalized_paper_path)
    if not texts:
        return []
    tokenized = [t.split() for t in texts]
    bm25 = BM25Okapi(tokenized)
    q_tokens = query.split()
    bm25_scores = bm25.get_scores(q_tokens)

    # Backward compatibility: accept `client=` as alias for embedding_client.
    embed_source = embedding_client if embedding_client is not None else client
    if embed_source is None:
        raise RuntimeError("hybrid_search requires embedding_client (or legacy client)")
    model_name = str(embedding_model or os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"))

    # 2. embedding search via DB/FAISS.
    emb = _embed_query(query=query, embedding_client=embed_source, embedding_model=model_name)
    vec = np.array([emb], dtype=np.float32)
    vec = _normalize(vec)

    # 2a. Prefer Postgres-native vector search (pgvector + vectorscale).
    emb_hits: List[Tuple[int, float]] = []
    try:
        emb_hits = _embedding_search_pg(db_url, vec, top_k, paper_path=normalized_paper_path)
    except Exception:
        emb_hits = []

    # 2b. Fallback to legacy FAISS path when DB vector search is unavailable.
    # FAISS path cannot be scoped to a single paper, so keep it disabled when a scope is requested.
    if not emb_hits and not normalized_paper_path:
        try:
            emb_hits = _embedding_search_faiss(db_url, vec, top_k)
        except Exception:
            emb_hits = []
    if not emb_hits:
        return []

    # combine scores: map bm25 by position in ids
    id_to_bm = {doc_id: float(bm25_scores[i]) for i, doc_id in enumerate(ids)}

    combined: Dict[int, float] = {}
    for doc_id, score in emb_hits:
        bm = id_to_bm.get(doc_id, 0.0)
        combined[doc_id] = combined.get(doc_id, 0.0) + (1.0 - bm25_weight) * float(score) + bm25_weight * bm

    # sort by combined score
    sorted_hits = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return sorted_hits
