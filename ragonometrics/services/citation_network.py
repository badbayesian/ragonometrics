"""OpenAlex citation network service for selected paper views."""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from ragonometrics.db.connection import pooled_connection
from ragonometrics.integrations.openalex import request_json as openalex_request_json
from ragonometrics.services.papers import PaperRef, load_prepared

_GRAPH_CACHE_STATUS_FRESH = "fresh_hit"
_GRAPH_CACHE_STATUS_STALE = "stale_hit"
_GRAPH_CACHE_STATUS_MISS = "miss_or_hard_expired"
_DEFAULT_GRAPH_CACHE_TTL_SECONDS = 60 * 60 * 24
_DEFAULT_GRAPH_CACHE_STALE_SECONDS = 60 * 60 * 24 * 7
_DEFAULT_GRAPH_CACHE_ALGO_VERSION = "v1"


def _openalex_work_id(value: Any) -> str:
    """Internal helper for openalex work id."""
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
    return text if text.startswith("W") else ""


def _openalex_work_url(value: Any) -> str:
    """Internal helper for openalex work url."""
    work_id = _openalex_work_id(value)
    return f"https://openalex.org/{work_id}" if work_id else ""


def _openalex_work_summary(work_id: str, *, include_references: bool = False) -> Optional[Dict[str, Any]]:
    """Internal helper for openalex work summary."""
    key = _openalex_work_id(work_id)
    if not key:
        return None
    fields = ["id", "display_name", "publication_year", "doi", "cited_by_count"]
    if include_references:
        fields.append("referenced_works")
    url = f"https://api.openalex.org/works/{key}"
    payload = openalex_request_json(url, params={"select": ",".join(fields)}, timeout=20)
    return payload if isinstance(payload, dict) else None


def _openalex_citing_works(work_id: str, *, limit: int) -> List[Dict[str, Any]]:
    """Internal helper for openalex citing works."""
    key = _openalex_work_id(work_id)
    if not key or limit <= 0:
        return []
    payload = openalex_request_json(
        "https://api.openalex.org/works",
        params={
            "filter": f"cites:{key}",
            "per-page": int(limit),
            "select": "id,display_name,publication_year,doi,cited_by_count",
            "sort": "cited_by_count:desc",
        },
        timeout=20,
    )
    results = (payload or {}).get("results") if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _work_title(work: Dict[str, Any]) -> str:
    """Internal helper for work title."""
    return str(work.get("display_name") or work.get("title") or work.get("id") or "Unknown paper")


def _sort_works_desc(works: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Internal helper for sort works desc."""
    return sorted(
        works,
        key=lambda w: (-(int(w.get("cited_by_count") or 0)), _work_title(w).lower()),
    )


def _work_row(work: Dict[str, Any], group: str, *, hop: Optional[int] = None) -> Dict[str, Any]:
    """Internal helper for work row."""
    out = {
        "id": str(work.get("id") or ""),
        "openalex_url": _openalex_work_url(work.get("id")),
        "title": _work_title(work),
        "publication_year": work.get("publication_year"),
        "doi": str(work.get("doi") or ""),
        "cited_by_count": int(work.get("cited_by_count") or 0),
        "group": group,
    }
    if hop is not None:
        out["hop"] = int(hop)
    return out


def _bounded_int_env(name: str, default: int, *, low: int, high: int) -> int:
    """Internal helper for bounded int env."""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(low, min(high, value))


def _graph_cache_disabled() -> bool:
    """Internal helper for graph cache disabled."""
    return str(os.environ.get("OPENALEX_NETWORK_GRAPH_CACHE_DISABLE") or "").strip() == "1"


def _graph_cache_ttl_seconds() -> int:
    """Internal helper for graph cache ttl seconds."""
    return _bounded_int_env(
        "OPENALEX_NETWORK_GRAPH_CACHE_TTL_SECONDS",
        _DEFAULT_GRAPH_CACHE_TTL_SECONDS,
        low=60,
        high=60 * 60 * 24 * 30,
    )


def _graph_cache_stale_seconds() -> int:
    """Internal helper for graph cache stale seconds."""
    return _bounded_int_env(
        "OPENALEX_NETWORK_GRAPH_CACHE_STALE_SECONDS",
        _DEFAULT_GRAPH_CACHE_STALE_SECONDS,
        low=60,
        high=60 * 60 * 24 * 90,
    )


def _graph_cache_algo_version() -> str:
    """Internal helper for graph cache algo version."""
    return str(os.environ.get("OPENALEX_NETWORK_GRAPH_CACHE_ALGO_VERSION") or _DEFAULT_GRAPH_CACHE_ALGO_VERSION).strip()


def _db_url() -> str:
    """Internal helper for db url."""
    return str(os.environ.get("DATABASE_URL") or "").strip()


def _now_utc() -> datetime:
    """Internal helper for now utc."""
    return datetime.now(timezone.utc)


def _normalize_json_dict(value: Any) -> Dict[str, Any]:
    """Internal helper for normalize json dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_db_ts(value: Any) -> Optional[datetime]:
    """Internal helper for parse db ts."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _graph_cache_key(
    *,
    center_work_id: str,
    n_hops: int,
    max_references: int,
    max_citing: int,
    max_nodes: int,
    algo_version: str,
) -> str:
    """Internal helper for graph cache key."""
    payload = {
        "center_work_id": _openalex_work_id(center_work_id),
        "n_hops": int(n_hops),
        "max_references": int(max_references),
        "max_citing": int(max_citing),
        "max_nodes": int(max_nodes),
        "algo_version": str(algo_version or "").strip(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _advisory_lock_key(cache_key: str) -> int:
    """Internal helper for advisory lock key."""
    raw = int(hashlib.sha256(str(cache_key or "").encode("utf-8")).hexdigest()[:16], 16)
    return raw - (1 << 64) if raw >= (1 << 63) else raw


def _try_advisory_lock(cache_key: str, *, db_url: Optional[str] = None) -> bool:
    """Internal helper for try advisory lock."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved:
        return True
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_advisory_lock_key(cache_key),))
            row = cur.fetchone()
            conn.commit()
            return bool(row and row[0])
    except Exception:
        # sqlite-backed tests and non-Postgres runtime can proceed without lock support.
        return True


def _release_advisory_lock(cache_key: str, *, db_url: Optional[str] = None) -> None:
    """Internal helper for release advisory lock."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved:
        return
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (_advisory_lock_key(cache_key),))
            conn.commit()
    except Exception:
        return


def _read_graph_cache(cache_key: str, *, db_url: Optional[str] = None) -> Dict[str, Any]:
    """Internal helper for read graph cache."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved or not cache_key:
        return {}
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    cache_key,
                    center_work_id,
                    n_hops,
                    max_references,
                    max_citing,
                    max_nodes,
                    algo_version,
                    payload_json,
                    summary_json,
                    generated_at,
                    expires_at,
                    stale_until,
                    last_accessed_at,
                    refresh_job_id,
                    refresh_failures
                FROM enrichment.openalex_citation_graph_cache
                WHERE cache_key = %s
                """,
                (cache_key,),
            )
            row = cur.fetchone()
            conn.commit()
    except Exception:
        return {}
    if not row:
        return {}
    return {
        "cache_key": str(row[0] or ""),
        "center_work_id": str(row[1] or ""),
        "n_hops": int(row[2] or 0),
        "max_references": int(row[3] or 0),
        "max_citing": int(row[4] or 0),
        "max_nodes": int(row[5] or 0),
        "algo_version": str(row[6] or ""),
        "payload_json": _normalize_json_dict(row[7]),
        "summary_json": _normalize_json_dict(row[8]),
        "generated_at": _parse_db_ts(row[9]),
        "expires_at": _parse_db_ts(row[10]),
        "stale_until": _parse_db_ts(row[11]),
        "last_accessed_at": _parse_db_ts(row[12]),
        "refresh_job_id": str(row[13] or ""),
        "refresh_failures": int(row[14] or 0),
    }


def _touch_graph_cache(cache_key: str, *, db_url: Optional[str] = None) -> None:
    """Internal helper for touch graph cache."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved or not cache_key:
        return
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE enrichment.openalex_citation_graph_cache
                SET last_accessed_at = NOW(),
                    updated_at = NOW()
                WHERE cache_key = %s
                """,
                (cache_key,),
            )
            conn.commit()
    except Exception:
        return


def _set_refresh_job_id(cache_key: str, job_id: str, *, db_url: Optional[str] = None) -> None:
    """Internal helper for set refresh job id."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved or not cache_key:
        return
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE enrichment.openalex_citation_graph_cache
                SET refresh_job_id = %s,
                    updated_at = NOW()
                WHERE cache_key = %s
                """,
                (str(job_id or "").strip() or None, cache_key),
            )
            conn.commit()
    except Exception:
        return


def mark_cached_citation_refresh_failure(*, cache_key: str, db_url: Optional[str] = None) -> None:
    """Increment refresh-failure metadata for a cached citation graph entry."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved or not cache_key:
        return
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE enrichment.openalex_citation_graph_cache
                SET refresh_failures = refresh_failures + 1,
                    refresh_job_id = NULL,
                    updated_at = NOW()
                WHERE cache_key = %s
                """,
                (cache_key,),
            )
            conn.commit()
    except Exception:
        return


def _upsert_graph_cache(
    *,
    cache_key: str,
    center_work_id: str,
    n_hops: int,
    max_references: int,
    max_citing: int,
    max_nodes: int,
    algo_version: str,
    payload_json: Dict[str, Any],
    summary_json: Dict[str, Any],
    refresh_job_id: Optional[str],
    generated_at: Optional[datetime] = None,
    db_url: Optional[str] = None,
) -> None:
    """Internal helper for upsert graph cache."""
    resolved = str(db_url or _db_url() or "").strip()
    if not resolved or not cache_key:
        return
    generated = generated_at or _now_utc()
    expires_at = generated + timedelta(seconds=int(_graph_cache_ttl_seconds()))
    stale_until = expires_at + timedelta(seconds=int(_graph_cache_stale_seconds()))
    try:
        with pooled_connection(resolved) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO enrichment.openalex_citation_graph_cache
                (
                    cache_key,
                    center_work_id,
                    n_hops,
                    max_references,
                    max_citing,
                    max_nodes,
                    algo_version,
                    payload_json,
                    summary_json,
                    generated_at,
                    expires_at,
                    stale_until,
                    last_accessed_at,
                    refresh_job_id,
                    refresh_failures,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s, 0, NOW(), NOW()
                )
                ON CONFLICT (cache_key) DO UPDATE SET
                    center_work_id = EXCLUDED.center_work_id,
                    n_hops = EXCLUDED.n_hops,
                    max_references = EXCLUDED.max_references,
                    max_citing = EXCLUDED.max_citing,
                    max_nodes = EXCLUDED.max_nodes,
                    algo_version = EXCLUDED.algo_version,
                    payload_json = EXCLUDED.payload_json,
                    summary_json = EXCLUDED.summary_json,
                    generated_at = EXCLUDED.generated_at,
                    expires_at = EXCLUDED.expires_at,
                    stale_until = EXCLUDED.stale_until,
                    last_accessed_at = EXCLUDED.last_accessed_at,
                    refresh_job_id = EXCLUDED.refresh_job_id,
                    refresh_failures = 0,
                    updated_at = NOW()
                """,
                (
                    cache_key,
                    _openalex_work_id(center_work_id),
                    int(n_hops),
                    int(max_references),
                    int(max_citing),
                    int(max_nodes),
                    str(algo_version or "").strip(),
                    json.dumps(payload_json or {}, ensure_ascii=False),
                    json.dumps(summary_json or {}, ensure_ascii=False),
                    generated.isoformat(),
                    expires_at.isoformat(),
                    stale_until.isoformat(),
                    generated.isoformat(),
                    str(refresh_job_id or "").strip() or None,
                ),
            )
            conn.commit()
    except Exception:
        return


def _enqueue_graph_refresh_job(
    *,
    cache_key: str,
    center_work_id: str,
    n_hops: int,
    max_references: int,
    max_citing: int,
    max_nodes: int,
    algo_version: str,
    existing_refresh_job_id: str,
    db_url: Optional[str] = None,
) -> bool:
    """Internal helper for enqueue graph refresh job."""
    if str(existing_refresh_job_id or "").strip():
        return True
    try:
        from ragonometrics.integrations.rq_queue import enqueue_openalex_network_refresh

        job = enqueue_openalex_network_refresh(
            db_url=db_url,
            cache_key=cache_key,
            center_work_id=center_work_id,
            n_hops=n_hops,
            max_references=max_references,
            max_citing=max_citing,
            max_nodes=max_nodes,
            algo_version=algo_version,
        )
        _set_refresh_job_id(cache_key, str(job.id or "").strip(), db_url=db_url)
        return True
    except Exception:
        return False


def _attach_cache_metadata(
    payload: Dict[str, Any],
    *,
    status: str,
    cache_key: str,
    generated_at: Optional[datetime],
    expires_at: Optional[datetime],
    stale_until: Optional[datetime],
    refresh_enqueued: bool,
) -> Dict[str, Any]:
    """Internal helper for attach cache metadata."""
    out = dict(payload or {})
    out["cache"] = {
        "status": str(status or _GRAPH_CACHE_STATUS_MISS),
        "cache_key": str(cache_key or ""),
        "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else "",
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else "",
        "stale_until": stale_until.isoformat() if isinstance(stale_until, datetime) else "",
        "refresh_enqueued": bool(refresh_enqueued),
    }
    return out


def _compute_citation_network(
    center_id: str,
    *,
    max_references: int,
    max_citing: int,
    n_hops: int,
    max_nodes: int,
) -> Dict[str, Any]:
    """Internal helper for compute citation network."""
    center_work_id = _openalex_work_id(center_id)
    if not center_work_id:
        return {
            "available": False,
            "message": "Invalid OpenAlex center work id.",
            "center": None,
            "references": [],
            "citing": [],
        }

    max_refs = max(0, min(50, int(max_references)))
    max_cit = max(0, min(50, int(max_citing)))
    hops = max(1, min(5, int(n_hops or 2)))
    max_nodes = max(20, min(2000, int(max_nodes)))

    summary_cache: Dict[str, Dict[str, Any]] = {}

    def load_summary(work_id: str, *, include_references: bool) -> Optional[Dict[str, Any]]:
        """Handle load summary."""
        key = _openalex_work_id(work_id)
        if not key:
            return None
        cached = summary_cache.get(key)
        if cached is not None:
            if include_references and not isinstance(cached.get("referenced_works"), list):
                fresh = _openalex_work_summary(key, include_references=True)
                if isinstance(fresh, dict):
                    summary_cache[key] = fresh
                    return fresh
            return cached
        fetched = _openalex_work_summary(key, include_references=include_references)
        if isinstance(fetched, dict):
            summary_cache[key] = fetched
            return fetched
        return None

    center = load_summary(center_work_id, include_references=True) or {}
    if not center:
        return {
            "available": False,
            "message": "Unable to load OpenAlex center work.",
            "center": None,
            "references": [],
            "citing": [],
        }

    root_refs: List[Dict[str, Any]] = []
    root_citing: List[Dict[str, Any]] = []
    discovered_ids: Set[str] = {center_work_id}
    node_depth: Dict[str, int] = {center_work_id: 0}
    node_group: Dict[str, str] = {center_work_id: "center"}
    edge_pairs: Set[Tuple[str, str]] = set()
    graph_edges: List[Dict[str, Any]] = []
    queue: Deque[str] = deque([center_work_id])

    while queue:
        current_id = queue.popleft()
        depth = int(node_depth.get(current_id, 0))
        expand_children = depth < hops and len(discovered_ids) < max_nodes
        current = load_summary(current_id, include_references=expand_children)
        if not isinstance(current, dict):
            continue

        references: List[Dict[str, Any]] = []
        if expand_children and max_refs > 0:
            raw_refs = current.get("referenced_works") if isinstance(current.get("referenced_works"), list) else []
            for ref_id in raw_refs[:max_refs]:
                if len(discovered_ids) >= max_nodes and _openalex_work_id(ref_id) not in discovered_ids:
                    break
                ref_summary = load_summary(str(ref_id), include_references=(depth + 1 < hops))
                if isinstance(ref_summary, dict):
                    references.append(ref_summary)
            references = _sort_works_desc(references)[:max_refs]

        citing: List[Dict[str, Any]] = []
        if expand_children and max_cit > 0:
            citing = _sort_works_desc(_openalex_citing_works(current_id, limit=max_cit))[:max_cit]

        if depth == 0:
            root_refs = references
            root_citing = citing

        for ref in references:
            ref_id = _openalex_work_id(ref.get("id"))
            if not ref_id:
                continue
            pair = (current_id, ref_id)
            if pair not in edge_pairs:
                edge_pairs.add(pair)
                graph_edges.append(
                    {
                        "from": f"https://openalex.org/{current_id}",
                        "to": str(ref.get("id") or ""),
                        "relation": "references",
                        "hop": depth + 1,
                    }
                )
            if ref_id not in discovered_ids and len(discovered_ids) < max_nodes:
                discovered_ids.add(ref_id)
                node_depth[ref_id] = depth + 1
                node_group[ref_id] = "reference" if depth == 0 else "neighborhood"
                if depth + 1 < hops:
                    queue.append(ref_id)

        for cit in citing:
            cit_id = _openalex_work_id(cit.get("id"))
            if not cit_id:
                continue
            pair = (cit_id, current_id)
            if pair not in edge_pairs:
                edge_pairs.add(pair)
                graph_edges.append(
                    {
                        "from": str(cit.get("id") or ""),
                        "to": f"https://openalex.org/{current_id}",
                        "relation": "cites",
                        "hop": depth + 1,
                    }
                )
            if cit_id not in discovered_ids and len(discovered_ids) < max_nodes:
                discovered_ids.add(cit_id)
                node_depth[cit_id] = depth + 1
                node_group[cit_id] = "citing" if depth == 0 else "neighborhood"
                if depth + 1 < hops:
                    queue.append(cit_id)

    graph_nodes: List[Dict[str, Any]] = []
    for work_id in sorted(discovered_ids, key=lambda wid: (node_depth.get(wid, 999), wid)):
        summary = load_summary(work_id, include_references=False)
        if not isinstance(summary, dict):
            continue
        graph_nodes.append(
            _work_row(
                summary,
                node_group.get(work_id, "neighborhood"),
                hop=node_depth.get(work_id, 0),
            )
        )

    center_row = _work_row(center, "center")
    reference_rows = [_work_row(item, "reference", hop=1) for item in root_refs]
    citing_rows = [_work_row(item, "citing", hop=1) for item in root_citing]
    return {
        "available": True,
        "center": center_row,
        "references": reference_rows,
        "citing": citing_rows,
        "graph": {
            "nodes": graph_nodes,
            "edges": graph_edges,
            "n_hops": hops,
            "node_count": len(graph_nodes),
            "edge_count": len(graph_edges),
        },
        "summary": {
            "references_shown": len(reference_rows),
            "citing_shown": len(citing_rows),
            "max_references": max_refs,
            "max_citing": max_cit,
            "n_hops_requested": hops,
            "nodes_shown": len(graph_nodes),
            "edges_shown": len(graph_edges),
            "max_nodes": max_nodes,
        },
    }


def _compute_and_upsert_graph(
    *,
    cache_key: str,
    center_work_id: str,
    n_hops: int,
    max_references: int,
    max_citing: int,
    max_nodes: int,
    algo_version: str,
    db_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Internal helper for compute and upsert graph."""
    computed = _compute_citation_network(
        center_work_id,
        max_references=max_references,
        max_citing=max_citing,
        n_hops=n_hops,
        max_nodes=max_nodes,
    )
    if bool(computed.get("available")):
        _upsert_graph_cache(
            cache_key=cache_key,
            center_work_id=center_work_id,
            n_hops=n_hops,
            max_references=max_references,
            max_citing=max_citing,
            max_nodes=max_nodes,
            algo_version=algo_version,
            payload_json=computed,
            summary_json=_normalize_json_dict(computed.get("summary")),
            refresh_job_id=None,
            db_url=db_url,
        )
        cached = _read_graph_cache(cache_key, db_url=db_url)
        return _attach_cache_metadata(
            computed,
            status=_GRAPH_CACHE_STATUS_MISS,
            cache_key=cache_key,
            generated_at=cached.get("generated_at"),
            expires_at=cached.get("expires_at"),
            stale_until=cached.get("stale_until"),
            refresh_enqueued=False,
        )
    return _attach_cache_metadata(
        computed,
        status=_GRAPH_CACHE_STATUS_MISS,
        cache_key=cache_key,
        generated_at=None,
        expires_at=None,
        stale_until=None,
        refresh_enqueued=False,
    )


def refresh_cached_citation_graph(*, payload: Dict[str, Any], db_url: Optional[str] = None) -> Dict[str, Any]:
    """Recompute and persist one cached citation graph payload from queued parameters."""
    center_work_id = _openalex_work_id(payload.get("center_work_id"))
    if not center_work_id:
        raise ValueError("center_work_id is required for openalex_network_refresh.")
    n_hops = max(1, min(5, int(payload.get("n_hops") or 2)))
    max_references = max(0, min(50, int(payload.get("max_references") or 10)))
    max_citing = max(0, min(50, int(payload.get("max_citing") or 10)))
    max_nodes = max(20, min(2000, int(payload.get("max_nodes") or _bounded_int_env("OPENALEX_NETWORK_MAX_NODES", 250, low=20, high=2000))))
    algo_version = str(payload.get("algo_version") or _graph_cache_algo_version()).strip() or _DEFAULT_GRAPH_CACHE_ALGO_VERSION
    cache_key = str(payload.get("cache_key") or "").strip() or _graph_cache_key(
        center_work_id=center_work_id,
        n_hops=n_hops,
        max_references=max_references,
        max_citing=max_citing,
        max_nodes=max_nodes,
        algo_version=algo_version,
    )
    out = _compute_citation_network(
        center_work_id,
        max_references=max_references,
        max_citing=max_citing,
        n_hops=n_hops,
        max_nodes=max_nodes,
    )
    if not bool(out.get("available")):
        raise RuntimeError("Failed to refresh OpenAlex citation graph cache: compute returned unavailable.")
    _upsert_graph_cache(
        cache_key=cache_key,
        center_work_id=center_work_id,
        n_hops=n_hops,
        max_references=max_references,
        max_citing=max_citing,
        max_nodes=max_nodes,
        algo_version=algo_version,
        payload_json=out,
        summary_json=_normalize_json_dict(out.get("summary")),
        refresh_job_id=None,
        db_url=db_url,
    )
    summary = _normalize_json_dict(out.get("summary"))
    return {
        "cache_key": cache_key,
        "center_work_id": center_work_id,
        "n_hops": n_hops,
        "max_references": max_references,
        "max_citing": max_citing,
        "max_nodes": max_nodes,
        "algo_version": algo_version,
        "available": bool(out.get("available")),
        "nodes_shown": int(summary.get("nodes_shown") or 0),
        "edges_shown": int(summary.get("edges_shown") or 0),
    }


def citation_network_for_paper(
    paper_ref: PaperRef,
    *,
    max_references: int = 10,
    max_citing: int = 10,
    n_hops: int = 2,
) -> Dict[str, Any]:
    """Return OpenAlex citation neighborhood around the selected paper."""
    paper, _, _, _ = load_prepared(paper_ref)
    meta = paper.openalex if isinstance(paper.openalex, dict) else {}
    center_id = _openalex_work_id(meta.get("id") if isinstance(meta, dict) else "")
    if not center_id:
        return {
            "available": False,
            "paper_id": paper_ref.paper_id,
            "paper_name": paper_ref.name,
            "message": "No OpenAlex work id found for this paper.",
            "center": None,
            "references": [],
            "citing": [],
            "cache": {
                "status": _GRAPH_CACHE_STATUS_MISS,
                "cache_key": "",
                "generated_at": "",
                "expires_at": "",
                "stale_until": "",
                "refresh_enqueued": False,
            },
        }

    max_refs = max(0, min(50, int(max_references)))
    max_cit = max(0, min(50, int(max_citing)))
    hops = max(1, min(5, int(n_hops or 2)))
    max_nodes = _bounded_int_env("OPENALEX_NETWORK_MAX_NODES", 250, low=20, high=2000)
    algo_version = _graph_cache_algo_version()
    cache_key = _graph_cache_key(
        center_work_id=center_id,
        n_hops=hops,
        max_references=max_refs,
        max_citing=max_cit,
        max_nodes=max_nodes,
        algo_version=algo_version,
    )

    if _graph_cache_disabled():
        out = _attach_cache_metadata(
            _compute_citation_network(
                center_id,
                max_references=max_refs,
                max_citing=max_cit,
                n_hops=hops,
                max_nodes=max_nodes,
            ),
            status=_GRAPH_CACHE_STATUS_MISS,
            cache_key=cache_key,
            generated_at=None,
            expires_at=None,
            stale_until=None,
            refresh_enqueued=False,
        )
        out["paper_id"] = paper_ref.paper_id
        out["paper_name"] = paper_ref.name
        return out

    db_url = _db_url()
    now = _now_utc()
    cached = _read_graph_cache(cache_key, db_url=db_url)
    if cached:
        payload = _normalize_json_dict(cached.get("payload_json"))
        expires_at = cached.get("expires_at")
        stale_until = cached.get("stale_until")
        generated_at = cached.get("generated_at")
        refresh_job_id = str(cached.get("refresh_job_id") or "").strip()
        if isinstance(expires_at, datetime) and now <= expires_at and payload:
            _touch_graph_cache(cache_key, db_url=db_url)
            out = _attach_cache_metadata(
                payload,
                status=_GRAPH_CACHE_STATUS_FRESH,
                cache_key=cache_key,
                generated_at=generated_at,
                expires_at=expires_at,
                stale_until=stale_until if isinstance(stale_until, datetime) else None,
                refresh_enqueued=bool(refresh_job_id),
            )
            out["paper_id"] = paper_ref.paper_id
            out["paper_name"] = paper_ref.name
            return out
        if isinstance(stale_until, datetime) and now <= stale_until and payload:
            refresh_enqueued = _enqueue_graph_refresh_job(
                cache_key=cache_key,
                center_work_id=center_id,
                n_hops=hops,
                max_references=max_refs,
                max_citing=max_cit,
                max_nodes=max_nodes,
                algo_version=algo_version,
                existing_refresh_job_id=refresh_job_id,
                db_url=db_url,
            )
            out = _attach_cache_metadata(
                payload,
                status=_GRAPH_CACHE_STATUS_STALE,
                cache_key=cache_key,
                generated_at=generated_at,
                expires_at=expires_at if isinstance(expires_at, datetime) else None,
                stale_until=stale_until,
                refresh_enqueued=refresh_enqueued,
            )
            out["paper_id"] = paper_ref.paper_id
            out["paper_name"] = paper_ref.name
            return out

    acquired = _try_advisory_lock(cache_key, db_url=db_url)
    try:
        if not acquired:
            loser_cached = _read_graph_cache(cache_key, db_url=db_url)
            loser_payload = _normalize_json_dict(loser_cached.get("payload_json"))
            if loser_payload:
                out = _attach_cache_metadata(
                    loser_payload,
                    status=_GRAPH_CACHE_STATUS_STALE,
                    cache_key=cache_key,
                    generated_at=loser_cached.get("generated_at"),
                    expires_at=loser_cached.get("expires_at"),
                    stale_until=loser_cached.get("stale_until"),
                    refresh_enqueued=bool(str(loser_cached.get("refresh_job_id") or "").strip()),
                )
                out["paper_id"] = paper_ref.paper_id
                out["paper_name"] = paper_ref.name
                return out

        out = _compute_and_upsert_graph(
            cache_key=cache_key,
            center_work_id=center_id,
            n_hops=hops,
            max_references=max_refs,
            max_citing=max_cit,
            max_nodes=max_nodes,
            algo_version=algo_version,
            db_url=db_url,
        )
        out["paper_id"] = paper_ref.paper_id
        out["paper_name"] = paper_ref.name
        return out
    finally:
        if acquired:
            _release_advisory_lock(cache_key, db_url=db_url)
