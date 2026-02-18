"""Composed runtime for routing LLM capabilities across providers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ragonometrics.llm.errors import LLMCapabilityError
from ragonometrics.llm.interfaces import ChatProvider, EmbeddingProvider
from ragonometrics.llm.router import ProviderSelection, _cfg_value, build_provider_registry, resolve_provider_selection
from ragonometrics.llm.types import EmbeddingRequest, EmbeddingResponse, GenerateRequest, GenerateResponse


def _is_truthy(value: Any) -> bool:
    """Normalize truthy config values."""
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


@dataclass
class ChatCapabilityRuntime:
    """Bound runtime object for one chat-like capability."""

    capability: str
    model: str
    selection: ProviderSelection
    primary_provider: ChatProvider
    fallback_provider: Optional[ChatProvider] = None

    def _ensure_chat_supported(self, provider: ChatProvider) -> None:
        if not bool(getattr(provider, "capabilities", None) and provider.capabilities.supports_chat):
            raise LLMCapabilityError(f"Provider '{provider.name}' does not support chat capability '{self.capability}'")

    def _ensure_stream_supported(self, provider: ChatProvider) -> None:
        if not bool(getattr(provider, "capabilities", None) and provider.capabilities.supports_streaming):
            raise LLMCapabilityError(
                f"Provider '{provider.name}' does not support streaming capability '{self.capability}'"
            )

    def generate(
        self,
        *,
        instructions: str,
        user_input: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> GenerateResponse:
        """Execute one non-streaming generation request with fallback routing."""
        req = GenerateRequest(
            model=str(model or self.model),
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            metadata=metadata or {},
        )
        try:
            self._ensure_chat_supported(self.primary_provider)
            return self.primary_provider.generate(req, capability=self.capability)
        except LLMCapabilityError:
            if self.fallback_provider is None:
                raise
            self._ensure_chat_supported(self.fallback_provider)
            resp = self.fallback_provider.generate(req, capability=self.capability)
            return GenerateResponse(
                text=resp.text,
                provider=resp.provider,
                capability=resp.capability,
                provider_request_id=resp.provider_request_id,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                total_tokens=resp.total_tokens,
                fallback_from=getattr(self.primary_provider, "name", self.selection.primary_provider),
                raw=resp.raw,
            )

    def stream(
        self,
        *,
        instructions: str,
        user_input: str,
        on_delta: Optional[Callable[[str], None]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> GenerateResponse:
        """Execute one streaming generation request with fallback routing."""
        req = GenerateRequest(
            model=str(model or self.model),
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            metadata=metadata or {},
        )
        try:
            self._ensure_stream_supported(self.primary_provider)
            return self.primary_provider.stream(req, on_delta=on_delta, capability=self.capability)
        except LLMCapabilityError:
            if self.fallback_provider is not None:
                self._ensure_stream_supported(self.fallback_provider)
                resp = self.fallback_provider.stream(req, on_delta=on_delta, capability=self.capability)
                return GenerateResponse(
                    text=resp.text,
                    provider=resp.provider,
                    capability=resp.capability,
                    provider_request_id=resp.provider_request_id,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    total_tokens=resp.total_tokens,
                    fallback_from=getattr(self.primary_provider, "name", self.selection.primary_provider),
                    raw=resp.raw,
                )
            # Last resort: non-streaming generate with one-shot delta emission.
            resp = self.generate(
                instructions=instructions,
                user_input=user_input,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                model=model,
                metadata=metadata,
            )
            if on_delta is not None and resp.text:
                on_delta(resp.text)
            return resp


@dataclass
class EmbeddingCapabilityRuntime:
    """Bound runtime object for the embeddings capability."""

    capability: str
    model: str
    selection: ProviderSelection
    primary_provider: EmbeddingProvider
    fallback_provider: Optional[EmbeddingProvider] = None

    def _ensure_embeddings_supported(self, provider: EmbeddingProvider) -> None:
        if not bool(getattr(provider, "capabilities", None) and provider.capabilities.supports_embeddings):
            raise LLMCapabilityError(
                f"Provider '{provider.name}' does not support embeddings capability '{self.capability}'"
            )

    def embed(
        self,
        *,
        texts: list[str],
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> EmbeddingResponse:
        """Execute one embeddings request with fallback routing."""
        req = EmbeddingRequest(model=str(model or self.model), texts=texts, metadata=metadata or {})
        try:
            self._ensure_embeddings_supported(self.primary_provider)
            return self.primary_provider.embed(req, capability=self.capability)
        except LLMCapabilityError:
            if self.fallback_provider is None:
                raise
            self._ensure_embeddings_supported(self.fallback_provider)
            resp = self.fallback_provider.embed(req, capability=self.capability)
            return EmbeddingResponse(
                embeddings=resp.embeddings,
                provider=resp.provider,
                capability=resp.capability,
                provider_request_id=resp.provider_request_id,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                total_tokens=resp.total_tokens,
                fallback_from=getattr(self.primary_provider, "name", self.selection.primary_provider),
                raw=resp.raw,
            )


@dataclass
class LLMRuntime:
    """Composed capability runtimes with deterministic provider routing."""

    chat: ChatCapabilityRuntime
    embeddings: EmbeddingCapabilityRuntime
    rerank_chat: ChatCapabilityRuntime
    query_expand_chat: ChatCapabilityRuntime
    metadata_title_chat: ChatCapabilityRuntime


def _resolve_model(settings: Any, key: str, default: str) -> str:
    """Resolve one model name from settings/env with fallback."""
    value = _cfg_value(settings, key, default=None)
    if value:
        return value
    env_key = key.upper()
    env_val = os.environ.get(env_key)
    if env_val and env_val.strip():
        return env_val.strip()
    return default


def build_llm_runtime(settings: Any) -> LLMRuntime:
    """Build composed runtime from settings/env provider configuration."""
    registry = build_provider_registry(settings)

    global_chat_model = _resolve_model(settings, "chat_model", "gpt-5-nano")
    embedding_model = _resolve_model(settings, "embedding_model", "text-embedding-3-small")
    rerank_model = _resolve_model(settings, "reranker_model", global_chat_model)
    query_expand_model = _resolve_model(settings, "query_expand_model", global_chat_model)
    metadata_title_model = _resolve_model(settings, "metadata_title_model", global_chat_model)

    if not _is_truthy(_cfg_value(settings, "query_expansion", default=os.environ.get("QUERY_EXPANSION", ""))):
        query_expand_model = global_chat_model

    def _chat_runtime(capability: str, model: str) -> ChatCapabilityRuntime:
        selection = resolve_provider_selection(settings, capability)
        primary = registry[selection.primary_provider]
        fallback = registry.get(selection.fallback_provider) if selection.fallback_provider else None
        return ChatCapabilityRuntime(
            capability=capability,
            model=model,
            selection=selection,
            primary_provider=primary,
            fallback_provider=fallback,
        )

    def _embedding_runtime(capability: str, model: str) -> EmbeddingCapabilityRuntime:
        selection = resolve_provider_selection(settings, capability)
        primary = registry[selection.primary_provider]
        fallback = registry.get(selection.fallback_provider) if selection.fallback_provider else None
        return EmbeddingCapabilityRuntime(
            capability=capability,
            model=model,
            selection=selection,
            primary_provider=primary,
            fallback_provider=fallback,
        )

    return LLMRuntime(
        chat=_chat_runtime("chat", global_chat_model),
        embeddings=_embedding_runtime("embeddings", embedding_model),
        rerank_chat=_chat_runtime("rerank", rerank_model),
        query_expand_chat=_chat_runtime("query_expand", query_expand_model),
        metadata_title_chat=_chat_runtime("metadata_title", metadata_title_model),
    )

