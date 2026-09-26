"""Logging setup with secret redaction.

Logs go to the console and to ``DATA_ROOT/logs/backend.log`` (gitignored).
A redaction filter masks configured secrets and password-like key/value
pairs so that credentials never reach log files.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from app.core.config import Settings
from app.core.paths import fs

_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)(\s*[=:]\s*)([^\s,;&\"']+)"),
    re.compile(r"(postgresql(?:\+psycopg)?://[^:/\s]+:)([^@\s]+)(@)"),
]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        # Short secrets (e.g. the beta "1234") are still masked, but only as
        # whole tokens to avoid mangling unrelated numbers.
        self._secrets = [s for s in secrets if s]

    def _redact(self, text: str) -> str:
        for s in self._secrets:
            if len(s) >= 6:
                text = text.replace(s, "***")
            else:
                text = re.sub(rf"(?<![\w]){re.escape(s)}(?![\w])", "***", text)
        text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
        text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = self._redact(msg)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


_configured = False


def setup_logging(settings: Settings) -> None:
    global _configured
    if _configured:
        return
    secrets = [
        settings.initial_admin_password.get_secret_value(),
        settings.postgres_password.get_secret_value(),
        settings.openai_api_key.get_secret_value(),
        settings.anthropic_api_key.get_secret_value(),
        settings.supabase_secret_key.get_secret_value(),
        settings.supabase_publishable_key.get_secret_value(),
    ]
    flt = RedactingFilter(secrets)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(flt)
    root.addHandler(console)
    log_dir = settings.data_root / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(fs(log_dir / "backend.log"), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(flt)
        root.addHandler(fh)
    except OSError:
        root.warning("Log dosyası açılamadı; yalnızca konsola yazılıyor.")
    for noisy in ("httpx", "httpcore", "multipart", "PIL", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
