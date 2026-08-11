"""Persist safe LightRAG document progress metadata.

Revision ID: 0008_knowledge_progress
Revises: 0007_knowledge_catalog
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0008_knowledge_progress"
down_revision: str | Sequence[str] | None = "0007_knowledge_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("knowledge_documents", "knowledge_remote_documents")


def upgrade() -> None:
    for table in _TABLES:
        safe_add_column(table, sa.Column("lightrag_stage", sa.String(length=16), nullable=True))
        safe_add_column(table, sa.Column("lightrag_chunks_count", sa.BigInteger(), nullable=True))
        safe_add_column(table, sa.Column("lightrag_stage_updated_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET lightrag_stage = CASE status
                    WHEN 'pending' THEN 'pending'
                    WHEN 'ready' THEN 'processed'
                    WHEN 'failed' THEN 'failed'
                END,
                lightrag_stage_updated_at = updated_at
                WHERE lightrag_stage IS NULL
                  AND status IN ('pending', 'ready', 'failed')
                """
            )
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        safe_drop_column(table, "lightrag_stage_updated_at")
        safe_drop_column(table, "lightrag_chunks_count")
        safe_drop_column(table, "lightrag_stage")
