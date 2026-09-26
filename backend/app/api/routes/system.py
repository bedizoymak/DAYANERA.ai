"""Health, readiness, system status and provider/integration status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import current_user, db_dep, provider_dep, require_owner, settings_dep
from app.core.config import Settings
from app.db.session import current_migration_revision, database_ping
from app.inference.base import LLMProvider
from app.inference.registry import provider_status
from app.services.auth import AuthenticatedUser
from app.services.system_status import supabase_status, system_status

router = APIRouter(tags=["system"])


@router.get("/health", summary="Canlılık (liveness)")
def health(settings: Settings = Depends(settings_dep)) -> dict:
    return {"status": "ok", "app": settings.app_name, "version": __version__, "env": settings.app_env}


@router.get("/readiness", summary="Hazırlık (readiness): veritabanı, migrasyon, Ollama")
def readiness(provider: LLMProvider = Depends(provider_dep)) -> dict:
    db_ok, db_err = database_ping()
    rev = current_migration_revision() if db_ok else None
    h = provider.health()
    ready = db_ok and rev is not None
    return {
        "ready": ready,
        "database": {"ok": db_ok, "error": db_err, "migration_revision": rev},
        "ollama": {"reachable": h.reachable, "model_available": h.model_available, "model": h.model},
        "messages": [m for m in [
            None if db_ok else "Veritabanına ulaşılamıyor: 'docker compose up -d postgres' ile PostgreSQL'i başlatın.",
            None if rev or not db_ok else "Veritabanı şeması eksik: migrasyonları uygulayın (scripts/start-local.ps1).",
            None if h.reachable else "Ollama çalışmıyor: Ollama uygulamasını başlatın; genel sohbet ve yanıt üretimi kullanılamaz.",
            None if h.model_available or not h.reachable else f"Model eksik: 'ollama pull {h.model}'.",
        ] if m],
    }


@router.get("/system/status", summary="Sistem durumu (gizli bilgi içermez)")
def status_(request: Request, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep),
            settings: Settings = Depends(settings_dep), provider: LLMProvider = Depends(provider_dep)) -> dict:
    state = request.app.state
    return system_status(db, settings, provider, getattr(state, "watcher", None), getattr(state, "worker", None),
                         is_owner=user.is_owner, path_warnings=getattr(state, "path_warnings", []))


@router.get("/providers/status", summary="Gelecek sağlayıcı yapılandırma durumu (anahtar döndürmez)")
def providers(user: AuthenticatedUser = Depends(current_user), settings: Settings = Depends(settings_dep)) -> dict:
    return provider_status(settings)


@router.get("/integrations/supabase/status", summary="Supabase hazırlık durumu (yalnızca sahip)")
def supabase(user: AuthenticatedUser = Depends(require_owner), settings: Settings = Depends(settings_dep)) -> dict:
    return supabase_status(settings)
