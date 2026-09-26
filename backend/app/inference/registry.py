"""Provider registry. Beta: only LocalOllamaProvider is ever active."""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.inference.base import LLMProvider
from app.inference.disabled import ClaudeProvider, OpenAIProvider
from app.inference.ollama import LocalOllamaProvider

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = LocalOllamaProvider(get_settings())
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Test hook: inject a deterministic provider."""
    global _provider
    _provider = provider


def provider_status(settings: Settings) -> dict:
    openai = OpenAIProvider(configured=bool(settings.openai_api_key.get_secret_value()))
    claude = ClaudeProvider(configured=bool(settings.anthropic_api_key.get_secret_value()))
    return {
        "active_provider": "local_ollama",
        "providers": [
            {
                "name": "local_ollama",
                "enabled": True,
                "active": True,
                "endpoint": settings.ollama_base_url,
                "model": settings.ollama_model,
                "configured": True,
            },
            {"name": openai.name, "enabled": False, "active": False, "configured": openai.configured,
             "note": "Beta sürümde devre dışı; çağrı yapılmaz."},
            {"name": claude.name, "enabled": False, "active": False, "configured": claude.configured,
             "note": "Beta sürümde devre dışı; çağrı yapılmaz."},
        ],
        "online_calls_allowed": False,
    }
