"""Operational CLI: python -m app.cli <command>

Commands:
  check-config     validate .env boundaries and paths (no secrets printed)
  migrate          apply Alembic migrations (portable SQL)
  seed             seed knowledge areas and the initial admin (idempotent)
  serve            run the API bound to APP_HOST:APP_PORT (loopback only)
  reindex          full manual rescan + reindex, processed synchronously
  export-openapi   write docs/openapi.json
"""
from __future__ import annotations

import argparse
import json
import sys

from app.core.config import REPO_ROOT, ConfigError, get_settings


def _alembic_cfg():
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "database" / "migrations"))
    return cfg


def cmd_check_config() -> int:
    s = get_settings()
    warnings = s.validate_paths()
    print(json.dumps({
        "app_bind": f"{s.app_host}:{s.app_port}", "frontend_bind": f"{s.frontend_host}:{s.frontend_port}",
        "database": f"{s.postgres_host}:{s.postgres_port}/{s.postgres_db}", "ollama": s.ollama_base_url,
        "model": s.ollama_model, "project_root": str(s.project_root), "iso_booklets": str(s.iso_booklets_path),
        "data_root": str(s.data_root), "agent_notes": str(s.agent_notes_path), "supabase_enabled": s.supabase_enabled,
        "online_providers_enabled": s.openai_enabled or s.anthropic_enabled, "warnings": warnings,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_migrate() -> int:
    from alembic import command

    command.upgrade(_alembic_cfg(), "head")
    print("Migrasyonlar uygulandı (head).")
    return 0


def cmd_seed() -> int:
    from app.db.session import init_engine, session_scope
    from app.services.auth import seed_initial_admin, seed_reference_data

    s = get_settings()
    init_engine()
    with session_scope() as db:
        seed_reference_data(db)
        created = seed_initial_admin(db, s)
    print("İlk yönetici oluşturuldu." if created else "İlk yönetici zaten mevcut (değişiklik yok).")
    return 0


def cmd_serve() -> int:
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, log_level="info", proxy_headers=False,
                server_header=False)
    return 0


def cmd_reindex() -> int:
    from sqlalchemy import select

    from app.db.models import Document
    from app.db.session import init_engine, session_scope
    from app.ingestion.jobs import run_pending
    from app.ingestion.pipeline import queue_reindex
    from app.ingestion.watcher import Watcher
    from app.services.audit import Actor

    s = get_settings()
    s.ensure_runtime_dirs()
    init_engine()
    actor = Actor(user_id=None, username="cli-reindex")
    summary = Watcher(s).scan(actor)
    print("Tarama:", json.dumps({k: v for k, v in summary.items() if k != "roots"}, ensure_ascii=False))
    with session_scope() as db:
        docs = db.execute(select(Document).where(Document.status == "active")).scalars().all()
        n = sum(1 for d in docs if queue_reindex(db, d, actor))
    print(f"{n} belge yeniden indeksleme kuyruğuna alındı; işleniyor (OCR sayfaları uzun sürebilir)…")
    processed = run_pending(s)
    # a running backend worker may have claimed some jobs concurrently: wait for them too
    import time

    from app.ingestion.jobs import queue_counts

    while True:
        counts = queue_counts()
        busy = counts.get("queued", 0) + counts.get("running", 0)
        if not busy:
            break
        print(f"Arka uç işçisinde {busy} iş sürüyor; bekleniyor…")
        time.sleep(5)
    failed = counts.get("failed", 0)
    print(f"Tamamlandı: bu süreçte {processed} iş işlendi; kuyruk boş. Başarısız iş toplamı: {failed}.")
    return 0


def cmd_export_openapi() -> int:
    from app.main import app

    out = REPO_ROOT / "docs" / "openapi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAPI belgesi yazıldı: {out.relative_to(REPO_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.cli")
    p.add_argument("command", choices=["check-config", "migrate", "seed", "serve", "reindex", "export-openapi"])
    args = p.parse_args(argv)
    try:
        return {
            "check-config": cmd_check_config, "migrate": cmd_migrate, "seed": cmd_seed, "serve": cmd_serve,
            "reindex": cmd_reindex, "export-openapi": cmd_export_openapi,
        }[args.command]()
    except ConfigError as exc:
        print(f"YAPILANDIRMA HATASI: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
