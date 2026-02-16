"""LLM helpers used by active runtime flows (workflow, CLI, UI)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import re
import time

from openai import OpenAI

from ragonometrics.core.io_loaders import load_pdf, load_text_file
from ragonometrics.core.prompts import PIPELINE_CITATION_EXTRACT_INSTRUCTIONS


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
_MAX_TOKENS_ENV = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "").strip()
try:
    DEFAULT_MAX_OUTPUT_TOKENS = int(_MAX_TOKENS_ENV) if _MAX_TOKENS_ENV else 800
except ValueError:
    DEFAULT_MAX_OUTPUT_TOKENS = 800


@dataclass(frozen=True)
class PaperText:
    """Container for full and segmented paper text."""

    text: str
    body_text: str
    references_text: str


def split_references(text: str) -> Tuple[str, str]:
    """Split text into body and references by heading heuristics."""
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
    """Return the last N characters of text."""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def load_paper_text(path: Path) -> PaperText:
    """Load a paper and split it into body and references sections."""
    if path.suffix.lower() == ".pdf":
        raw = load_pdf(path).text
    else:
        raw = load_text_file(path).text

    body, refs = split_references(raw)
    if not refs:
        refs = tail_text(raw, max_chars=40_000)

    return PaperText(text=raw, body_text=body, references_text=refs)


def build_client(api_key: Optional[str]) -> OpenAI:
    """Build an OpenAI client using explicit or env API key."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set.")
    return OpenAI(api_key=key)


def response_text(response: Any) -> str:
    """Extract text content from an OpenAI response object."""
    parts: List[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "output_text":
                parts.append(content.text)
    if parts:
        return "\n".join(parts).strip()

    fallback = getattr(response, "output_text", None) or getattr(response, "text", None)
    if fallback:
        return str(fallback).strip()
    raise ValueError("No text in OpenAI response.")


def call_openai(
    client: OpenAI,
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
    """Call OpenAI Responses API and return text."""
    if max_output_tokens is not None and max_output_tokens < 16:
        max_output_tokens = 16
    max_retries = int(os.environ.get("OPENAI_MAX_RETRIES", "2"))
    for attempt in range(max_retries + 1):
        try:
            payload = dict(
                model=model,
                instructions=instructions,
                input=user_input,
                max_output_tokens=max_output_tokens,
            )
            if temperature is not None:
                payload["temperature"] = temperature
            t0 = time.perf_counter()
            resp = client.responses.create(**payload)
            latency_ms = int(max(0.0, (time.perf_counter() - t0) * 1000.0))
            try:
                from ragonometrics.pipeline.token_usage import record_usage

                usage = getattr(resp, "usage", None)
                input_tokens = output_tokens = total_tokens = 0
                if usage is not None:
                    if isinstance(usage, dict):
                        input_tokens = int(usage.get("input_tokens") or 0)
                        output_tokens = int(usage.get("output_tokens") or 0)
                        total_tokens = int(usage.get("total_tokens") or 0)
                    else:
                        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
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
                    provider_request_id=str(getattr(resp, "id", "") or ""),
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    meta=meta,
                )
            except Exception:
                pass
            return response_text(resp)
        except Exception as exc:
            if attempt >= max_retries:
                db_url = os.environ.get("DATABASE_URL")
                if db_url:
                    try:
                        import psycopg2
                        from ragonometrics.indexing import metadata

                        conn = psycopg2.connect(db_url)
                        metadata.record_failure(
                            conn,
                            "openai",
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


def extract_json(text: str) -> Any:
    """Extract JSON from a string, tolerating markdown fences."""
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
    """Extract citation metadata from a paper using OpenAI."""
    paper = load_paper_text(paper_path)
    client = build_client(api_key)

    prompt = "Extract all references from the text below.\n\n" + paper.references_text
    raw = call_openai(
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
        raw_retry = call_openai(
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
