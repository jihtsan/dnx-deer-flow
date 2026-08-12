"""Add durable Nexus Skill receiver operations.

Revision ID: 0013_nexus_receiver_operations
Revises: 0012_merge_knowledge_and_mcp_tasks
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_nexus_receiver_operations"
down_revision: str | Sequence[str] | None = "0012_merge_knowledge_and_mcp_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("nexus_receiver_operations"):
        return

    op.create_table(
        "nexus_receiver_operations",
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_sha256", sa.String(length=71), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("command_json", sa.JSON(), nullable=False),
        sa.Column("operation_json", sa.JSON(), nullable=False),
        sa.Column("execution_owner", sa.String(length=100), nullable=True),
        sa.Column("execution_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_nexus_receiver_operations_idempotency_key"),
    )
    op.create_index(
        "ix_nexus_receiver_operations_phase",
        "nexus_receiver_operations",
        ["phase"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_nexus_receiver_operations_phase", table_name="nexus_receiver_operations")
    op.drop_table("nexus_receiver_operations")
