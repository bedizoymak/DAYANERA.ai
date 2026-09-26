"""Alembic environment for DAYANERA.ai.

Migrations are plain, Supabase-compatible PostgreSQL SQL files stored in
``database/migrations/sql``; each Alembic revision applies one SQL file.
The database URL comes from the application settings (``DATABASE_URL`` in
the untracked ``.env``) or from ``-x database_url=...`` for test databases.
"""
from __future__ import annotations

import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

config = context.config


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("database_url"):
        return x_args["database_url"]
    from app.core.config import get_settings

    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), literal_binds=True, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
