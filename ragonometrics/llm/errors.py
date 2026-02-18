"""Errors raised by the LLM provider abstraction layer."""

from __future__ import annotations


class LLMProviderError(RuntimeError):
    """Base error for provider-layer failures."""


class LLMConfigurationError(LLMProviderError):
    """Raised when provider configuration is invalid or incomplete."""


class LLMCapabilityError(LLMProviderError):
    """Raised when a selected provider cannot serve a requested capability."""

