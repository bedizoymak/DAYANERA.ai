"""Operational CLI: python -m app.cli <command>

Commands:
  check-config     validate .env boundaries and paths (no secrets printed)
  migrate          apply Alembic migrations (portable SQL)
  seed             seed knowledge areas and the initial admin (idempotent)
  serve            run the API bound to APP_HOST:APP_PORT (loopback only)
  reindex          full manual rescan + reindex, processed synchronously
  export-openapi   write docs/openapi.json
  corpus-status    lifecycle status and quality-gate findings of every active document
  corpus-approve   --code "ISO 53" --by <owner_admin> [--note ...]   approve for the verified corpus
  corpus-revoke    --code ... --by ... --note ...                     take out of the verified corpus
  corpus-reject    --code ... --by ... --note ...                     mark the extraction as failed
  knowledge-validate   [--out docs/knowledge/formula_registry_validation.json]  authority validation snapshot
                       of the formula registry (exit 1 on an engine CONFLICT)
  knowledge-scan       --repos DIR [--out FILE] [--update-registry]   static reference-repository scan
  knowledge-reverify   re-run the regression of corrections verified with another engine/registry
  inspect-document     --file PDF | --code "ISO 21771" [--type formula] [--page 25] [--clause 4.3]
                       [--preview 80 | --full] [--export out.jsonl]   chunk inspection for one document
  reingest-document    --code "ISO 21771" [--apply] [--allow-revoke]   safe re-extraction of one document
                       (dry run by default: gates + old/new comparison, nothing written)
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
        "model": s.ollama_model, "fast_model": s.ollama_fast_model or None,
        "heavy_model": s.ollama_heavy_model or None, "project_root": str(s.project_root), "iso_booklets": str(s.iso_booklets_path),
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
    # X-Forwarded-* is trusted only from the loopback reverse proxy (Caddy for LAN access),
    # so audit logs see the real LAN client and the scheme is https behind the proxy.
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, log_level="info", proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", server_header=False)
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


def cmd_corpus_status() -> int:
    from app.db.session import init_engine, session_scope
    from app.services.corpus import status_rows

    init_engine()
    with session_scope() as db:
        rows = status_rows(db)
    for r in rows:
        flags = ", ".join(f"{g['id']}:{g['status']}" for g in r["attention"]) or "-"
        print(f"{(r['standard_code'] or r['title'])[:28]:28s} {r['area'] or '-':14s} {r['ingestion_status']:11s} "
              f"{r['corpus_status']:12s} parça={r['chunks']:<4} {flags}")
    return 0


def cmd_corpus_action(action: str, code: str | None, by: str | None, note: str | None) -> int:
    from app.db.session import init_engine, session_scope
    from app.services import corpus

    if not code or not by:
        print("--code ve --by zorunludur (ör. --code \"ISO 53\" --by admin).", file=sys.stderr)
        return 2
    init_engine()
    with session_scope() as db:
        try:
            reviewer = corpus.reviewer_by_username(db, by)
            versions = corpus.find_versions(db, code)
            if not versions:
                raise corpus.CorpusError(f"Doğrulanmış korpus alanında etkin '{code}' belgesi yok.")
            fn = {"approve": corpus.approve, "revoke": corpus.revoke, "reject": corpus.reject}[action]
            for v in versions:
                fn(db, v.id, reviewer, note if action == "approve" else (note or ""))
                print(f"{action}: sürüm {v.id} -> {v.corpus_status}")
        except corpus.CorpusError as exc:
            db.rollback()
            print(f"REDDEDİLDİ: {exc}", file=sys.stderr)
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.cli")
    p.add_argument("command", choices=["check-config", "migrate", "seed", "serve", "reindex", "export-openapi",
                                       "sync-init", "sync-once", "sync-status", "corpus-status", "corpus-approve",
                                       "corpus-revoke", "corpus-reject", "knowledge-validate", "knowledge-scan",
                                       "knowledge-reverify", "inspect-document", "reingest-document"])
    p.add_argument("--code", help="standart kodu (ör. 'ISO 53', 'ISO 286-1:2010')")
    p.add_argument("--by", help="onaylayan owner_admin kullanıcı adı")
    p.add_argument("--note", help="inceleme notu / gerekçe")
    p.add_argument("--repos", help="referans depoların klonlandığı klasör (DAYANERA dışında)")
    p.add_argument("--out", help="tarama manifestosunun yazılacağı dosya")
    p.add_argument("--update-registry", action="store_true", help="satır/parmak izi bilgisini kayda yaz")
    p.add_argument("--file", help="inspect-document: PDF yolu (bellekte çıkarılır, veritabanına yazılmaz)")
    p.add_argument("--type", dest="ctype", help="inspect-document: yalnızca bu içerik türü (formula, table_row_group ...)")
    p.add_argument("--page", type=int, help="inspect-document: yalnızca bu sayfayı kapsayan parçalar")
    p.add_argument("--clause", help="inspect-document: yalnızca bu madde (ve alt maddeleri)")
    p.add_argument("--preview", type=int, default=80, help="metin önizleme uzunluğu (0 = metin yok)")
    p.add_argument("--full", action="store_true", help="tam parça metni (telifli belge: yalnızca yerelde kullanın)")
    p.add_argument("--export", help="JSON Lines çıktısı (varsayılan önizleme kurallarıyla)")
    p.add_argument("--apply", action="store_true", help="reingest-document: doğrulama geçerse uygula")
    p.add_argument("--allow-revoke", action="store_true", help="reingest-document: doğrulanmış onayı kaldırmaya izin ver")
    args = p.parse_args(argv)
    try:
        return {
            "check-config": cmd_check_config, "migrate": cmd_migrate, "seed": cmd_seed, "serve": cmd_serve,
            "reindex": cmd_reindex, "export-openapi": cmd_export_openapi,
            "sync-init": lambda: cmd_sync("init"), "sync-once": lambda: cmd_sync("once"),
            "sync-status": lambda: cmd_sync("status"), "corpus-status": cmd_corpus_status,
            "corpus-approve": lambda: cmd_corpus_action("approve", args.code, args.by, args.note),
            "corpus-revoke": lambda: cmd_corpus_action("revoke", args.code, args.by, args.note),
            "corpus-reject": lambda: cmd_corpus_action("reject", args.code, args.by, args.note),
            "knowledge-validate": lambda: cmd_knowledge_validate(args.out),
            "knowledge-scan": lambda: cmd_knowledge_scan(args.repos, args.out, args.update_registry),
            "knowledge-reverify": cmd_knowledge_reverify,
            "inspect-document": lambda: cmd_inspect_document(args),
            "reingest-document": lambda: cmd_reingest_document(args.code, args.apply, args.allow_revoke, args.by),
        }[args.command]()
    except ConfigError as exc:
        print(f"YAPILANDIRMA HATASI: {exc}", file=sys.stderr)
        return 2


def cmd_inspect_document(args) -> int:
    """Chunks of ONE document: from a PDF file (in memory, nothing written) or from the database."""
    from app.ingestion import inspect

    if not args.file and not args.code:
        print("--file PDF veya --code 'ISO 21771' gerekli", file=sys.stderr)
        return 2
    if args.file:
        s = get_settings()
        rows, meta = inspect.from_file(args.file, s)
    else:
        from app.db.session import init_engine

        init_engine()
        rows, meta = inspect.from_db(args.code)
    if args.export:
        n = inspect.export(rows, meta, args.export, preview=args.preview, full=args.full)
        print(f"{n} parça yazıldı: {args.export}")
        return 0
    print(inspect.render(rows, meta, preview=args.preview, full=args.full, kind=args.ctype, page=args.page,
                         clause=args.clause))
    return 0


def cmd_reingest_document(code: str | None, apply: bool, allow_revoke: bool, by: str | None) -> int:
    """Safe re-extraction of one document: dry run (default) -> validate -> replace atomically."""
    from app.db.session import init_engine, session_scope
    from app.ingestion.pipeline import reingest_version
    from app.services import corpus
    from app.services.audit import Actor

    if not code:
        print("--code gerekli (ör. --code \"ISO 21771\")", file=sys.stderr)
        return 2
    init_engine()
    with session_scope() as db:
        versions = corpus.find_versions(db, code)
        if not versions:
            print(f"Etkin '{code}' belgesi bulunamadı.", file=sys.stderr)
            return 1
        rc = 0
        for v in versions:
            report = reingest_version(db, v.id, actor=Actor(user_id=None, username=by or "cli-reingest"),
                                      dry_run=not apply, allow_revoke=allow_revoke)
            print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
            if report.get("blocked"):
                rc = 1
    return rc


def cmd_knowledge_validate(out: str | None = None) -> int:
    """Authority validation of the registry; prints (or writes) the deterministic snapshot (no DB needed)."""
    from pathlib import Path

    from app.knowledge.validation import snapshot, validate

    report = validate()
    text = json.dumps(snapshot(report), ensure_ascii=False, indent=2) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"Doğrulama anlık görüntüsü yazıldı: {out}")
    else:
        print(text)
    return 1 if report.summary()["by_status"]["CONFLICT"] else 0


def cmd_knowledge_scan(repos: str | None, out: str | None, update: bool) -> int:
    """Static scan of reference repositories cloned OUTSIDE the DAYANERA tree (nothing is executed)."""
    from pathlib import Path

    from app.knowledge.scanner import compact, scan, update_registry_anchors

    if not repos:
        print("--repos gerekli (ör. /tmp/dayanera-reference-repos)", file=sys.stderr)
        return 2
    root = Path(repos).resolve()
    if REPO_ROOT.resolve() in (root, *root.parents):
        print("Referans depolar DAYANERA çalışma ağacının dışında olmalı.", file=sys.stderr)
        return 2
    result = scan(root)
    if update:
        result["registry_anchors_updated"] = update_registry_anchors(result)
    if out:
        Path(out).write_text(json.dumps(compact(result), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    bad = [a for a in result["anchors"] if a["state"] in ("changed", "missing")]
    return 1 if bad else 0


def cmd_knowledge_reverify() -> int:
    from app.db.session import init_engine, session_scope
    from app.services import self_maintenance
    from app.services.audit import Actor

    init_engine(get_settings().database_url)
    with session_scope() as db:
        done = self_maintenance.reverify_stale(db, Actor.system())
    print(json.dumps({"reverified": done}, ensure_ascii=False))
    return 0


def cmd_sync(action: str) -> int:
    from app.services.database_sync import SyncError, initialize, read_status, remote_dsn, sync_once

    settings = get_settings()
    try:
        if action == "status":
            result = read_status(settings)
        else:
            dsn = remote_dsn(settings)
            result = (initialize if action == "init" else sync_once)(settings.database_url, dsn)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") in ("ok", "initialized") else 1
    except Exception as exc:
        # Driver errors can contain passwords and private row content.
        print(json.dumps({"status": "waiting", "error": str(exc) if isinstance(exc, SyncError)
                          else type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
