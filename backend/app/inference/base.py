"""Provider abstraction for text generation.

Transport/API contracts never depend on a specific provider. The beta uses
only ``LocalOllamaProvider``; cloud adapters exist but are hard-disabled.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ProviderError(RuntimeError):
    code = "provider_error"
    user_message = "Yerel dil modeli şu anda yanıt veremedi. Lütfen biraz sonra tekrar deneyin."


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
    user_message = (
        "Yerel Ollama servisine ulaşılamadı (127.0.0.1:11434). Ollama uygulamasının çalıştığını kontrol edin "
        "(Başlat menüsü > Ollama) ve tekrar deneyin."
    )


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    user_message = (
        "Yerel model zaman aşımına uğradı. Dizüstü bilgisayar CPU ile çalıştığı için uzun yanıtlar yavaş olabilir; "
        "soruyu kısaltıp tekrar deneyin."
    )


class ProviderModelMissingError(ProviderError):
    code = "model_missing"
    user_message = (
        "Yapılandırılan model Ollama'da bulunamadı. 'ollama pull <OLLAMA_MODEL>' komutuyla modeli indirin."
    )


class ProviderDisabledError(ProviderError):
    code = "provider_disabled"
    user_message = "Bu sağlayıcı beta sürümde devre dışıdır; yalnızca yerel Ollama kullanılır."


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class GenerationOptions:
    temperature: float = 0.2
    num_predict: int | None = None
    json_mode: bool = False
    stop: list[str] | None = None


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealth:
    name: str
    reachable: bool
    model: str | None
    model_available: bool
    version: str | None = None
    detail: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    enabled: bool
    model: str

    def chat(self, messages: list[ChatMessage], options: GenerationOptions | None = None) -> LLMResult: ...

    def stream_chat(
        self, messages: list[ChatMessage], options: GenerationOptions | None = None
    ) -> Iterator[str]: ...

    def health(self) -> ProviderHealth: ...
