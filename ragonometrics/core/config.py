"""Configuration loader and merger for Ragonometrics. Used by load_settings to build pipeline config from TOML and environment variables."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import tomllib
except ImportError:  # pragma: no cover - fallback for older Python
    import tomli as tomllib  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
SQLITE_DIR = PROJECT_ROOT / "sqlite"

ENV_CONFIG_MAP = {
    "DATABASE_URL": "database_url",
    "BM25_WEIGHT": "bm25_weight",
    "RERANKER_MODEL": "reranker_model",
    "RERANK_TOP_N": "rerank_top_n",
    "QUERY_EXPANSION": "query_expansion",
    "QUERY_EXPAND_MODEL": "query_expand_model",
    "FORCE_OCR": "force_ocr",
    "SECTION_AWARE_CHUNKING": "section_aware_chunking",
    "INDEX_IDEMPOTENT_SKIP": "index_idempotent_skip",
    "ALLOW_UNVERIFIED_INDEX": "allow_unverified_index",
}


def load_config(path: Path | None) -> Dict[str, Any]:
    """Load a TOML config file and return the ragonometrics section or top-level dict."""
    if not path or not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "ragonometrics" in data and isinstance(data["ragonometrics"], dict):
        return data["ragonometrics"]
    return data or {}


def hash_config_dict(config: Mapping[str, Any]) -> str:
    """Return a stable SHA-256 hash of a config mapping."""
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _env_or_config(env: Mapping[str, str], config: Mapping[str, Any], env_key: str, config_key: str, default: Any) -> Any:
    if env_key in env and env[env_key] != "":
        return env[env_key]
    if config_key in config:
        return config[config_key]
    return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def build_effective_config(
    config: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    project_root: Path,
) -> Dict[str, Any]:
    """Build the effective config with env overrides applied."""
    papers_dir_val = _env_or_config(env, config, "PAPERS_DIR", "papers_dir", project_root / "papers")
    papers_dir = Path(papers_dir_val)
    if not papers_dir.is_absolute():
        papers_dir = project_root / papers_dir

    effective = {
        "papers_dir": str(papers_dir),
        "max_papers": _coerce_int(_env_or_config(env, config, "MAX_PAPERS", "max_papers", 3), 3),
        "max_words": _coerce_int(_env_or_config(env, config, "MAX_WORDS", "max_words", 12000), 12000),
        "chunk_words": _coerce_int(_env_or_config(env, config, "CHUNK_WORDS", "chunk_words", 350), 350),
        "chunk_overlap": _coerce_int(_env_or_config(env, config, "CHUNK_OVERLAP", "chunk_overlap", 50), 50),
        "top_k": _coerce_int(_env_or_config(env, config, "TOP_K", "top_k", 6), 6),
        "batch_size": _coerce_int(_env_or_config(env, config, "EMBED_BATCH", "batch_size", 64), 64),
        "embedding_model": _env_or_config(env, config, "EMBEDDING_MODEL", "embedding_model", "text-embedding-3-small"),
        "chat_model": _env_or_config(env, config, "OPENAI_MODEL", "chat_model", None)
        or _env_or_config(env, config, "CHAT_MODEL", "chat_model", "gpt-5-nano"),
        "database_url": _env_or_config(env, config, "DATABASE_URL", "database_url", None),
        "bm25_weight": _coerce_float(_env_or_config(env, config, "BM25_WEIGHT", "bm25_weight", 0.5), 0.5),
        "reranker_model": _env_or_config(env, config, "RERANKER_MODEL", "reranker_model", ""),
        "rerank_top_n": _coerce_int(_env_or_config(env, config, "RERANK_TOP_N", "rerank_top_n", 30), 30),
        "query_expansion": _env_or_config(env, config, "QUERY_EXPANSION", "query_expansion", ""),
        "query_expand_model": _env_or_config(env, config, "QUERY_EXPAND_MODEL", "query_expand_model", ""),
        "section_aware_chunking": _coerce_bool(
            _env_or_config(env, config, "SECTION_AWARE_CHUNKING", "section_aware_chunking", False), False
        ),
        "force_ocr": _coerce_bool(_env_or_config(env, config, "FORCE_OCR", "force_ocr", False), False),
        "index_idempotent_skip": _coerce_bool(
            _env_or_config(env, config, "INDEX_IDEMPOTENT_SKIP", "index_idempotent_skip", True), True
        ),
        "allow_unverified_index": _coerce_bool(
            _env_or_config(env, config, "ALLOW_UNVERIFIED_INDEX", "allow_unverified_index", False), False
        ),
    }
    return effective


def apply_config_env_overrides(config: Mapping[str, Any], env: Mapping[str, str]) -> None:
    """Populate env vars from config when not already set.

    Boolean values only set the env var when True to avoid truthy "0"/"false" strings.
    """
    for env_key, config_key in ENV_CONFIG_MAP.items():
        if env_key in env and env[env_key] != "":
            continue
        if config_key not in config:
            continue
        value = config.get(config_key)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                os.environ[env_key] = "1"
            continue
        if isinstance(value, (int, float)):
            os.environ[env_key] = str(value)
            continue
        value_str = str(value).strip()
        if value_str == "":
            continue
        os.environ[env_key] = value_str

