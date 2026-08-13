"""Add Nexus receiver GLOBAL catalog and fenced recovery coordination.

Revision ID: 0014_nexus_receiver_coordination
Revises: 0013_nexus_receiver_operations
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_nexus_receiver_coordination"
down_revision: str | Sequence[str] | None = "0013_nexus_receiver_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column

    safe_add_column(
        "nexus_receiver_operations",
        sa.Column("execution_token", sa.String(length=36), nullable=True),
    )
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "nexus_receiver_catalog_state" not in tables:
        op.create_table(
            "nexus_receiver_catalog_state",
            sa.Column("singleton_key", sa.Integer(), nullable=False),
            sa.Column("revision", sa.BigInteger(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("singleton_key = 1", name="ck_nexus_receiver_catalog_singleton"),
            sa.PrimaryKeyConstraint("singleton_key"),
        )
    if "nexus_receiver_global_skills" not in tables:
        op.create_table(
            "nexus_receiver_global_skills",
            sa.Column("runtime_skill_name", sa.String(length=64), nullable=False),
            sa.Column("skill_version_id", sa.String(length=255), nullable=False),
            sa.Column("version", sa.String(length=100), nullable=False),
            sa.Column("package_digest", sa.String(length=71), nullable=False),
            sa.Column("activated_revision", sa.BigInteger(), nullable=False),
            sa.Column("load_probe_succeeded", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("runtime_skill_name"),
            sa.UniqueConstraint("activated_revision", name="uq_nexus_receiver_global_skills_revision"),
        )
    if "nexus_receiver_recovery_lease" not in tables:
        op.create_table(
            "nexus_receiver_recovery_lease",
            sa.Column("singleton_key", sa.Integer(), nullable=False),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_token", sa.String(length=36), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("singleton_key = 1", name="ck_nexus_receiver_recovery_singleton"),
            sa.PrimaryKeyConstraint("singleton_key"),
        )


def downgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_drop_column

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "nexus_receiver_recovery_lease" in tables:
        op.drop_table("nexus_receiver_recovery_lease")
    if "nexus_receiver_global_skills" in tables:
        op.drop_table("nexus_receiver_global_skills")
    if "nexus_receiver_catalog_state" in tables:
        op.drop_table("nexus_receiver_catalog_state")
    safe_drop_column("nexus_receiver_operations", "execution_token")
