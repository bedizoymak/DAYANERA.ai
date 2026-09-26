"""DAYANERA.ai FastAPI application (DAYANERA Core API v1, localhost only)."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.routes import (
    audit_log,
    auth,
    calculations,
    conversations,
    documents,
    extractions,
    memory,
    notes,
    retrieval,
    system,
    users,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.security import CSRF_HEADER
from app.db.session import init_engine, session_scope
from app.inference.base import ChatMessage, GenerationOptions, ProviderError
from app.inference.registry import get_provider
from app.ingestion.jobs import JobWorker
from app.ingestion.watcher import Watcher
from app.services import audit
from app.services.audit import Actor
from app.services.auth import ensure_schema_ready, seed_initial_admin, seed_reference_data
from app.services.database_sync import SyncWorker

log = logging.getLogger("dayanera")

API_PREFIX = "/api/v1"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _warmup_model() -> None:
    try:
        get_provider().chat([ChatMessage("user", "merhaba")], GenerationOptions(num_predict=1))
        log.info("Yerel model belleğe yüklendi (ısınma tamamlandı).")
    except ProviderError as exc:
        log.warning("Model ısınması yapılamadı: %s", exc.code)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings)
    app.state.path_warnings = settings.validate_paths()
    for w in app.state.path_warnings:
        log.warning(w)
    created = settings.ensure_runtime_dirs()
    for p in created:
        log.info("Çalışma klasörü oluşturuldu (proje kökü altında): %s", p)
    init_engine()
    schema_ok = False
    try:
        with session_scope() as db:
            schema_ok = ensure_schema_ready(db)
            if schema_ok:
                seed_reference_data(db)
                seed_initial_admin(db, settings)
                audit.record(db, Actor.system(), "system.startup", outcome="info",
                             details={"version": __version__, "bind": f"{settings.app_host}:{settings.app_port}"})
            else:
                log.error("Veritabanı şeması yok: önce migrasyonları uygulayın (scripts/start-local.ps1).")
    except OperationalError:
        log.error("Veritabanına bağlanılamadı. Docker Desktop'ı ve PostgreSQL konteynerini başlatın.")
    worker = JobWorker(settings)
    sync_worker = SyncWorker(settings)
    watcher = Watcher(settings, on_new_jobs=worker.wake)
    app.state.worker, app.state.watcher = worker, watcher
    if schema_ok and not settings.disable_background_workers:
        sync_worker.start()
        worker.start()
        if settings.watcher_enabled:
            watcher.start()
        threading.Thread(target=_warmup_model, daemon=True, name="model-warmup").start()
    yield
    sync_worker.stop()
    watcher.stop()
    worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DAYANERA Core API",
        version=__version__,
        description=(
            "Özel, yalnızca yerel (127.0.0.1) DAYANERA.ai çekirdek API'si. Tarayıcı, gelecekteki Windows ve Android "
            "istemcileri bu tek arayüzü kullanır. Herkese açık bir ürün API'si değildir."
        ),
        openapi_url=f"{API_PREFIX}/openapi.json",
        # Swagger UI / ReDoc would load JavaScript from a public CDN; the beta must
        # work offline without cloud dependencies, so only the JSON document is served.
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts())
    origins = [f"http://{h}:{settings.frontend_port}" for h in ("127.0.0.1", "localhost")]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", CSRF_HEADER])

    @app.middleware("http")
    async def csrf_and_headers(request: Request, call_next):
        if request.method in UNSAFE_METHODS and request.url.path.startswith(API_PREFIX):
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "İstek reddedildi: güvenlik başlığı (CSRF) eksik."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith(API_PREFIX) and not request.url.path.endswith(("/docs", "/openapi.json")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(_request: Request, exc: ProviderError):
        return JSONResponse({"detail": exc.user_message, "code": exc.code}, status_code=503)

    @app.exception_handler(OperationalError)
    async def db_error(_request: Request, _exc: OperationalError):
        return JSONResponse({"detail": "Veritabanına ulaşılamıyor. Docker Desktop'ı ve PostgreSQL konteynerini "
                                       "başlatın (scripts/start-local.ps1).", "code": "database_unavailable"},
                            status_code=503)

    for r in (system.router, auth.router, conversations.router, documents.router, extractions.router,
              calculations.router, memory.router, audit_log.router, users.router, notes.router, retrieval.router):
        app.include_router(r, prefix=API_PREFIX)
    return app


app = create_app()
