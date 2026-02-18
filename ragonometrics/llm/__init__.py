"""Composable LLM provider runtime and adapter interfaces."""

from ragonometrics.llm.errors import LLMCapabilityError, LLMConfigurationError, LLMProviderError
from ragonometrics.llm.interfaces import ChatProvider, EmbeddingProvider, ProviderCapabilities
from ragonometrics.llm.runtime import LLMRuntime, build_llm_runtime
from ragonometrics.llm.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerateRequest,
    GenerateResponse,
    StreamChunk,
)

__all__ = [
    "LLMProviderError",
    "LLMConfigurationError",
    "LLMCapabilityError",
    "ProviderCapabilities",
    "ChatProvider",
    "EmbeddingProvider",
    "GenerateRequest",
    "GenerateResponse",
    "StreamChunk",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "LLMRuntime",
    "build_llm_runtime",
]

