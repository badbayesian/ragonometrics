"""Provider routing logic for per-capability LLM selection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ragonometrics.llm.errors import LLMConfigurationError
from ragonometrics.llm.providers import AnthropicProvider, OpenAICompatibleProvider, OpenAIProvider


@dataclass(frozen=True)
class ProviderSelection:
    """Resolved provider keys for one capability."""

    capability: str
    primary_provider: str
    fallback_provider: Optional[str] = None


def _cfg_value(settings: Any, key: str, default: Optional[str] = None) -> Optional[str]:
    """Read one normalized string setting from object/dict/env sources."""
    if settings is None:
        value = None
    elif isinstance(settings, dict):
        value = settings.get(key)
    else:
        value = getattr(settings, key, None)
    text = str(value or "").strip()
    if text:
        return text
    env_key = key.upper()
    env_val = os.environ.get(env_key)
    if env_val and env_val.strip():
        return env_val.strip()
    return default


def resolve_provider_selection(settings: Any, capability: str) -> ProviderSelection:
    """Resolve primary/fallback providers for one capability."""
    capability_map: Dict[str, str] = {
        "chat": "chat_provider",
        "stream_chat": "chat_provider",
        "embeddings": "embedding_provider",
        "rerank": "rerank_provider",
        "query_expand": "query_expand_provider",
        "metadata_title": "metadata_title_provider",
    }
    if capability not in capability_map:
        raise LLMConfigurationError(f"Unknown capability '{capability}'")
    override_key = capability_map[capability]
    global_provider = _cfg_value(settings, "llm_provider", default="openai") or "openai"
    primary = _cfg_value(settings, override_key, default=global_provider) or global_provider
    fallback = _cfg_value(settings, f"{override_key}_fallback", default=None)
    if fallback and fallback == primary:
        fallback = None
    return ProviderSelection(capability=capability, primary_provider=primary, fallback_provider=fallback)


def build_provider_registry(settings: Any) -> Dict[str, Any]:
    """Instantiate configured providers keyed by provider id."""
    registry: Dict[str, Any] = {}

    def _register(key: str) -> None:
        provider_id = str(key or "").strip().lower()
        if not provider_id or provider_id in registry:
            return
        if provider_id == "openai":
            registry[provider_id] = OpenAIProvider(
                api_key=_cfg_value(settings, "openai_api_key", default=os.environ.get("OPENAI_API_KEY")),
            )
            return
        if provider_id == "openai_compatible":
            base_url = _cfg_value(settings, "llm_base_url", default=os.environ.get("LLM_BASE_URL"))
            if not base_url:
                raise LLMConfigurationError("openai_compatible provider requires llm_base_url or LLM_BASE_URL")
            compat_key = _cfg_value(
                settings,
                "openai_compatible_api_key",
                default=_cfg_value(settings, "llm_api_key", default=os.environ.get("LLM_API_KEY")),
            )
            registry[provider_id] = OpenAICompatibleProvider(base_url=base_url, api_key=compat_key)
            return
        if provider_id == "anthropic":
            registry[provider_id] = AnthropicProvider(
                api_key=_cfg_value(settings, "anthropic_api_key", default=os.environ.get("ANTHROPIC_API_KEY"))
            )
            return
        raise LLMConfigurationError(
            f"Unsupported llm provider '{provider_id}'. Supported: openai, anthropic, openai_compatible"
        )

    for cap in ("chat", "stream_chat", "embeddings", "rerank", "query_expand", "metadata_title"):
        selection = resolve_provider_selection(settings, cap)
        _register(selection.primary_provider)
        if selection.fallback_provider:
            _register(selection.fallback_provider)

    return registry

