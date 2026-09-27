"""Engineering chunks: parent/child lineage, structural context, formula/table payloads.

Revision ID: 0004_engineering_chunks
Revises: 0003_self_maintenance
Create Date: 2026-09-27
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0004_engineering_chunks"
down_revision = "0003_self_maintenance"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def upgrade() -> None:
    _run("0004_engineering_chunks.sql")


def downgrade() -> None:
    # Parent context rows are removed and new content types are mapped back to the 0002 set;
    # raw versions, pages, the remaining chunks and the audit log stay.
    _run("0004_engineering_chunks.down.sql")
