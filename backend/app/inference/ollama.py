"""LocalOllamaProvider: calls the native Windows Ollama service on localhost."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx

from app.core.config import LOOPBACK_HOSTS, Settings
from app.inference.base import (
    ChatMessage,
    GenerationOptions,
    LLMResult,
    ProviderError,
    ProviderHealth,
    ProviderModelMissingError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

log = logging.getLogger(__name__)


class LocalOllamaProvider:
    name = "local_ollama"
    enabled = True

    def __init__(self, settings: Settings):
        host = urlparse(settings.ollama_base_url).hostname
        if host not in LOOPBACK_HOSTS:  # defence in depth; Settings already validates
            raise ProviderError("Ollama yalnızca localhost üzerinden kullanılabilir.")
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.num_ctx = settings.ollama_num_ctx
        self.num_predict = settings.ollama_num_predict
        self.keep_alive = settings.ollama_keep_alive
        self.timeout = httpx.Timeout(settings.ollama_request_timeout_seconds, connect=5.0)

    # trust_env=False: never route localhost inference through system proxies
    def _client(self, timeout: httpx.Timeout | None = None) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout or self.timeout, trust_env=False)

    def _payload(self, messages: list[ChatMessage], options: GenerationOptions, stream: bool) -> dict:
        opts: dict = {
            "temperature": options.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": options.num_predict or self.num_predict,
        }
        if options.stop:
            opts["stop"] = options.stop
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "options": opts,
            "keep_alive": self.keep_alive,
        }
        if options.json_mode:
            payload["format"] = "json"
        return payload

    @staticmethod
    def _raise_for(exc: Exception) -> None:
        if isinstance(exc, httpx.ConnectError):
            raise ProviderUnavailableError(str(exc)) from exc
        if isinstance(exc, httpx.TimeoutException):
            raise ProviderTimeoutError(str(exc)) from exc
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code == 404:
                raise ProviderModelMissingError(exc.response.text[:200]) from exc
            raise ProviderError(f"HTTP {exc.response.status_code}") from exc
        if isinstance(exc, httpx.HTTPError):
            raise ProviderUnavailableError(str(exc)) from exc
        raise ProviderError(str(exc)) from exc

    def chat(self, messages: list[ChatMessage], options: GenerationOptions | None = None) -> LLMResult:
        options = options or GenerationOptions()
        t0 = time.perf_counter()
        try:
            with self._client() as client:
                resp = client.post("/api/chat", json=self._payload(messages, options, stream=False))
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._raise_for(exc)
            raise  # unreachable
        content = (data.get("message") or {}).get("content", "")
        return LLMResult(
            content=content,
            provider=self.name,
            model=data.get("model", self.model),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            raw={k: data.get(k) for k in ("total_duration", "load_duration", "eval_duration", "prompt_eval_duration")},
        )

    def stream_chat(self, messages: list[ChatMessage], options: GenerationOptions | None = None) -> Iterator[str]:
        options = options or GenerationOptions()
        try:
            with self._client() as client:
                with client.stream("POST", "/api/chat", json=self._payload(messages, options, stream=True)) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get("error"):
                            raise ProviderError(str(data["error"])[:200])
                        piece = (data.get("message") or {}).get("content", "")
                        if piece:
                            yield piece
                        if data.get("done"):
                            break
        except ProviderError:
            raise
        except Exception as exc:
            self._raise_for(exc)

    def health(self) -> ProviderHealth:
        try:
            with self._client(httpx.Timeout(5.0, connect=2.0)) as client:
                ver = client.get("/api/version").json().get("version")
                tags = client.get("/api/tags").json().get("models", [])
            names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
            return ProviderHealth(
                name=self.name,
                reachable=True,
                model=self.model,
                model_available=self.model in names,
                version=ver,
                detail=None if self.model in names else "Model indirilmemiş: ollama pull " + self.model,
            )
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                reachable=False,
                model=self.model,
                model_available=False,
                detail=f"Ollama erişilemedi ({type(exc).__name__}).",
            )
