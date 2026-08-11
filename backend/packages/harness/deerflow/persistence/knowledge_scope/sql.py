from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.knowledge_scope.model import KnowledgeScopeRow
from deerflow.utils.time import coerce_iso


class KnowledgeScopeAlreadyExistsError(RuntimeError):
    """The database singleton constraint rejected a second scope."""


class KnowledgeScopeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _row_to_dict(row: KnowledgeScopeRow) -> dict[str, Any]:
        data = row.to_dict(exclude={"singleton_key"})
        for key in ("created_at", "updated_at"):
            if isinstance(data.get(key), datetime):
                data[key] = coerce_iso(data[key])
        return data

    async def create(
        self,
        *,
        scope_id: str,
        owner_user_id: str,
        name: str,
        description: str,
        enabled: bool,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = KnowledgeScopeRow(
            id=scope_id,
            singleton_key=1,
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise KnowledgeScopeAlreadyExistsError from exc
            await session.refresh(row)
            return self._row_to_dict(row)

    async def get_for_user(self, owner_user_id: str) -> dict[str, Any] | None:
        stmt = select(KnowledgeScopeRow).where(KnowledgeScopeRow.owner_user_id == owner_user_id)
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return self._row_to_dict(row) if row is not None else None

    async def update_for_user(
        self,
        owner_user_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        stmt = select(KnowledgeScopeRow).where(KnowledgeScopeRow.owner_user_id == owner_user_id)
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if enabled is not None:
                row.enabled = enabled
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return self._row_to_dict(row)
