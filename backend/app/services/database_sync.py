"""Conservative two-way reconciliation for one local DB / one Supabase pair.

Native tables, explicit opt-in, durable common-ancestor hashes, fail-closed
conflicts. No last-writer-wins, auth session transfer, or filesystem transfer.
This bounded snapshot implementation is intended for the small-team beta.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from itertools import groupby
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app.core.config import REPO_ROOT, Settings

REMOTE_SCHEMA = "dayanera"
TABLES = (
    "users", "knowledge_areas", "user_scopes", "documents", "document_versions",
    "document_pages", "document_chunks", "archive_members", "document_relationships",
    "extracted_values", "conversations", "messages", "message_attachments",
    "message_sources", "calculations", "memory_items", "recommendation_notes",
)
LOCAL_ONLY = ("auth_sessions", "ingestion_jobs", "system_state", "audit_events")
MAX_ROWS = 100_000
MAX_BYTES = 64 * 1024 * 1024
LOCK_ID = 731_946_202
log = logging.getLogger("dayanera.sync")


class SyncError(RuntimeError):
    """Only static, non-sensitive error codes may leave the sync layer."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def reconcile(local: dict, remote: dict, baseline: dict) -> tuple[dict, list]:
    """Three-way, whole-row comparison; absence after a baseline is deletion."""
    merged, conflicts = {}, []
    for key in sorted(local.keys() | remote.keys() | baseline.keys()):
        left, right = local.get(key), remote.get(key)
        lh, rh = digest(left), digest(right)
        before = baseline.get(key, digest(None))
        if lh == rh:
            chosen = left
        elif lh == before:
            chosen = right
        elif rh == before:
            chosen = left
        else:
            conflicts.append(key)
            continue
        if chosen is not None:
            merged[key] = chosen
    return merged, conflicts


def remote_dsn(settings: Settings) -> str:
    value = settings.supabase_database_url.get_secret_value().strip()
    if not value:
        raise SyncError("missing_database_url")
    try:
        args = conninfo_to_dict(value)
        project = urlparse(settings.supabase_url).hostname or ""
        ref = project.removesuffix(".supabase.co")
        host = args.get("host", "")
        direct = host == f"db.{ref}.supabase.co"
        pooled = host.endswith(".pooler.supabase.com") and args.get("user", "").endswith("." + ref)
        if (not project.endswith(".supabase.co") or not ref or not (direct or pooled)
                or not args.get("password") or args.get("port", "5432") != "5432"
                or set(args) - {"host", "port", "user", "password", "dbname", "sslmode", "sslrootcert"}):
            raise ValueError
        # Certificate and hostname verification; never weaken TLS on retry.
        args.update(sslmode="verify-full", sslrootcert=args.get("sslrootcert", "system"))
        return psycopg.conninfo.make_conninfo(**args)
    except (ValueError, psycopg.Error):
        raise SyncError("invalid_database_url_use_session_pooler_or_direct_tls") from None


def connect(dsn: str):
    return psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://"),
                          connect_timeout=5, application_name="dayanera-sync",
                          keepalives_idle=10, keepalives_interval=5, keepalives_count=2,
                          options="-c statement_timeout=30000 -c lock_timeout=2000 "
                                  "-c idle_in_transaction_session_timeout=45000 -c timezone=UTC")


def _defer_foreign_keys(conn, schema: str):
    rows = conn.execute("""SELECT c.relname, k.conname FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE k.contype='f' AND n.nspname=%s AND c.relname=ANY(%s) AND NOT k.condeferrable""",
                        (schema, list(TABLES))).fetchall()
    for table, constraint in rows:
        conn.execute(sql.SQL("ALTER TABLE {}.{} ALTER CONSTRAINT {} DEFERRABLE INITIALLY IMMEDIATE")
                     .format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(constraint)))


def initialize(local_dsn: str, cloud_dsn: str):
    """Explicit setup only. Never adopt or erase an existing unowned cloud schema."""
    with connect(local_dsn) as local:
        local.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        local.execute("CREATE SCHEMA IF NOT EXISTS dayanera_sync")
        local.execute("REVOKE ALL ON SCHEMA dayanera_sync FROM PUBLIC")
        local.execute("""CREATE TABLE IF NOT EXISTS dayanera_sync.control (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), pair_id uuid NOT NULL,
            status jsonb NOT NULL DEFAULT '{}')""")
        local.execute("""INSERT INTO dayanera_sync.control(singleton,pair_id) VALUES(true,%s)
            ON CONFLICT DO NOTHING""", (uuid.uuid4(),))
        local.execute("""CREATE TABLE IF NOT EXISTS dayanera_sync.baseline (
            table_name text NOT NULL, row_key text NOT NULL, hash text NOT NULL,
            PRIMARY KEY(table_name,row_key))""")
        _defer_foreign_keys(local, "public")
        pair = local.execute("SELECT pair_id FROM dayanera_sync.control").fetchone()[0]
    with connect(cloud_dsn) as remote:
        remote.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        exists = remote.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (REMOTE_SCHEMA,)).fetchone()
        if exists:
            try:
                other = remote.execute("SELECT pair_id FROM dayanera._sync_pair").fetchone()[0]
            except (psycopg.Error, TypeError, IndexError):
                raise SyncError("remote_schema_not_owned") from None
            if other != pair:
                raise SyncError("remote_pair_mismatch")
        else:
            remote.execute("CREATE SCHEMA dayanera")
            remote.execute("SET LOCAL search_path=dayanera,pg_catalog")
            remote.execute((REPO_ROOT / "database/migrations/sql/0001_initial.sql").read_text(encoding="utf-8"))
            remote.execute("CREATE TABLE dayanera._sync_pair(pair_id uuid PRIMARY KEY)")
            remote.execute("INSERT INTO dayanera._sync_pair VALUES(%s)", (pair,))
        _defer_foreign_keys(remote, REMOTE_SCHEMA)
        remote.execute("REVOKE ALL ON SCHEMA dayanera FROM PUBLIC")
        remote.execute("REVOKE ALL ON ALL TABLES IN SCHEMA dayanera FROM PUBLIC")
        # Supabase default privileges must not expose copied private data.
        for role in ("anon", "authenticated", "service_role"):
            if remote.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                remote.execute(sql.SQL("REVOKE ALL ON SCHEMA dayanera FROM {}").format(sql.Identifier(role)))
                remote.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA dayanera FROM {}")
                               .format(sql.Identifier(role)))
                remote.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA dayanera FROM {}")
                               .format(sql.Identifier(role)))
        for table in TABLES + LOCAL_ONLY + ("_sync_pair",):
            remote.execute(sql.SQL("ALTER TABLE dayanera.{} ENABLE ROW LEVEL SECURITY")
                           .format(sql.Identifier(table)))
    return {"status": "initialized", "direction": "bidirectional", "files_synced": False}


def _specs(conn, schema):
    result = {}
    for table in TABLES:
        cols = conn.execute("""SELECT column_name,udt_name,is_generated FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""", (schema, table)).fetchall()
        pk = conn.execute("""SELECT a.attname FROM pg_index i
            JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(num,ord)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
            WHERE n.nspname=%s AND c.relname=%s AND i.indisprimary ORDER BY k.ord""", (schema, table)).fetchall()
        if not cols or not pk:
            raise SyncError("schema_mismatch")
        result[table] = (cols, [p[0] for p in pk])
    return result


def _snapshot(conn, schema, specs):
    rows, size = {}, 0
    for table, (cols, pk) in specs.items():
        writable = [name for name, _, generated in cols if generated == "NEVER"]
        query = sql.SQL("SELECT to_jsonb(t) FROM (SELECT {} FROM {}.{}) t").format(
            sql.SQL(",").join(map(sql.Identifier, writable)), sql.Identifier(schema), sql.Identifier(table))
        with conn.cursor(name="snapshot_" + table) as cursor:
            cursor.execute(query)
            for (row,) in cursor:
                key = json.dumps([row[p] for p in pk], separators=(",", ":"))
                rows[(table, key)] = row
                size += len(json.dumps(row, ensure_ascii=False).encode())
                if len(rows) > MAX_ROWS or size > MAX_BYTES:
                    raise SyncError("snapshot_limit_exceeded")
    return rows


def _apply(conn, schema, current, merged, specs):
    # Delete first to release unique values. Deferred FKs support document/version
    # cycles. A full post-write comparison detects unintended cascade side effects.
    for table, key in sorted(current.keys() - merged.keys(), reverse=True):
        _, pk = specs[table]
        predicate = sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(p)) for p in pk)
        conn.execute(sql.SQL("DELETE FROM {}.{} WHERE {}").format(
            sql.Identifier(schema), sql.Identifier(table), predicate), json.loads(key))
    changes = [(key, row) for key, row in merged.items() if digest(current.get(key)) != digest(row)]
    # Release the application's partial unique 'active version' slot first.
    def group(item):
        return (item[0][0] != "document_versions" or item[1].get("is_active", True), item[0][0])

    changes.sort(key=group)
    for (_, table), batch in groupby(changes, key=group):
        cols, pk = specs[table]
        names = [name for name, _, generated in cols if generated == "NEVER"]
        columns = sql.SQL(",").join(map(sql.Identifier, names))
        updates = sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(n), sql.Identifier(n))
                                    for n in names if n not in pk)
        query = sql.SQL("""INSERT INTO {}.{} ({})
            SELECT {} FROM jsonb_populate_record(NULL::{}.{},%s)
            ON CONFLICT ({}) DO UPDATE SET {}""").format(
                sql.Identifier(schema), sql.Identifier(table), columns, columns,
                sql.Identifier(schema), sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, pk)), updates)
        # Psycopg batches these writes through its pipeline, avoiding a network
        # round trip per ISO text chunk during the initial cloud upload.
        with conn.cursor() as cursor:
            cursor.executemany(query, [(Jsonb(row),) for _, row in batch])
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    actual = _snapshot(conn, schema, specs)
    if ({key: digest(row) for key, row in actual.items()}
            != {key: digest(row) for key, row in merged.items()}):
        raise SyncError("post_write_verification_failed")
    return len(changes) + len(current.keys() - merged.keys())


def sync_once(local_dsn: str, cloud_dsn: str) -> dict:
    """Remote commit precedes local commit. Old baseline survives partial failure.

    A retry converges or reports a conflict; it never silently chooses a newer
    clock. All compared tables are locked against writers during reconciliation.
    """
    with connect(local_dsn) as local:
        if not local.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            raise SyncError("sync_already_running")
        pair = local.execute("SELECT pair_id FROM dayanera_sync.control").fetchone()[0]
        # Connect remotely before taking any application table locks locally.
        with connect(cloud_dsn) as remote:
            other = remote.execute("SELECT pair_id FROM dayanera._sync_pair").fetchone()[0]
            if other != pair:
                raise SyncError("remote_pair_mismatch")
            for conn, schema in ((remote, REMOTE_SCHEMA), (local, "public")):
                tables = sql.SQL(",").join(sql.Identifier(schema, t) for t in sorted(TABLES))
                conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(tables))
                conn.execute("SET CONSTRAINTS ALL DEFERRED")
            specs = _specs(local, "public")
            if specs != _specs(remote, REMOTE_SCHEMA):
                raise SyncError("schema_mismatch")
            left = _snapshot(local, "public", specs)
            right = _snapshot(remote, REMOTE_SCHEMA, specs)
            base = {(table, key): value for table, key, value in local.execute(
                "SELECT table_name,row_key,hash FROM dayanera_sync.baseline")}
            merged, conflicts = reconcile(left, right, base)
            if conflicts:
                result = {"status": "conflict", "conflict_count": len(conflicts),
                          "conflicts": [{"table": t, "key": k} for t, k in conflicts[:100]]}
                # Read-only cycle; baseline remains unchanged until both sides agree.
                local.execute("UPDATE dayanera_sync.control SET status=status || %s::jsonb", (Jsonb(result),))
                return result
            pushed = _apply(remote, REMOTE_SCHEMA, right, merged, specs)
            pulled = _apply(local, "public", left, merged, specs)
            if pushed or pulled:
                local.execute("""INSERT INTO public.audit_events(event_type,outcome,details)
                    VALUES('supabase.sync','success',%s)""", (Jsonb({"pushed": pushed, "pulled": pulled}),))
            # Baseline, application writes and successful status share one local tx.
            local.execute("DELETE FROM dayanera_sync.baseline")
            if merged:
                with local.cursor() as cursor:
                    cursor.executemany("INSERT INTO dayanera_sync.baseline VALUES(%s,%s,%s)",
                                       [(t, k, digest(row)) for (t, k), row in merged.items()])
            result = {"status": "ok", "pushed": pushed, "pulled": pulled,
                      "last_success": datetime.now(UTC).isoformat(), "conflict_count": 0}
            local.execute("UPDATE dayanera_sync.control SET status=%s", (Jsonb(result),))
            # The remote context commits first. Failure rolls back the local tx.
        return result


def read_status(settings: Settings) -> dict:
    try:
        with connect(settings.database_url) as conn:
            value = conn.execute("SELECT status FROM dayanera_sync.control").fetchone()
            return value[0] if value else {"status": "not_initialized"}
    except psycopg.Error:
        return {"status": "not_initialized"}


class SyncWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self):
        if self.settings.supabase_enabled:
            self.thread = threading.Thread(target=self.run, name="supabase-sync", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run(self):
        delay = self.settings.supabase_sync_interval_seconds
        while not self.stop_event.is_set():
            start = time.monotonic()
            try:
                result = sync_once(self.settings.database_url, remote_dsn(self.settings))
                log.info("Supabase sync: %s", result["status"])
                delay = self.settings.supabase_sync_interval_seconds
            except Exception as exc:  # noqa: BLE001 - background boundary; never log DB exception content
                code = str(exc) if isinstance(exc, SyncError) else type(exc).__name__
                log.warning("Supabase sync bekliyor: %s", code)
                try:
                    with connect(self.settings.database_url) as local:
                        local.execute("""UPDATE dayanera_sync.control
                            SET status=status || %s::jsonb""", (Jsonb({"status": "waiting", "error": code}),))
                except psycopg.Error:
                    pass
                delay = min(max(delay * 2, 30), 900)
            self.stop_event.wait(max(1, delay - (time.monotonic() - start)))
