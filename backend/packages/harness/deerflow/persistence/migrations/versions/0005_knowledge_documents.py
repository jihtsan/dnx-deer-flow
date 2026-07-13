"""Knowledge documents and ingestion jobs.

Revision ID: 0005_knowledge_documents
Revises: 0004_knowledge_scope
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_knowledge_documents"
down_revision: str | Sequence[str] | None = "0004_knowledge_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("knowledge_documents"):
        op.create_table(
            "knowledge_documents",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("scope_id", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("storage_name", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=255), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("lightrag_tracking_id", sa.String(length=255), nullable=True),
            sa.Column("failure_code", sa.String(length=64), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("ingestion_job_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("size_bytes >= 0", name="ck_knowledge_documents_size_bytes"),
            sa.CheckConstraint(
                "status IN ('pending', 'indexing', 'ready', 'failed')",
                name="ck_knowledge_documents_status",
            ),
            sa.ForeignKeyConstraint(["scope_id"], ["knowledge_scopes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ingestion_job_id"),
            sa.UniqueConstraint("storage_name"),
            sa.UniqueConstraint(
                "scope_id",
                "idempotency_key",
                name="uq_knowledge_documents_scope_idempotency",
            ),
        )
        with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
            batch_op.create_index("ix_knowledge_documents_owner_user_id", ["owner_user_id"], unique=False)
            batch_op.create_index("ix_knowledge_documents_scope_id", ["scope_id"], unique=False)
            batch_op.create_index("ix_knowledge_documents_status", ["status"], unique=False)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("knowledge_ingestion_jobs"):
        op.create_table(
            "knowledge_ingestion_jobs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("document_id", sa.String(length=64), nullable=False),
            sa.Column("scope_id", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["scope_id"], ["knowledge_scopes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("document_id"),
            sa.UniqueConstraint(
                "scope_id",
                "idempotency_key",
                name="uq_knowledge_ingestion_jobs_scope_idempotency",
            ),
        )
        with op.batch_alter_table("knowledge_ingestion_jobs", schema=None) as batch_op:
            batch_op.create_index("ix_knowledge_ingestion_jobs_document_id", ["document_id"], unique=False)
            batch_op.create_index("ix_knowledge_ingestion_jobs_scope_id", ["scope_id"], unique=False)
            batch_op.create_index("ix_knowledge_ingestion_jobs_status", ["status"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_ingestion_jobs"):
        with op.batch_alter_table("knowledge_ingestion_jobs", schema=None) as batch_op:
            batch_op.drop_index("ix_knowledge_ingestion_jobs_status")
            batch_op.drop_index("ix_knowledge_ingestion_jobs_scope_id")
            batch_op.drop_index("ix_knowledge_ingestion_jobs_document_id")
        op.drop_table("knowledge_ingestion_jobs")

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_documents"):
        with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
            batch_op.drop_index("ix_knowledge_documents_status")
            batch_op.drop_index("ix_knowledge_documents_scope_id")
            batch_op.drop_index("ix_knowledge_documents_owner_user_id")
        op.drop_table("knowledge_documents")
