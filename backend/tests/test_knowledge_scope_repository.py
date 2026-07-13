from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.knowledge_scope import (
    KnowledgeScopeAlreadyExistsError,
    KnowledgeScopeRepository,
)
from deerflow.persistence.knowledge_scope.model import KnowledgeScopeRow


async def _exercise_contract(sf: async_sessionmaker[AsyncSession]) -> None:
    repo = KnowledgeScopeRepository(sf)
    created = await repo.create(
        scope_id="scope-stable",
        owner_user_id="alice",
        name="产品知识库",
        description="面向支持团队",
        enabled=True,
    )
    assert created["id"] == "scope-stable"
    assert created["owner_user_id"] == "alice"
    assert created["name"] == "产品知识库"
    assert created["description"] == "面向支持团队"
    assert created["enabled"] is True
    assert created["created_at"] is not None
    assert created["updated_at"] is not None

    assert await KnowledgeScopeRepository(sf).get_for_user("alice") == created

    updated = await repo.update_for_user("alice", name="客服知识库", description="退款与交付说明")
    assert updated is not None
    assert updated["name"] == "客服知识库"
    assert updated["description"] == "退款与交付说明"
    assert (await repo.update_for_user("alice", enabled=False))["enabled"] is False
    assert (await repo.update_for_user("alice", enabled=True))["enabled"] is True

    assert await repo.get_for_user("bob") is None
    assert await repo.update_for_user("bob", name="不可见") is None

    with pytest.raises(KnowledgeScopeAlreadyExistsError):
        await repo.create(
            scope_id="scope-second",
            owner_user_id="alice",
            name="重复",
            description="",
            enabled=True,
        )

    async with sf() as session:
        count = await session.scalar(select(func.count()).select_from(KnowledgeScopeRow))
    assert count == 1


@pytest_asyncio.fixture
async def sqlite_scope_sf(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sf
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_repository_contract(sqlite_scope_sf) -> None:
    await _exercise_contract(sqlite_scope_sf)


@pytest.mark.asyncio
async def test_sqlite_concurrent_create_keeps_one_scope(sqlite_scope_sf) -> None:
    repo = KnowledgeScopeRepository(sqlite_scope_sf)

    async def create(scope_id: str) -> str:
        try:
            await repo.create(
                scope_id=scope_id,
                owner_user_id="alice",
                name="统一知识库",
                description="",
                enabled=True,
            )
        except KnowledgeScopeAlreadyExistsError:
            return "conflict"
        return "created"

    assert sorted(await asyncio.gather(create("scope-a"), create("scope-b"))) == [
        "conflict",
        "created",
    ]
    async with sqlite_scope_sf() as session:
        count = await session.scalar(select(func.count()).select_from(KnowledgeScopeRow))
    assert count == 1


@pytest_asyncio.fixture
async def postgres_scope_sf() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.getenv("DEER_FLOW_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("DEER_FLOW_TEST_POSTGRES_URL is not configured")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeScopeRow.__table__.create, checkfirst=True)
        async with sf() as session:
            await session.execute(delete(KnowledgeScopeRow))
            await session.commit()
        yield sf
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(KnowledgeScopeRow.__table__.drop, checkfirst=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_repository_contract(postgres_scope_sf) -> None:
    await _exercise_contract(postgres_scope_sf)
