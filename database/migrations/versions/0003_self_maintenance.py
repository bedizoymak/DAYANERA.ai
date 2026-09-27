"""Self-maintaining engineering knowledge: mismatch events and correction memory.

Revision ID: 0003_self_maintenance
Revises: 0002_canonical_ingestion
Create Date: 2026-09-27
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0003_self_maintenance"
down_revision = "0002_canonical_ingestion"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def upgrade() -> None:
    _run("0003_self_maintenance.sql")


def downgrade() -> None:
    _run("0003_self_maintenance.down.sql")
