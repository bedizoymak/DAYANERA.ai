"""Canonical ingestion: verified-corpus lifecycle, chunk lineage, structure-aware FTS.

Revision ID: 0002_canonical_ingestion
Revises: 0001_initial
Create Date: 2026-09-27
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0002_canonical_ingestion"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def _run(name: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def upgrade() -> None:
    _run("0002_canonical_ingestion.sql")


def downgrade() -> None:
    # Drops only lifecycle/lineage metadata; raw versions, pages, chunks and audit
    # records stay. Approval history remains in audit_events.
    _run("0002_canonical_ingestion.down.sql")
