"""Typed runtime configuration (Pydantic settings).

Values are read from the untracked root ``.env`` file (or the file named by
``DAYANERA_ENV_FILE``) and from process environment variables. Validation
enforces the non-negotiable beta boundaries: loopback-only bindings, a local
database, a local Ollama endpoint, disabled cloud providers and all
application-controlled data below ``PROJECT_ROOT``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _default_env_file() -> str:
    return os.environ.get("DAYANERA_ENV_FILE", str(REPO_ROOT / ".env"))


class ConfigError(RuntimeError):
    """Raised when configuration violates a beta boundary."""


def _norm(p: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(str(p))))


def is_within(child: Path, parent: Path) -> bool:
    c = os.path.normcase(str(_norm(child)))
    p = os.path.normcase(str(_norm(parent)))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- application ---
    app_name: str = "DAYANERA.ai"
    app_env: str = "beta_local"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 5173

    # --- database ---
    postgres_db: str = "dayanera"
    postgres_user: str = "dayanera"
    postgres_password: SecretStr = SecretStr("change-local-password")
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 54329
    database_url: str = "postgresql+psycopg://dayanera:change-local-password@127.0.0.1:54329/dayanera"

    # --- inference ---
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b-instruct-q4_K_M"
    ollama_request_timeout_seconds: float = 600.0
    ollama_num_ctx: int = 4096
    ollama_num_predict: int = 700
    ollama_keep_alive: str = "30m"

    # --- initial admin ---
    initial_admin_username: str = "admin"
    initial_admin_password: SecretStr = SecretStr("1234")

    # --- paths ---
    project_root: Path = REPO_ROOT
    iso_booklets_path: Path = REPO_ROOT / "iso booklets"
    data_root: Path = REPO_ROOT / "data"
    agent_notes_path: Path = REPO_ROOT / "agent-notes"
    extra_watch_roots: str = ""

    # --- watcher / ingestion ---
    watcher_enabled: bool = True
    watcher_interval_seconds: int = 30
    ingestion_workers: int = 1
    upload_max_bytes: int = 1024 * 1024 * 1024
    ocr_enabled: bool = True
    ocr_dpi: int = 200
    ocr_max_pages_per_document: int = 250
    image_max_pixels: int = 40_000_000
    archive_max_members: int = 2000
    archive_max_total_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    archive_max_member_bytes: int = 512 * 1024 * 1024
    archive_max_compression_ratio: int = 200
    draft_value_max_pages: int = 12
    transcription_enabled: bool = True
    whisper_model_name: str = "small"
    # optional read-only override of the model folder (default: DATA_ROOT/models/faster-whisper-<name>)
    whisper_model_path: str = ""
    whisper_compute_type: str = "int8"
    audio_max_seconds: int = 3600
    video_max_transcribe_seconds: int = 600
    video_frame_count: int = 3

    # --- retrieval / answers ---
    retrieval_top_k: int = 4
    retrieval_max_context_chars: int = 3000
    # Step 2 Order C relevance gate (absolute, 0..1): the best passage must support at
    # least this share of the question's significant terms, otherwise the answer is
    # refused before any LLM call (reason "low_relevance").
    retrieval_min_score: float = Field(default=0.4, ge=0.0, le=1.0)  # calibrated 2026-09-26, see report
    # share of glossary concepts a passage must match to be a candidate (multi-concept questions)
    retrieval_min_coverage: float = Field(default=0.5, ge=0.0, le=1.0)
    session_ttl_hours: int = 12
    calc_llm_compare: bool = True
    summary_every_n_messages: int = 12

    # --- future providers (disabled in beta) ---
    llm_provider: Literal["local_ollama"] = "local_ollama"
    openai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    anthropic_enabled: bool = False
    anthropic_api_key: SecretStr = SecretStr("")

    # --- future Supabase (prepared, disabled) ---
    supabase_enabled: bool = False
    supabase_url: str = ""
    supabase_publishable_key: SecretStr = SecretStr("")
    supabase_secret_key: SecretStr = SecretStr("")
    supabase_database_url: SecretStr = SecretStr("")
    supabase_sync_interval_seconds: int = Field(default=60, ge=15, le=86400)

    # --- testing hooks (never set in production .env) ---
    disable_background_workers: bool = Field(default=False)
    trusted_hosts_extra: str = ""

    @field_validator("project_root", "iso_booklets_path", "data_root", "agent_notes_path", mode="before")
    @classmethod
    def _expand_path(cls, v: object) -> object:
        if isinstance(v, str):
            # tolerate doubled backslashes coming from unquoted .env values
            return Path(os.path.normpath(v.replace("\\\\", "\\")))
        return v

    @model_validator(mode="after")
    def _enforce_beta_boundaries(self) -> "Settings":
        for name in ("app_host", "frontend_host", "postgres_host"):
            if getattr(self, name) not in LOOPBACK_HOSTS:
                raise ConfigError(
                    f"{name.upper()}={getattr(self, name)!r} izin verilmiyor: beta yalnızca 127.0.0.1/localhost "
                    "üzerinde dinleyebilir (0.0.0.0 veya ağ adresi yasak)."
                )
        db_host = urlparse(self.database_url.replace("postgresql+psycopg", "postgresql")).hostname
        if db_host not in LOOPBACK_HOSTS:
            raise ConfigError("DATABASE_URL yerel (127.0.0.1) PostgreSQL'i göstermelidir; harici veritabanı yasak.")
        ollama_host = urlparse(self.ollama_base_url).hostname
        if ollama_host not in LOOPBACK_HOSTS:
            raise ConfigError("OLLAMA_BASE_URL yalnızca yerel (127.0.0.1/localhost) olabilir.")
        if self.openai_enabled or self.anthropic_enabled:
            raise ConfigError(
                "OPENAI_ENABLED / ANTHROPIC_ENABLED beta sürümde true olamaz: çevrimiçi yapay zekâ sağlayıcıları devre dışıdır."
            )
        return self

    # ----- derived helpers -----
    @property
    def extra_watch_root_paths(self) -> list[Path]:
        roots = []
        for part in self.extra_watch_roots.split(","):
            part = part.strip()
            if part:
                roots.append(Path(os.path.normpath(part.replace("\\\\", "\\"))))
        return roots

    @property
    def models_dir(self) -> Path:
        return self.data_root / "models"

    @property
    def whisper_model_dir(self) -> Path:
        if self.whisper_model_path.strip():
            return Path(os.path.normpath(self.whisper_model_path.replace("\\\\", "\\")))
        return self.models_dir / f"faster-whisper-{self.whisper_model_name}"

    def validate_paths(self) -> list[str]:
        """Validate path boundaries. Returns a list of human-readable warnings.

        Raises ConfigError when application-controlled data would be written
        outside PROJECT_ROOT.
        """
        warnings: list[str] = []
        root = self.project_root
        if not root.exists():
            raise ConfigError(f"PROJECT_ROOT bulunamadı: {root}. .env içindeki yolu düzeltin.")
        for label, path in (("DATA_ROOT", self.data_root), ("AGENT_NOTES_PATH", self.agent_notes_path)):
            if not is_within(path, root):
                raise ConfigError(
                    f"{label} ({path}) PROJECT_ROOT ({root}) altında olmalıdır; uygulama verisi proje kökü dışına yazılamaz."
                )
        for label, path in [("ISO_BOOKLETS_PATH", self.iso_booklets_path)] + [
            ("EXTRA_WATCH_ROOTS", p) for p in self.extra_watch_root_paths
        ]:
            if not is_within(path, root):
                raise ConfigError(f"{label} ({path}) PROJECT_ROOT altında olmalıdır.")
            if not path.exists():
                warnings.append(
                    f"{label} klasörü bulunamadı: {path}. Klasörü oluşturup ISO PDF'lerini ekleyin; izleyici boş korpusla çalışır."
                )
        return warnings

    def ensure_runtime_dirs(self) -> list[Path]:
        """Create runtime directories (only below PROJECT_ROOT)."""
        self.validate_paths()
        created = []
        for sub in ("documents", "document-versions", "media", "indexes", "exports", "logs", "backups", "tmp", "models", "run"):
            p = self.data_root / sub
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(p)
        if not self.agent_notes_path.exists():
            self.agent_notes_path.mkdir(parents=True, exist_ok=True)
            created.append(self.agent_notes_path)
        return created

    def trusted_hosts(self) -> list[str]:
        hosts = ["127.0.0.1", "localhost", "[::1]", "::1"]
        hosts += [h.strip() for h in self.trusted_hosts_extra.split(",") if h.strip()]
        return hosts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
