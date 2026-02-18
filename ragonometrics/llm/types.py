"""Provider-agnostic request and response datatypes for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GenerateRequest:
    """Structured text-generation request."""

    model: str
    instructions: str
    user_input: str
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerateResponse:
    """Normalized text-generation response."""

    text: str
    provider: str
    capability: str
    provider_request_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    fallback_from: Optional[str] = None
    raw: Any = None


@dataclass(frozen=True)
class StreamChunk:
    """One streamed text chunk."""

    delta: str


@dataclass(frozen=True)
class EmbeddingRequest:
    """Structured embeddings request."""

    model: str
    texts: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingResponse:
    """Normalized embeddings response."""

    embeddings: List[List[float]]
    provider: str
    capability: str
    provider_request_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    fallback_from: Optional[str] = None
    raw: Any = None

