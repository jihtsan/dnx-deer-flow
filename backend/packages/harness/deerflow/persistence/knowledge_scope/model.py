from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class KnowledgeScopeRow(Base):
    __tablename__ = "knowledge_scopes"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_knowledge_scopes_singleton_key"),
        UniqueConstraint("singleton_key", name="uq_knowledge_scopes_singleton_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    singleton_key: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
