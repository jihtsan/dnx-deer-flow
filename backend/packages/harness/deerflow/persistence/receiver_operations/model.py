from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ReceiverOperationRow(Base):
    """Durable receiver operation and its canonical request binding."""

    __tablename__ = "nexus_receiver_operations"

    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    command_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    operation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    execution_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    execution_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
