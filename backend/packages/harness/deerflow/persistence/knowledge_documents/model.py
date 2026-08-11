from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class KnowledgeDirectoryRow(Base):
    __tablename__ = "knowledge_directories"
    __table_args__ = (
        Index(
            "uq_knowledge_directories_root_name",
            "scope_id",
            "normalized_name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_knowledge_directories_child_name",
            "scope_id",
            "parent_id",
            "normalized_name",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("knowledge_directories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeDocumentRow(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_knowledge_documents_size_bytes"),
        CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed')",
            name="ck_knowledge_documents_status",
        ),
        UniqueConstraint(
            "scope_id",
            "idempotency_key",
            name="uq_knowledge_documents_scope_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    directory_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("knowledge_directories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    lightrag_tracking_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lightrag_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lightrag_chunks_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lightrag_stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingestion_job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeIngestionJobRow(Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "scope_id",
            "idempotency_key",
            name="uq_knowledge_ingestion_jobs_scope_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'retry_wait', 'succeeded', 'dead', 'cancelled')",
            name="ck_knowledge_ingestion_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_knowledge_ingestion_jobs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_knowledge_ingestion_jobs_max_attempts"),
        CheckConstraint("manual_retry_count >= 0", name="ck_knowledge_ingestion_jobs_manual_retry_count"),
        Index(
            "ix_knowledge_ingestion_jobs_claim",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    scope_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_manual_retry_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manual_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeIngestionRetryRequestRow(Base):
    """Durable deduplication ledger for accepted manual retry requests."""

    __tablename__ = "knowledge_ingestion_retry_requests"

    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    retry_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class KnowledgeRemoteDocumentRow(Base):
    """Persisted metadata mirror for documents created directly in LightRAG."""

    __tablename__ = "knowledge_remote_documents"
    __table_args__ = (
        UniqueConstraint(
            "scope_id",
            "remote_document_id",
            name="uq_knowledge_remote_documents_scope_remote",
        ),
        CheckConstraint("content_length >= 0", name="ck_knowledge_remote_documents_content_length"),
        CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed')",
            name="ck_knowledge_remote_documents_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    directory_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("knowledge_directories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    remote_document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    content_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    lightrag_tracking_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    lightrag_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lightrag_chunks_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lightrag_stage_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
