"""Unit tests for provider routing runtime composition."""

from __future__ import annotations

import pytest

from ragonometrics.llm.errors import LLMCapabilityError, LLMConfigurationError
from ragonometrics.llm.interfaces import ProviderCapabilities
from ragonometrics.llm.runtime import ChatCapabilityRuntime, EmbeddingCapabilityRuntime
from ragonometrics.llm.router import ProviderSelection, resolve_provider_selection
from ragonometrics.llm.types import EmbeddingRequest, EmbeddingResponse, GenerateRequest, GenerateResponse


class _FakeChatProvider:
    """Simple in-memory chat provider for routing tests."""

    def __init__(self, *, name: str, supports_streaming: bool = True) -> None:
        self.name = name
        self.capabilities = ProviderCapabilities(
            supports_chat=True,
            supports_streaming=supports_streaming,
            supports_embeddings=False,
        )

    def generate(self, request: GenerateRequest, *, capability: str = "chat") -> GenerateResponse:
        return GenerateResponse(
            text=f"{self.name}:{request.user_input}",
            provider=self.name,
            capability=capability,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def stream(self, request: GenerateRequest, *, on_delta=None, capability: str = "stream_chat") -> GenerateResponse:
        if not self.capabilities.supports_streaming:
            raise LLMCapabilityError("streaming unsupported")
        text = f"{self.name}:{request.user_input}"
        if on_delta is not None:
            on_delta(text)
        return GenerateResponse(
            text=text,
            provider=self.name,
            capability=capability,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )


class _FakeEmbeddingProvider:
    """Simple in-memory embedding provider for routing tests."""

    def __init__(self, *, name: str, supports_embeddings: bool = True) -> None:
        self.name = name
        self.capabilities = ProviderCapabilities(
            supports_chat=False,
            supports_streaming=False,
            supports_embeddings=supports_embeddings,
        )

    def embed(self, request: EmbeddingRequest, *, capability: str = "embeddings") -> EmbeddingResponse:
        if not self.capabilities.supports_embeddings:
            raise LLMCapabilityError("embeddings unsupported")
        return EmbeddingResponse(
            embeddings=[[0.1, 0.2] for _ in request.texts],
            provider=self.name,
            capability=capability,
            input_tokens=1,
            output_tokens=0,
            total_tokens=1,
        )


def test_resolve_provider_selection_prefers_override() -> None:
    cfg = {
        "llm_provider": "openai",
        "embedding_provider": "openai_compatible",
        "embedding_provider_fallback": "openai",
    }
    sel = resolve_provider_selection(cfg, "embeddings")
    assert sel.primary_provider == "openai_compatible"
    assert sel.fallback_provider == "openai"


def test_resolve_provider_selection_falls_back_to_global() -> None:
    cfg = {"llm_provider": "openai_compatible"}
    sel = resolve_provider_selection(cfg, "query_expand")
    assert sel.primary_provider == "openai_compatible"
    assert sel.fallback_provider is None


def test_chat_runtime_stream_falls_back_when_primary_lacks_stream() -> None:
    primary = _FakeChatProvider(name="primary", supports_streaming=False)
    fallback = _FakeChatProvider(name="fallback", supports_streaming=True)
    runtime = ChatCapabilityRuntime(
        capability="stream_chat",
        model="m",
        selection=ProviderSelection(
            capability="stream_chat",
            primary_provider="primary",
            fallback_provider="fallback",
        ),
        primary_provider=primary,
        fallback_provider=fallback,
    )
    chunks = []
    response = runtime.stream(
        instructions="i",
        user_input="u",
        on_delta=lambda txt: chunks.append(txt),
    )
    assert response.provider == "fallback"
    assert response.fallback_from == "primary"
    assert chunks and chunks[-1] == "fallback:u"


def test_embedding_runtime_raises_without_supported_provider() -> None:
    primary = _FakeEmbeddingProvider(name="primary", supports_embeddings=False)
    runtime = EmbeddingCapabilityRuntime(
        capability="embeddings",
        model="e",
        selection=ProviderSelection(
            capability="embeddings",
            primary_provider="primary",
            fallback_provider=None,
        ),
        primary_provider=primary,
        fallback_provider=None,
    )
    with pytest.raises(LLMCapabilityError):
        runtime.embed(texts=["hello"])


def test_openai_compatible_requires_base_url() -> None:
    from ragonometrics.llm.runtime import build_llm_runtime

    with pytest.raises(LLMConfigurationError):
        build_llm_runtime({"llm_provider": "openai_compatible"})

