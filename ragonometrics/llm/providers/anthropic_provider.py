"""Anthropic-backed chat provider implementation."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ragonometrics.llm.errors import LLMCapabilityError, LLMConfigurationError
from ragonometrics.llm.interfaces import ProviderCapabilities
from ragonometrics.llm.types import EmbeddingRequest, EmbeddingResponse, GenerateRequest, GenerateResponse


def _usage_counts(usage: Any) -> tuple[int, int, int]:
    """Normalize Anthropics token usage payload into input/output/total."""
    if usage is None:
        return 0, 0, 0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


class AnthropicProvider:
    """Provider adapter for Anthropic Messages API (chat only)."""

    name = "anthropic"
    capabilities = ProviderCapabilities(
        supports_chat=True,
        supports_streaming=True,
        supports_embeddings=False,
    )

    def __init__(self, *, api_key: Optional[str] = None) -> None:
        """Initialize Anthropic client with API key."""
        try:
            from anthropic import Anthropic
        except Exception as exc:
            raise LLMConfigurationError("anthropic package is required for provider=anthropic") from exc
        key = str(api_key or "").strip()
        if not key:
            raise LLMConfigurationError("anthropic provider requires ANTHROPIC_API_KEY")
        self._client = Anthropic(api_key=key)

    def _payload(self, request: GenerateRequest) -> dict[str, Any]:
        """Internal helper for payload."""
        max_tokens = int(request.max_output_tokens or 1024)
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": max_tokens,
            "system": request.instructions,
            "messages": [{"role": "user", "content": request.user_input}],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return payload

    def generate(self, request: GenerateRequest, *, capability: str = "chat") -> GenerateResponse:
        """Generate text via Anthropic Messages API."""
        response = self._client.messages.create(**self._payload(request))
        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                val = getattr(block, "text", None)
                if val:
                    text_parts.append(str(val))
        text = "\n".join(text_parts).strip()
        in_tok, out_tok, total_tok = _usage_counts(getattr(response, "usage", None))
        return GenerateResponse(
            text=text,
            provider=self.name,
            capability=capability,
            provider_request_id=str(getattr(response, "id", "") or "") or None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok,
            raw=response,
        )

    def stream(
        self,
        request: GenerateRequest,
        *,
        on_delta: Optional[Callable[[str], None]] = None,
        capability: str = "stream_chat",
    ) -> GenerateResponse:
        """Stream text using Anthropic streaming API.

        The implementation degrades to non-streaming generation if streaming
        events are unavailable for the configured client/version.
        """
        streamed = ""
        response = None
        try:
            with self._client.messages.stream(**self._payload(request)) as stream:
                for event in stream:
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    text = getattr(delta, "text", None)
                    if not text:
                        continue
                    streamed += str(text)
                    if on_delta is not None:
                        on_delta(streamed)
                response = stream.get_final_message()
        except Exception:
            # Fallback to non-streaming path for compatibility.
            gen = self.generate(request, capability=capability)
            if on_delta is not None and gen.text:
                on_delta(gen.text)
            return gen
        text = streamed.strip()
        if response is not None and not text:
            text_parts: list[str] = []
            for block in getattr(response, "content", []) or []:
                if getattr(block, "type", None) == "text":
                    val = getattr(block, "text", None)
                    if val:
                        text_parts.append(str(val))
            text = "\n".join(text_parts).strip()
        in_tok, out_tok, total_tok = _usage_counts(getattr(response, "usage", None))
        return GenerateResponse(
            text=text,
            provider=self.name,
            capability=capability,
            provider_request_id=str(getattr(response, "id", "") or "") or None if response is not None else None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok,
            raw=response,
        )

    def embed(self, request: EmbeddingRequest, *, capability: str = "embeddings") -> EmbeddingResponse:
        """Raise capability error because Anthropic does not expose embeddings."""
        _ = (request, capability)
        raise LLMCapabilityError("anthropic provider does not support embeddings")

