"""Protocols and capability descriptors for provider composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .types import EmbeddingRequest, EmbeddingResponse, GenerateRequest, GenerateResponse


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capability flags for an LLM provider implementation."""

    supports_chat: bool = True
    supports_streaming: bool = False
    supports_embeddings: bool = False


class ChatProvider(Protocol):
    """Protocol for chat/text-generation providers."""

    name: str
    capabilities: ProviderCapabilities

    def generate(self, request: GenerateRequest, *, capability: str = "chat") -> GenerateResponse:
        """Run one non-streaming text-generation request."""

    def stream(
        self,
        request: GenerateRequest,
        *,
        on_delta: Optional[Callable[[str], None]] = None,
        capability: str = "stream_chat",
    ) -> GenerateResponse:
        """Run one streaming text-generation request."""


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    name: str
    capabilities: ProviderCapabilities

    def embed(self, request: EmbeddingRequest, *, capability: str = "embeddings") -> EmbeddingResponse:
        """Run one embeddings request."""

