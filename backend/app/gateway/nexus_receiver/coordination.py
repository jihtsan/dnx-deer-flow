"""SQL coordination primitives for GLOBAL catalog and recovery ownership."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.receiver_catalog.model import ReceiverCatalogStateRow, ReceiverGlobalSkillRow, ReceiverRecoveryLeaseRow


@dataclass(frozen=True, slots=True)
class GlobalSkillCatalogEntry:
    runtime_skill_name: str
    skill_version_id: str
    version: str
    package_digest: str
    activated_revision: int

    @property
    def revision(self) -> int:
        return self.activated_revision


class _GlobalManagedStorage(Protocol):
    async def install_from_archive(
        self,
        archive_path: str | Path,
        *,
        receiver_metadata: dict[str, Any],
        precommit_scan: Callable[[Path, str], Awaitable[None]],
    ) -> dict[str, Any]: ...

    def receiver_metadata(self, name: str) -> dict[str, Any] | None: ...

    def load_probe(self, name: str): ...


class SqlReceiverCoordinationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _ensure_singleton(self, model: type[ReceiverCatalogStateRow] | type[ReceiverRecoveryLeaseRow]) -> None:
        async with self._session_factory() as session:
            if await session.get(model, 1) is not None:
                return
            session.add(model(singleton_key=1))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    @staticmethod
    def _catalog_entry(row: ReceiverGlobalSkillRow) -> GlobalSkillCatalogEntry:
        return GlobalSkillCatalogEntry(
            runtime_skill_name=row.runtime_skill_name,
            skill_version_id=row.skill_version_id,
            version=row.version,
            package_digest=row.package_digest,
            activated_revision=row.activated_revision,
        )

    async def get_catalog_revision(self) -> int:
        await self._ensure_singleton(ReceiverCatalogStateRow)
        async with self._session_factory() as session:
            row = await session.get(ReceiverCatalogStateRow, 1)
            assert row is not None
            return row.revision

    async def get_global_skill(self, runtime_skill_name: str) -> GlobalSkillCatalogEntry | None:
        async with self._session_factory() as session:
            row = await session.get(ReceiverGlobalSkillRow, runtime_skill_name)
            return self._catalog_entry(row) if row is not None else None

    async def commit_global_skill(
        self,
        *,
        runtime_skill_name: str,
        skill_version_id: str,
        version: str,
        package_digest: str,
        now: datetime,
        load_probe_succeeded: bool,
    ) -> GlobalSkillCatalogEntry:
        if not load_probe_succeeded:
            raise ValueError("GLOBAL catalog commit requires a successful load probe")
        await self._ensure_singleton(ReceiverCatalogStateRow)
        async with self._session_factory() as session:
            async with session.begin():
                state = (await session.execute(select(ReceiverCatalogStateRow).where(ReceiverCatalogStateRow.singleton_key == 1).with_for_update())).scalar_one()
                existing = await session.get(ReceiverGlobalSkillRow, runtime_skill_name)
                if existing is not None:
                    if existing.skill_version_id == skill_version_id and existing.version == version and existing.package_digest == package_digest and existing.load_probe_succeeded:
                        return self._catalog_entry(existing)
                    raise ValueError("GLOBAL runtime Skill name is already bound to another identity")
                state.revision += 1
                state.updated_at = now
                row = ReceiverGlobalSkillRow(
                    runtime_skill_name=runtime_skill_name,
                    skill_version_id=skill_version_id,
                    version=version,
                    package_digest=package_digest,
                    activated_revision=state.revision,
                    load_probe_succeeded=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            return self._catalog_entry(row)

    async def try_acquire_recovery_lease(
        self,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> str | None:
        if not owner or lease_duration <= timedelta(0):
            raise ValueError("recovery lease owner and duration must be valid")
        await self._ensure_singleton(ReceiverRecoveryLeaseRow)
        token = str(uuid4())
        statement = (
            update(ReceiverRecoveryLeaseRow)
            .where(
                ReceiverRecoveryLeaseRow.singleton_key == 1,
                or_(
                    ReceiverRecoveryLeaseRow.lease_token.is_(None),
                    ReceiverRecoveryLeaseRow.lease_expires_at.is_(None),
                    ReceiverRecoveryLeaseRow.lease_expires_at <= now,
                ),
            )
            .values(lease_owner=owner, lease_token=token, lease_expires_at=now + lease_duration, updated_at=now)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
            return token if result.rowcount == 1 else None

    async def renew_recovery_lease(
        self,
        *,
        owner: str,
        token: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        statement = (
            update(ReceiverRecoveryLeaseRow)
            .where(
                ReceiverRecoveryLeaseRow.singleton_key == 1,
                ReceiverRecoveryLeaseRow.lease_owner == owner,
                ReceiverRecoveryLeaseRow.lease_token == token,
                ReceiverRecoveryLeaseRow.lease_expires_at.is_not(None),
                ReceiverRecoveryLeaseRow.lease_expires_at > now,
            )
            .values(lease_expires_at=now + lease_duration, updated_at=now)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
            return result.rowcount == 1

    async def release_recovery_lease(self, *, owner: str, token: str) -> bool:
        statement = (
            update(ReceiverRecoveryLeaseRow)
            .where(
                ReceiverRecoveryLeaseRow.singleton_key == 1,
                ReceiverRecoveryLeaseRow.lease_owner == owner,
                ReceiverRecoveryLeaseRow.lease_token == token,
            )
            .values(lease_owner=None, lease_token=None, lease_expires_at=None)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            await session.commit()
            return result.rowcount == 1


class GlobalManagedSkillCommitter:
    """Recoverable filesystem + parser-probe + SQL catalog commit boundary."""

    def __init__(self, *, storage: _GlobalManagedStorage, catalog: SqlReceiverCoordinationStore) -> None:
        self._storage = storage
        self._catalog = catalog

    async def commit(
        self,
        archive_path: str | Path,
        *,
        receiver_metadata: dict[str, Any],
        precommit_scan: Callable[[Path, str], Awaitable[None]],
        now: datetime,
    ) -> GlobalSkillCatalogEntry:
        runtime_skill_name = receiver_metadata.get("runtimeSkillName")
        if not isinstance(runtime_skill_name, str):
            raise ValueError("GLOBAL receiver metadata requires runtimeSkillName")
        existing = self._storage.receiver_metadata(runtime_skill_name)
        if existing is None:
            await self._storage.install_from_archive(
                archive_path,
                receiver_metadata=receiver_metadata,
                precommit_scan=precommit_scan,
            )
            existing = self._storage.receiver_metadata(runtime_skill_name)
        if existing != receiver_metadata:
            raise ValueError("GLOBAL managed Skill tree is bound to another receiver identity")
        probe = self._storage.load_probe(runtime_skill_name)
        if probe is None or getattr(probe, "name", None) != runtime_skill_name:
            raise ValueError("GLOBAL managed Skill load probe failed")
        return await self._catalog.commit_global_skill(
            runtime_skill_name=runtime_skill_name,
            skill_version_id=str(receiver_metadata.get("skillVersionId") or ""),
            version=str(receiver_metadata.get("version") or ""),
            package_digest=str(receiver_metadata.get("packageDigest") or ""),
            now=now,
            load_probe_succeeded=True,
        )
