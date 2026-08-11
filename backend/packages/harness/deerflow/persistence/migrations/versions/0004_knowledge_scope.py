"""single Knowledge Scope.

Revision ID: 0004_knowledge_scope
Revises: 0003_scheduled_tasks
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_knowledge_scope"
down_revision: str | Sequence[str] | None = "0003_scheduled_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("knowledge_scopes"):
        return

    op.create_table(
        "knowledge_scopes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("singleton_key", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("singleton_key = 1", name="ck_knowledge_scopes_singleton_key"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key", name="uq_knowledge_scopes_singleton_key"),
    )
    with op.batch_alter_table("knowledge_scopes", schema=None) as batch_op:
        batch_op.create_index("ix_knowledge_scopes_owner_user_id", ["owner_user_id"], unique=False)


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("knowledge_scopes"):
        return
    with op.batch_alter_table("knowledge_scopes", schema=None) as batch_op:
        batch_op.drop_index("ix_knowledge_scopes_owner_user_id")
    op.drop_table("knowledge_scopes")
