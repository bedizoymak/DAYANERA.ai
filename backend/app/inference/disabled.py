"""Future cloud provider adapters - designed but HARD-DISABLED in beta.

These classes intentionally contain no network code and import no vendor
SDK. Any generation call raises ``ProviderDisabledError``. Enabling them is
a future, explicitly approved phase (see README "Future providers").
"""
from __future__ import annotations

from collections.abc import Iterator

from app.inference.base import (
    ChatMessage,
    GenerationOptions,
    LLMResult,
    ProviderDisabledError,
    ProviderHealth,
)


class _DisabledProvider:
    name = "disabled"
    enabled = False
    model = ""

    def __init__(self, configured: bool = False):
        # "configured" only reports whether a key placeholder is filled in;
        # the key itself is never read here, stored or returned.
        self.configured = configured

    def chat(self, messages: list[ChatMessage], options: GenerationOptions | None = None) -> LLMResult:
        raise ProviderDisabledError(f"{self.name} beta sürümde devre dışı.")

    def stream_chat(self, messages: list[ChatMessage], options: GenerationOptions | None = None) -> Iterator[str]:
        raise ProviderDisabledError(f"{self.name} beta sürümde devre dışı.")

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name, reachable=False, model=None, model_available=False, detail="Beta sürümde devre dışı."
        )


class OpenAIProvider(_DisabledProvider):
    name = "openai"


class ClaudeProvider(_DisabledProvider):
    name = "anthropic_claude"
