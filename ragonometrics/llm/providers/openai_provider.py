"""OpenAI-backed provider implementations for chat and embeddings."""

from __future__ import annotations

from typing import Any, Callable, Optional

from openai import OpenAI

from ragonometrics.llm.errors import LLMCapabilityError
from ragonometrics.llm.interfaces import ProviderCapabilities
from ragonometrics.llm.types import EmbeddingRequest, EmbeddingResponse, GenerateRequest, GenerateResponse


def _extract_text_from_openai_response(response: Any) -> str:
    """Extract text payload from an OpenAI Responses API object."""
    output_text = getattr(response, "output_text", None) or getattr(response, "text", None)
    if output_text:
        return str(output_text).strip()
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "output_text":
                text = getattr(content, "text", None)
                if text:
                    parts.append(str(text))
    return "\n".join(parts).strip()


def _usage_counts(usage: Any) -> tuple[int, int, int]:
    """Normalize token counts from provider usage payloads."""
    if usage is None:
        return 0, 0, 0
    if isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0)
    else:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


class OpenAIProvider:
    """Provider adapter for OpenAI Responses + Embeddings APIs."""

    name = "openai"
    capabilities = ProviderCapabilities(
        supports_chat=True,
        supports_streaming=True,
        supports_embeddings=True,
    )

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        client: Optional[OpenAI] = None,
        provider_name: Optional[str] = None,
    ) -> None:
        """Initialize provider with optional explicit API key/base URL."""
        self.name = str(provider_name or self.name)
        self._client = client or OpenAI(api_key=api_key, base_url=base_url)

    def generate(self, request: GenerateRequest, *, capability: str = "chat") -> GenerateResponse:
        """Generate text using non-streaming Responses API."""
        payload: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": request.user_input,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        response = self._client.responses.create(**payload)
        text = _extract_text_from_openai_response(response)
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
        """Stream text deltas using OpenAI Responses streaming API."""
        payload: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": request.user_input,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        streamed = ""
        final_resp = None
        with self._client.responses.stream(**payload) as stream:
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if not delta:
                        continue
                    streamed += delta
                    if on_delta is not None:
                        on_delta(streamed)
            final_resp = stream.get_final_response()

        final_text = _extract_text_from_openai_response(final_resp) if final_resp is not None else ""
        if final_text and len(final_text) > len(streamed):
            streamed = final_text
            if on_delta is not None:
                on_delta(streamed)
        text = (streamed or final_text).strip()
        in_tok, out_tok, total_tok = _usage_counts(getattr(final_resp, "usage", None) if final_resp is not None else None)
        return GenerateResponse(
            text=text,
            provider=self.name,
            capability=capability,
            provider_request_id=str(getattr(final_resp, "id", "") or "") or None if final_resp is not None else None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok,
            raw=final_resp,
        )

    def embed(self, request: EmbeddingRequest, *, capability: str = "embeddings") -> EmbeddingResponse:
        """Generate embeddings for input texts."""
        response = self._client.embeddings.create(model=request.model, input=request.texts)
        embeddings = [list(item.embedding) for item in (getattr(response, "data", None) or [])]
        in_tok, out_tok, total_tok = _usage_counts(getattr(response, "usage", None))
        return EmbeddingResponse(
            embeddings=embeddings,
            provider=self.name,
            capability=capability,
            provider_request_id=str(getattr(response, "id", "") or "") or None,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok,
            raw=response,
        )


class OpenAICompatibleProvider(OpenAIProvider):
    """OpenAI-compatible adapter (local/self-hosted endpoint)."""

    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: Optional[str] = None) -> None:
        """Initialize an OpenAI-compatible provider.

        Args:
            base_url (str): OpenAI-compatible endpoint base URL.
            api_key (Optional[str]): Optional API key/token.
        """
        if not str(base_url or "").strip():
            raise LLMCapabilityError("openai_compatible provider requires a non-empty base_url")
        super().__init__(api_key=api_key, base_url=base_url, provider_name=self.name)

