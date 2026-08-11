"""Repair a missing durable knowledge retry-request ledger.

Revision ID: 0009_repair_knowledge_retry_requests
Revises: 0008_knowledge_progress
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_repair_knowledge_retry_requests"
down_revision: str | Sequence[str] | None = "0008_knowledge_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "knowledge_ingestion_retry_requests"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("retry_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["knowledge_ingestion_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "retry_key"),
    )


def downgrade() -> None:
    # The table belongs to 0006; this repair revision must not remove it.
    pass
