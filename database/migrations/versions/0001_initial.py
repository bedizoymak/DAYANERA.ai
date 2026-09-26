"""Initial DAYANERA.ai schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-26
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0001_initial.sql"


def upgrade() -> None:
    # Execute the portable SQL file verbatim through the DBAPI cursor so that
    # PL/pgSQL bodies and casts are passed through untouched.
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Dropping the schema would destroy chats, documents and audit records.
    # Destructive resets are handled only by scripts/reset-local.ps1 with an
    # explicit confirmation argument.
    raise NotImplementedError("Destructive downgrades are intentionally not automated.")
