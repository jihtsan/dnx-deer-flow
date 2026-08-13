from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ReceiverCatalogStateRow(Base):
    __tablename__ = "nexus_receiver_catalog_state"
    __table_args__ = (CheckConstraint("singleton_key = 1", name="ck_nexus_receiver_catalog_singleton"),)

    singleton_key: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReceiverGlobalSkillRow(Base):
    __tablename__ = "nexus_receiver_global_skills"

    runtime_skill_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    package_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    activated_revision: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    load_probe_succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReceiverRecoveryLeaseRow(Base):
    __tablename__ = "nexus_receiver_recovery_lease"
    __table_args__ = (CheckConstraint("singleton_key = 1", name="ck_nexus_receiver_recovery_singleton"),)

    singleton_key: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
