"""LLM helpers used by active runtime flows (workflow, CLI, UI)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import re
import time

from ragonometrics.core.io_loaders import load_pdf, load_text_file
from ragonometrics.core.prompts import PIPELINE_CITATION_EXTRACT_INSTRUCTIONS
from ragonometrics.db.connection import connect
from ragonometrics.llm.providers.openai_provider import OpenAIProvider, _extract_text_from_openai_response
from ragonometrics.llm.runtime import build_llm_runtime


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
_MAX_TOKENS_ENV = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "").strip()
try:
    DEFAULT_MAX_OUTPUT_TOKENS = int(_MAX_TOKENS_ENV) if _MAX_TOKENS_ENV else 800
except ValueError:
    DEFAULT_MAX_OUTPUT_TOKENS = 800

_INVALID_LLM_TEXT_PATTERNS = (
    re.compile(r"^\s*ResponseTextConfig\(", re.IGNORECASE),
    re.compile(r"^\s*ResponseFormatText\(", re.IGNORECASE),
)


@dataclass(frozen=True)
class PaperText:
    """Container for full and segmented paper text."""

    text: str
    body_text: str
    references_text: str


def split_references(text: str) -> Tuple[str, str]:
    """Split text into body and references by heading heuristics.

    Args:
        text (str): Input text value.

    Returns:
        Tuple[str, str]: Tuple of result values produced by the operation.
    """
    pattern = re.compile(
        r"^\s*(references|bibliography|works cited)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return text, ""
    start = matches[-1].start()
    return text[:start], text[start:]


def tail_text(text: str, max_chars: int) -> str:
    """Return the last N characters of text.

    Args:
        text (str): Input text value.
        max_chars (int): Input value for max chars.

    Returns:
        str: Computed string result.
    """
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def load_paper_text(path: Path) -> PaperText:
    """Load a paper and split it into body and references sections.

    Args:
        path (Path): Filesystem path value.

    Returns:
        PaperText: Result produced by the operation.
    """
    if path.suffix.lower() == ".pdf":
        raw = load_pdf(path).text
    else:
        raw = load_text_file(path).text

    body, refs = split_references(raw)
    if not refs:
        refs = tail_text(raw, max_chars=40_000)

    return PaperText(text=raw, body_text=body, references_text=refs)


def build_client(api_key: Optional[str]) -> Any:
    """Build a chat client/provider using explicit or runtime config.

    Args:
        api_key (Optional[str]): Input value for api key.

    Returns:
        Any: Chat capability runtime/provider.
    """
    if api_key:
        return OpenAIProvider(api_key=api_key)
    runtime = build_llm_runtime({})
    return runtime.chat


def response_text(response: Any) -> str:
    """Extract text content from an OpenAI response object.

    Args:
        response (Any): Response payload returned by the upstream call.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    text = _extract_text_from_openai_response(response)
    text = str(text or "").strip()
    if text and not any(pattern.search(text) for pattern in _INVALID_LLM_TEXT_PATTERNS):
        return text
    raise ValueError("No text in OpenAI response.")


def call_llm(
    client: Any,
    *,
    model: str,
    instructions: str,
    user_input: str,
    max_output_tokens: Optional[int],
    temperature: Optional[float] = None,
    usage_context: str = "llm",
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step: Optional[str] = None,
    question_id: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Call a provider-routed LLM interface and return text.

    Args:
        client (Any): Provider client instance.
        model (str): Model name used for this operation.
        instructions (str): Input value for instructions.
        user_input (str): Input value for user input.
        max_output_tokens (Optional[int]): Input value for max output tokens.
        temperature (Optional[float]): Input value for temperature.
        usage_context (str): Input value for usage context.
        session_id (Optional[str]): Session identifier.
        request_id (Optional[str]): Request identifier.
        run_id (Optional[str]): Unique workflow run identifier.
        step (Optional[str]): Pipeline step name.
        question_id (Optional[str]): Structured question identifier.
        cache_hit (Optional[bool]): Whether the result was served from cache.
        meta (Optional[Dict[str, Any]]): Additional metadata dictionary.

    Returns:
        str: Computed string result.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    if max_output_tokens is not None and max_output_tokens < 16:
        max_output_tokens = 16
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))

    def _resolve_chat_endpoint(runtime_or_client: Any) -> Any:
        if not hasattr(runtime_or_client, "chat"):
            return runtime_or_client
        usage = (usage_context or "").strip().lower()
        if usage in {"query_expansion", "query_expand"} and hasattr(runtime_or_client, "query_expand_chat"):
            return runtime_or_client.query_expand_chat
        if usage in {"rerank", "agent_report_rerank"} and hasattr(runtime_or_client, "rerank_chat"):
            return runtime_or_client.rerank_chat
        if usage in {"metadata_title", "openalex_title"} and hasattr(runtime_or_client, "metadata_title_chat"):
            return runtime_or_client.metadata_title_chat
        return runtime_or_client.chat

    for attempt in range(max_retries + 1):
        try:
            t0 = time.perf_counter()
            provider_name = "unknown"
            provider_request_id: str | None = None
            input_tokens = output_tokens = total_tokens = 0
            fallback_from: str | None = None
            capability = usage_context

            endpoint = _resolve_chat_endpoint(client)

            if hasattr(endpoint, "generate"):
                resp = endpoint.generate(
                    instructions=instructions,
                    user_input=user_input,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    model=model,
                    metadata=meta or {},
                )
                text = str(getattr(resp, "text", "") or "").strip()
                provider_name = str(getattr(resp, "provider", "") or "unknown")
                capability = str(getattr(resp, "capability", "") or usage_context)
                provider_request_id = str(getattr(resp, "provider_request_id", "") or "") or None
                input_tokens = int(getattr(resp, "input_tokens", 0) or 0)
                output_tokens = int(getattr(resp, "output_tokens", 0) or 0)
                total_tokens = int(getattr(resp, "total_tokens", 0) or 0)
                fallback_from = str(getattr(resp, "fallback_from", "") or "") or None
            elif hasattr(endpoint, "chat") and hasattr(endpoint.chat, "generate"):
                resp = endpoint.chat.generate(
                    instructions=instructions,
                    user_input=user_input,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    model=model,
                    metadata=meta or {},
                )
                text = str(getattr(resp, "text", "") or "").strip()
                provider_name = str(getattr(resp, "provider", "") or "unknown")
                capability = str(getattr(resp, "capability", "") or usage_context)
                provider_request_id = str(getattr(resp, "provider_request_id", "") or "") or None
                input_tokens = int(getattr(resp, "input_tokens", 0) or 0)
                output_tokens = int(getattr(resp, "output_tokens", 0) or 0)
                total_tokens = int(getattr(resp, "total_tokens", 0) or 0)
                fallback_from = str(getattr(resp, "fallback_from", "") or "") or None
            elif hasattr(endpoint, "responses") and hasattr(endpoint.responses, "create"):
                payload = dict(
                    model=model,
                    instructions=instructions,
                    input=user_input,
                    max_output_tokens=max_output_tokens,
                )
                if temperature is not None:
                    payload["temperature"] = temperature
                resp = endpoint.responses.create(**payload)
                text = response_text(resp)
                provider_name = "openai"
                provider_request_id = str(getattr(resp, "id", "") or "") or None
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    if isinstance(usage, dict):
                        input_tokens = int(usage.get("input_tokens") or 0)
                        output_tokens = int(usage.get("output_tokens") or 0)
                        total_tokens = int(usage.get("total_tokens") or 0)
                    else:
                        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
            else:
                raise RuntimeError("Unsupported LLM client/provider interface")

            text = str(text or "").strip()
            if not text or any(pattern.search(text) for pattern in _INVALID_LLM_TEXT_PATTERNS):
                raise ValueError("No valid text in LLM response.")

            latency_ms = int(max(0.0, (time.perf_counter() - t0) * 1000.0))
            try:
                from ragonometrics.pipeline.token_usage import record_usage

                usage_meta = dict(meta or {})
                usage_meta.setdefault("provider", provider_name)
                usage_meta.setdefault("capability", capability)
                if fallback_from:
                    usage_meta.setdefault("fallback_from", fallback_from)
                record_usage(
                    model=model,
                    operation=usage_context,
                    step=step,
                    question_id=question_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    session_id=session_id,
                    request_id=request_id,
                    run_id=run_id,
                    provider_request_id=provider_request_id,
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    meta=usage_meta,
                )
            except Exception:
                pass
            return text
        except Exception as exc:
            if attempt >= max_retries:
                db_url = os.environ.get("DATABASE_URL")
                if db_url:
                    try:
                        from ragonometrics.indexing import metadata

                        conn = connect(db_url, require_migrated=True)
                        metadata.record_failure(
                            conn,
                            "llm",
                            str(exc),
                            {"model": model, "max_output_tokens": max_output_tokens},
                        )
                        conn.close()
                    except Exception:
                        pass
                raise
            try:
                import time

                time.sleep(0.5 * (attempt + 1))
            except Exception:
                pass


def call_openai(
    client: Any,
    *,
    model: str,
    instructions: str,
    user_input: str,
    max_output_tokens: Optional[int],
    temperature: Optional[float] = None,
    usage_context: str = "llm",
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step: Optional[str] = None,
    question_id: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Backward-compatible alias for provider-routed LLM text generation."""
    return call_llm(
        client,
        model=model,
        instructions=instructions,
        user_input=user_input,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        usage_context=usage_context,
        session_id=session_id,
        request_id=request_id,
        run_id=run_id,
        step=step,
        question_id=question_id,
        cache_hit=cache_hit,
        meta=meta,
    )


def extract_json(text: str) -> Any:
    """Extract JSON from a string, tolerating markdown fences.

    Args:
        text (str): Input text value.

    Returns:
        Any: Return value produced by the operation.
    """
    candidate = text.strip()
    if "```" in candidate:
        blocks = re.findall(r"```(?:json)?\s*(.*?)```", candidate, flags=re.DOTALL)
        if blocks:
            candidate = blocks[0].strip()

    start = min([i for i in [candidate.find("["), candidate.find("{")] if i != -1], default=-1)
    if start >= 0:
        candidate = candidate[start:]

    end_bracket = candidate.rfind("]")
    end_brace = candidate.rfind("}")
    end = max(end_bracket, end_brace)
    if end >= 0:
        candidate = candidate[: end + 1]

    return json.loads(candidate)


def extract_citations(
    *,
    paper_path: Path,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> List[Dict[str, Any]]:
    """Extract citation metadata from a paper using OpenAI.

    Args:
        paper_path (Path): Path to a single paper file.
        model (str): Model name used for this operation.
        api_key (Optional[str]): Input value for api key.
        max_output_tokens (int): Input value for max output tokens.

    Returns:
        List[Dict[str, Any]]: Dictionary containing the computed result payload.

    Raises:
        Exception: If an unexpected runtime error occurs.
    """
    paper = load_paper_text(paper_path)
    client = build_client(api_key)

    prompt = "Extract all references from the text below.\n\n" + paper.references_text
    raw = call_llm(
        client,
        model=model,
        instructions=PIPELINE_CITATION_EXTRACT_INSTRUCTIONS,
        user_input=prompt,
        max_output_tokens=max_output_tokens,
    )
    try:
        data = extract_json(raw)
        if not isinstance(data, list):
            raise ValueError("Citations output is not a list.")
        return data
    except Exception:
        retry_instructions = (
            PIPELINE_CITATION_EXTRACT_INSTRUCTIONS
            + " If you are unsure, return an empty JSON list: []."
        )
        raw_retry = call_llm(
            client,
            model=model,
            instructions=retry_instructions,
            user_input=prompt,
            max_output_tokens=max_output_tokens,
        )
        try:
            data = extract_json(raw_retry)
            if not isinstance(data, list):
                raise ValueError("Citations output is not a list.")
            return data
        except Exception:
            return []
