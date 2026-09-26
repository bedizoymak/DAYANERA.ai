"""Real PostgreSQL pair tests; never connect to Supabase or the user's DB."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg
import pytest
from app.core.config import REPO_ROOT, Settings
from app.services import database_sync as sync
from conftest import BASE_DB_URL
from psycopg import sql
from pydantic import SecretStr


@dataclass(repr=False)
class Pair:
    local: str
    remote: str


@pytest.fixture
def pair():
    args = psycopg.conninfo.conninfo_to_dict(BASE_DB_URL.replace("postgresql+psycopg://", "postgresql://"))
    args["dbname"] = "postgres"
    names = ["dayanera_sync_test_" + uuid.uuid4().hex[:12] for _ in range(2)]
    with psycopg.connect(**args, autocommit=True) as admin:
        for name in names:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsns = [psycopg.conninfo.make_conninfo(**(args | {"dbname": name})) for name in names]
    try:
        with sync.connect(dsns[0]) as conn:
            conn.execute((REPO_ROOT / "database/migrations/sql/0001_initial.sql").read_text(encoding="utf-8"))
        sync.initialize(*dsns)
        yield Pair(*dsns)
    finally:
        with psycopg.connect(**args, autocommit=True) as admin:
            for name in names:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def execute(dsn, query, params=()):
    with sync.connect(dsn) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


def seed_user(pair):
    ident = uuid.uuid4()
    execute(pair.local, """INSERT INTO users(id,username,display_name,password_hash,role)
        VALUES(%s,'sync-user','Original','not-a-real-hash','member')""", (ident,))
    return ident


def test_real_bidirectional_crud_restart_conflict_and_generated_columns(pair):
    user = seed_user(pair)
    assert sync.sync_once(pair.local, pair.remote)["pushed"] == 1
    execute(pair.remote, "UPDATE dayanera.users SET display_name='Cloud' WHERE id=%s", (user,))
    assert sync.sync_once(pair.local, pair.remote)["pulled"] == 1
    assert execute(pair.local, "SELECT display_name FROM users")[0][0] == "Cloud"
    conversation, message = uuid.uuid4(), uuid.uuid4()
    execute(pair.remote, "INSERT INTO dayanera.conversations(id,owner_id,title) VALUES(%s,%s,'Remote chat')",
            (conversation, user))
    execute(pair.remote, "INSERT INTO dayanera.messages(id,conversation_id,role,content) VALUES(%s,%s,'user','Hello')",
            (message, conversation))
    assert sync.sync_once(pair.local, pair.remote)["pulled"] == 2
    assert execute(pair.local, "SELECT tsv IS NOT NULL FROM messages")[0][0]
    execute(pair.local, """UPDATE messages SET metadata='{"flag":true}'::jsonb""")
    sync.sync_once(pair.local, pair.remote)
    execute(pair.remote, """UPDATE dayanera.messages SET metadata='{"flag":1}'::jsonb""")
    assert sync.sync_once(pair.local, pair.remote)["pulled"] == 1
    assert type(execute(pair.local, "SELECT metadata FROM messages")[0][0]["flag"]) is int
    # A new invocation only uses the durable DB baseline.
    result = sync.sync_once(pair.local, pair.remote)
    assert result["pushed"] == result["pulled"] == 0
    execute(pair.local, "UPDATE conversations SET title='Local edit' WHERE id=%s", (conversation,))
    execute(pair.remote, "UPDATE dayanera.conversations SET title='Remote edit' WHERE id=%s", (conversation,))
    result = sync.sync_once(pair.local, pair.remote)
    assert result["status"] == "conflict" and result["conflict_count"] == 1
    assert execute(pair.local, "SELECT title FROM conversations")[0][0] == "Local edit"
    assert execute(pair.remote, "SELECT title FROM dayanera.conversations")[0][0] == "Remote edit"
    execute(pair.remote, "UPDATE dayanera.conversations SET title='Local edit'")
    assert sync.sync_once(pair.local, pair.remote)["status"] == "ok"
    execute(pair.remote, "DELETE FROM dayanera.messages")
    assert sync.sync_once(pair.local, pair.remote)["pulled"] == 1
    assert execute(pair.local, "SELECT count(*) FROM messages")[0][0] == 0
    execute(pair.local, "DELETE FROM conversations")
    assert sync.sync_once(pair.local, pair.remote)["pushed"] == 1
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.conversations")[0][0] == 0


def test_document_cycle_composite_keys_partial_unique_and_exclusions(pair):
    user = seed_user(pair)
    document, version, conversation, message = [uuid.uuid4() for _ in range(4)]
    with sync.connect(pair.local) as conn:
        conn.execute("SET CONSTRAINTS ALL DEFERRED")
        conn.execute("""INSERT INTO documents(id,title,source_kind,original_filename,status,current_version_id)
            VALUES(%s,'Document','upload','test.txt','active',%s)""", (document, version))
        conn.execute("""INSERT INTO document_versions(id,document_id,version_number,sha256,size_bytes,mime_type,
            storage_relpath,state,is_active,ingestion_status) VALUES(%s,%s,1,%s,0,'text/plain','test/v1.txt','active',true,'indexed')""",
                     (version, document, 'a' * 64))
        conn.execute("INSERT INTO conversations(id,owner_id,title) VALUES(%s,%s,'Chat')", (conversation, user))
        conn.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES(%s,%s,'user','Hi')", (message, conversation))
        conn.execute("INSERT INTO message_attachments(message_id,document_id,version_id) VALUES(%s,%s,%s)",
                     (message, document, version))
        conn.execute("INSERT INTO audit_events(event_type,outcome) VALUES('sync.test','info')")
    assert sync.sync_once(pair.local, pair.remote)["status"] == "ok"
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.message_attachments")[0][0] == 1
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.audit_events")[0][0] == 0
    next_version = uuid.uuid4()
    with sync.connect(pair.local) as conn:
        conn.execute("UPDATE document_versions SET is_active=false,state='superseded'")
        conn.execute("""INSERT INTO document_versions(id,document_id,version_number,sha256,size_bytes,mime_type,
            storage_relpath,state,is_active,ingestion_status) VALUES(%s,%s,2,%s,0,'text/plain','test/v2.txt','active',true,'indexed')""",
                     (next_version, document, 'b' * 64))
        conn.execute("UPDATE documents SET current_version_id=%s", (next_version,))
    assert sync.sync_once(pair.local, pair.remote)["status"] == "ok"
    assert execute(pair.remote, "SELECT current_version_id FROM dayanera.documents")[0][0] == next_version


def test_delete_update_conflict_offline_and_identity_guard(pair):
    seed_user(pair)
    sync.sync_once(pair.local, pair.remote)
    execute(pair.local, "DELETE FROM users")
    execute(pair.remote, "UPDATE dayanera.users SET display_name='Keep me'")
    assert sync.sync_once(pair.local, pair.remote)["status"] == "conflict"
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.users")[0][0] == 1
    bad = psycopg.conninfo.make_conninfo(pair.remote, port=1)
    with pytest.raises(psycopg.OperationalError):
        sync.sync_once(pair.local, bad)
    assert execute(pair.local, "SELECT count(*) FROM dayanera_sync.baseline")[0][0] == 1
    execute(pair.remote, "UPDATE dayanera._sync_pair SET pair_id=%s", (uuid.uuid4(),))
    with pytest.raises(sync.SyncError, match="remote_pair_mismatch"):
        sync.sync_once(pair.local, pair.remote)


def test_unique_collision_rolls_back_both_sides(pair):
    seed_user(pair)
    execute(pair.remote, """INSERT INTO dayanera.users(username,display_name,password_hash,role)
        VALUES('sync-user','Different user','not-a-real-hash','member')""")
    with pytest.raises(psycopg.errors.UniqueViolation):
        sync.sync_once(pair.local, pair.remote)
    assert execute(pair.local, "SELECT display_name FROM users") == [('Original',)]
    assert execute(pair.remote, "SELECT display_name FROM dayanera.users") == [('Different user',)]
    assert execute(pair.local, "SELECT count(*) FROM dayanera_sync.baseline")[0][0] == 0


def test_recovery_after_remote_commit_local_commit_failure(pair, monkeypatch):
    seed_user(pair)
    real_connect = sync.connect

    class FailLocalCommit:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self.conn.__enter__()

        def __exit__(self, typ, value, tb):
            self.conn.rollback()
            self.conn.close()
            raise RuntimeError('simulated local commit failure')

    def connect(dsn):
        conn = real_connect(dsn)
        return FailLocalCommit(conn) if dsn == pair.local else conn

    monkeypatch.setattr(sync, "connect", connect)
    with pytest.raises(RuntimeError, match="simulated"):
        sync.sync_once(pair.local, pair.remote)
    monkeypatch.setattr(sync, "connect", real_connect)
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.users")[0][0] == 1
    assert execute(pair.local, "SELECT count(*) FROM dayanera_sync.baseline")[0][0] == 0
    assert sync.sync_once(pair.local, pair.remote)["status"] == "ok"


def test_connection_validation_and_three_way_rules():
    settings = Settings(_env_file=None, supabase_url="https://example.supabase.co",
                        supabase_database_url=SecretStr("postgresql://postgres.example:test@aws-0-x.pooler.supabase.com:5432/postgres"))
    args = psycopg.conninfo.conninfo_to_dict(sync.remote_dsn(settings))
    assert args["sslmode"] == "verify-full"
    for value in ("", "postgresql://a:b@127.0.0.1/postgres",
                  "postgresql://postgres.other:abc@aws-0-x.pooler.supabase.com:5432/postgres",
                  "postgresql://postgres.example:abc@aws-0-x.pooler.supabase.com:6543/postgres"):
        with pytest.raises(sync.SyncError):
            sync.remote_dsn(settings.model_copy(update={"supabase_database_url": SecretStr(value)}))
    key = ("users", "id")
    assert sync.reconcile({key: {"a": 1}}, {key: {"a": 2}}, {})[1] == [key]
    assert sync.reconcile({}, {}, {key: sync.digest({"a": 1})}) == ({}, [])


def test_schema_drift_limits_and_unowned_remote_are_not_overwritten(pair, monkeypatch):
    seed_user(pair)
    monkeypatch.setattr(sync, "MAX_ROWS", 0)
    with pytest.raises(sync.SyncError, match="snapshot_limit_exceeded"):
        sync.sync_once(pair.local, pair.remote)
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.users")[0][0] == 0
    monkeypatch.setattr(sync, "MAX_ROWS", 100_000)
    execute(pair.remote, "ALTER TABLE dayanera.users ADD COLUMN extra_field text")
    with pytest.raises(sync.SyncError, match="schema_mismatch"):
        sync.sync_once(pair.local, pair.remote)
    assert execute(pair.remote, "SELECT count(*) FROM dayanera.users")[0][0] == 0
    execute(pair.remote, "DROP TABLE dayanera._sync_pair")
    with pytest.raises(sync.SyncError, match="remote_schema_not_owned"):
        sync.initialize(pair.local, pair.remote)
    assert execute(pair.local, "SELECT count(*) FROM users")[0][0] == 1
