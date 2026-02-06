"""Index manifest and version sidecar builders. Used by indexer to record build provenance for reproducible artifacts."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
import hashlib
import platform
import sys
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path
from typing import Iterable, Optional

from ragonometrics.core.config import PROJECT_ROOT
from ragonometrics.core.main import Settings


def get_git_sha(repo_root: Path = PROJECT_ROOT) -> Optional[str]:
    """Return the current git SHA if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        sha = result.stdout.strip()
        return sha or None
    except Exception:
        return None


def _sha256_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
        if not parts.username and not parts.password:
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        netloc = f"{user}:***@{host}" if user else f"***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return url


def _sanitize_config(config: dict | None) -> dict | None:
    if not config:
        return None
    sanitized = dict(config)
    if "database_url" in sanitized:
        sanitized["database_url"] = _redact_url(sanitized.get("database_url"))
    return sanitized


def build_run_manifest(
    *,
    settings: Settings,
    paper_paths: Iterable[Path],
    index_path: Path,
    shard_path: Path,
    pipeline_run_id: Optional[int],
    corpus_fingerprint: Optional[str] = None,
    embedding_dim: Optional[int] = None,
    index_sha256: Optional[str] = None,
    embeddings_sha256: Optional[str] = None,
    paper_manifest: Optional[list[dict]] = None,
) -> dict:
    """Build a run manifest for an indexing run."""
    config_effective = _sanitize_config(settings.config_effective)
    dependency_fingerprint = {
        "pyproject_toml_sha256": _sha256_file(PROJECT_ROOT / "pyproject.toml"),
        "requirements_txt_sha256": _sha256_file(PROJECT_ROOT / "requirements.txt"),
        "poetry_lock_sha256": _sha256_file(PROJECT_ROOT / "poetry.lock"),
    }
    dependency_fingerprint = {k: v for k, v in dependency_fingerprint.items() if v}
    section_aware = None
    if settings.config_effective is not None and "section_aware_chunking" in settings.config_effective:
        section_aware = bool(settings.config_effective.get("section_aware_chunking"))
    manifest = {
        "git_sha": get_git_sha(),
        "config_path": str(settings.config_path) if settings.config_path else None,
        "config_hash": settings.config_hash,
        "config_effective": config_effective,
        "dependency_fingerprint": dependency_fingerprint or None,
        "python_version": sys.version,
        "platform": platform.platform(),
        "embedding_model": settings.embedding_model,
        "embedding_dim": embedding_dim,
        "chat_model": settings.chat_model,
        "chunking": {
            "scheme": "word",
            "chunk_words": settings.chunk_words,
            "chunk_overlap": settings.chunk_overlap,
            "section_aware": section_aware,
            "normalization": "normalize_text",
        },
        "batch_size": settings.batch_size,
        "top_k": settings.top_k,
        "corpus_fingerprint": corpus_fingerprint,
        "papers": paper_manifest if paper_manifest is not None else [str(p) for p in paper_paths],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "index_path": str(index_path),
        "index_version_path": str(shard_path),
        "index_sha256": index_sha256,
        "embeddings_sha256": embeddings_sha256,
        "pipeline_run_id": pipeline_run_id,
    }
    return manifest


def write_run_manifest(shard_path: Path, manifest: dict) -> Path:
    """Write a manifest JSON file next to the index shard."""
    manifest_path = shard_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def build_index_version(
    *,
    index_id: str,
    embedding_model: str,
    chunk_words: int,
    chunk_overlap: int,
    corpus_fingerprint: str,
    embedding_dim: Optional[int] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Build the index version payload for sidecar storage."""
    return {
        "index_id": index_id,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "embedding_model": embedding_model,
        "embedding_dim": embedding_dim,
        "chunk_words": chunk_words,
        "chunk_overlap": chunk_overlap,
        "corpus_fingerprint": corpus_fingerprint,
    }


def write_index_version_sidecar(shard_path: Path, payload: dict) -> Path:
    """Write an index version sidecar JSON next to the FAISS artifact."""
    sidecar = shard_path.with_suffix(".index.version.json")
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return sidecar

