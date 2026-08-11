"""Reliable knowledge ingestion leases and retries.

Revision ID: 0006_reliable_knowledge_ingestion
Revises: 0005_knowledge_documents
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0006_reliable_knowledge_ingestion"
down_revision: str | Sequence[str] | None = "0005_knowledge_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "knowledge_ingestion_jobs"
_RETRY_REQUEST_TABLE = "knowledge_ingestion_retry_requests"


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _add_column_if_missing(column: sa.Column) -> None:
    if column.name not in _column_names():
        safe_add_column(_TABLE, column)


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return

    _add_column_if_missing(sa.Column("attempt_count", sa.Integer(), nullable=True))
    _add_column_if_missing(sa.Column("max_attempts", sa.Integer(), nullable=True))
    _add_column_if_missing(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(sa.Column("lease_owner", sa.String(length=128), nullable=True))
    _add_column_if_missing(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(sa.Column("last_error_code", sa.String(length=64), nullable=True))
    _add_column_if_missing(sa.Column("last_error_message", sa.Text(), nullable=True))
    _add_column_if_missing(sa.Column("last_manual_retry_key", sa.String(length=255), nullable=True))
    _add_column_if_missing(sa.Column("manual_retry_count", sa.Integer(), nullable=True))

    jobs = sa.table(
        _TABLE,
        sa.column("document_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("attempt_count", sa.Integer()),
        sa.column("max_attempts", sa.Integer()),
        sa.column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.column("lease_owner", sa.String()),
        sa.column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.column("last_error_code", sa.String()),
        sa.column("last_error_message", sa.Text()),
        sa.column("manual_retry_count", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        jobs.update().values(
            status=sa.case(
                (jobs.c.status.in_(("succeeded", "ready")), "succeeded"),
                (jobs.c.status.in_(("failed", "dead")), "dead"),
                (jobs.c.status == "cancelled", "cancelled"),
                else_="pending",
            ),
            attempt_count=sa.func.coalesce(jobs.c.attempt_count, 0),
            max_attempts=sa.func.coalesce(jobs.c.max_attempts, 5),
            next_attempt_at=sa.case(
                (
                    jobs.c.status.in_(("pending", "tracking", "indexing", "retry_wait", "leased")),
                    sa.func.coalesce(jobs.c.next_attempt_at, jobs.c.updated_at),
                ),
                else_=jobs.c.next_attempt_at,
            ),
            lease_owner=None,
            lease_expires_at=None,
            manual_retry_count=sa.func.coalesce(jobs.c.manual_retry_count, 0),
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE knowledge_ingestion_jobs
            SET last_error_code = (
                    SELECT failure_code FROM knowledge_documents
                    WHERE knowledge_documents.id = knowledge_ingestion_jobs.document_id
                ),
                last_error_message = (
                    SELECT failure_reason FROM knowledge_documents
                    WHERE knowledge_documents.id = knowledge_ingestion_jobs.document_id
                )
            WHERE status = 'dead'
            """
        )
    )

    inspector = sa.inspect(op.get_bind())
    checks = {constraint.get("name") for constraint in inspector.get_check_constraints(_TABLE)}
    indexes = {index["name"] for index in inspector.get_indexes(_TABLE)}
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.alter_column("attempt_count", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("max_attempts", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("manual_retry_count", existing_type=sa.Integer(), nullable=False)
        if "ck_knowledge_ingestion_jobs_status" not in checks:
            batch_op.create_check_constraint(
                "ck_knowledge_ingestion_jobs_status",
                "status IN ('pending', 'leased', 'retry_wait', 'succeeded', 'dead', 'cancelled')",
            )
        if "ck_knowledge_ingestion_jobs_attempt_count" not in checks:
            batch_op.create_check_constraint("ck_knowledge_ingestion_jobs_attempt_count", "attempt_count >= 0")
        if "ck_knowledge_ingestion_jobs_max_attempts" not in checks:
            batch_op.create_check_constraint("ck_knowledge_ingestion_jobs_max_attempts", "max_attempts > 0")
        if "ck_knowledge_ingestion_jobs_manual_retry_count" not in checks:
            batch_op.create_check_constraint(
                "ck_knowledge_ingestion_jobs_manual_retry_count",
                "manual_retry_count >= 0",
            )
        if "ix_knowledge_ingestion_jobs_claim" not in indexes:
            batch_op.create_index(
                "ix_knowledge_ingestion_jobs_claim",
                ["status", "next_attempt_at", "lease_expires_at"],
                unique=False,
            )

    if not sa.inspect(op.get_bind()).has_table(_RETRY_REQUEST_TABLE):
        op.create_table(
            _RETRY_REQUEST_TABLE,
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
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    if sa.inspect(op.get_bind()).has_table(_RETRY_REQUEST_TABLE):
        op.drop_table(_RETRY_REQUEST_TABLE)
    inspector = sa.inspect(op.get_bind())
    checks = {constraint.get("name") for constraint in inspector.get_check_constraints(_TABLE)}
    indexes = {index["name"] for index in inspector.get_indexes(_TABLE)}
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        if "ix_knowledge_ingestion_jobs_claim" in indexes:
            batch_op.drop_index("ix_knowledge_ingestion_jobs_claim")
        for constraint in (
            "ck_knowledge_ingestion_jobs_manual_retry_count",
            "ck_knowledge_ingestion_jobs_max_attempts",
            "ck_knowledge_ingestion_jobs_attempt_count",
            "ck_knowledge_ingestion_jobs_status",
        ):
            if constraint in checks:
                batch_op.drop_constraint(constraint, type_="check")
    jobs = sa.table(
        _TABLE,
        sa.column("status", sa.String()),
    )
    op.execute(
        jobs.update().values(
            status=sa.case(
                (jobs.c.status == "succeeded", "succeeded"),
                (jobs.c.status.in_(("dead", "cancelled")), "failed"),
                else_="pending",
            )
        )
    )
    for column_name in (
        "manual_retry_count",
        "last_manual_retry_key",
        "last_error_message",
        "last_error_code",
        "last_attempt_at",
        "lease_expires_at",
        "lease_owner",
        "next_attempt_at",
        "max_attempts",
        "attempt_count",
    ):
        safe_drop_column(_TABLE, column_name)
