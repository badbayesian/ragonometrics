"""Regression tests for OpenAI response text extraction."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragonometrics.llm.providers.openai_provider import _extract_text_from_openai_response
from ragonometrics.pipeline.pipeline import response_text


class _ResponseTextConfigObject:
    """Simulate SDK config object stringification in a bad fallback path."""

    def __str__(self) -> str:
        return "ResponseTextConfig(format=ResponseFormatText(type='text'), verbosity='medium')"


def test_extract_text_rejects_config_like_text_fallback() -> None:
    resp = SimpleNamespace(output=[], output_text=None, text=_ResponseTextConfigObject())
    assert _extract_text_from_openai_response(resp) == ""


def test_extract_text_prefers_output_message_text() -> None:
    resp = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Valid answer text")],
            )
        ],
        output_text=None,
        text=_ResponseTextConfigObject(),
    )
    assert _extract_text_from_openai_response(resp) == "Valid answer text"


def test_response_text_raises_when_only_config_like_payload_exists() -> None:
    resp = SimpleNamespace(output=[], output_text=None, text=_ResponseTextConfigObject())
    with pytest.raises(ValueError):
        _ = response_text(resp)
