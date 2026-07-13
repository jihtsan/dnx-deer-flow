from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.knowledge_documents import (
    KnowledgeDocumentQuotaExceededError,
    KnowledgeDocumentRepository,
    KnowledgeIdempotencyConflictError,
)
from deerflow.persistence.knowledge_documents.model import KnowledgeDocumentRow, KnowledgeIngestionJobRow
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository
from deerflow.persistence.knowledge_scope.model import KnowledgeScopeRow


async def _create_scope(sf: async_sessionmaker[AsyncSession], *, owner: str = "alice", enabled: bool = True) -> None:
    await KnowledgeScopeRepository(sf).create(
        scope_id="scope-stable",
        owner_user_id=owner,
        name="统一知识库",
        description="",
        enabled=enabled,
    )


async def _exercise_contract(sf: async_sessionmaker[AsyncSession]) -> None:
    await _create_scope(sf)
    repo = KnowledgeDocumentRepository(sf)

    created = await repo.create_document_with_job(
        document_id="doc-stable",
        job_id="job-stable",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-stable",
        original_filename="产品说明.txt",
        storage_name="doc-stable.txt",
        content_type="text/plain",
        size_bytes=12,
        content_sha256="a" * 64,
    )
    assert created.created is True
    assert created.document == {
        "id": "doc-stable",
        "scope_id": "scope-stable",
        "owner_user_id": "alice",
        "original_filename": "产品说明.txt",
        "storage_name": "doc-stable.txt",
        "content_type": "text/plain",
        "size_bytes": 12,
        "content_sha256": "a" * 64,
        "status": "pending",
        "lightrag_tracking_id": None,
        "failure_code": None,
        "failure_reason": None,
        "ingestion_job_id": "job-stable",
        "created_at": created.document["created_at"],
        "updated_at": created.document["updated_at"],
        "completed_at": None,
    }
    assert created.job["status"] == "pending"

    replay = await repo.create_document_with_job(
        document_id="doc-replayed",
        job_id="job-replayed",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-stable",
        original_filename="产品说明.txt",
        storage_name="doc-replayed.txt",
        content_type="text/plain",
        size_bytes=12,
        content_sha256="a" * 64,
    )
    assert replay.created is False
    assert replay.document["id"] == "doc-stable"
    assert replay.job["id"] == "job-stable"
    lookup = await repo.get_idempotent_result_for_user(
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-stable",
    )
    assert lookup is not None
    assert lookup.created is False
    assert lookup.document["id"] == "doc-stable"

    with pytest.raises(KnowledgeIdempotencyConflictError):
        await repo.create_document_with_job(
            document_id="doc-conflict",
            job_id="job-conflict",
            scope_id="scope-stable",
            owner_user_id="alice",
            idempotency_key="upload-stable",
            original_filename="不同.txt",
            storage_name="doc-conflict.txt",
            content_type="text/plain",
            size_bytes=9,
            content_sha256="b" * 64,
        )

    assert [item["id"] for item in await repo.list_for_user("alice")] == ["doc-stable"]
    assert await repo.list_for_user("bob") == []
    assert await repo.document_stats_for_user("alice") == {
        "total": 1,
        "pending": 1,
        "indexing": 0,
        "ready": 0,
        "failed": 0,
    }

    await repo.set_remote_tracking(job_id="job-stable", tracking_id="upload-remote")
    indexing = await repo.set_document_status(job_id="job-stable", status="indexing")
    assert indexing is not None
    assert indexing["lightrag_tracking_id"] == "upload-remote"
    assert indexing["status"] == "indexing"
    assert await repo.list_ready_candidates_for_user("alice") == []

    ready = await repo.set_document_status(job_id="job-stable", status="ready")
    assert ready is not None
    assert ready["completed_at"] is not None
    assert [item["id"] for item in await repo.list_ready_candidates_for_user("alice")] == ["doc-stable"]

    await repo.create_document_with_job(
        document_id="doc-failed",
        job_id="job-failed",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-failed",
        original_filename="失败.txt",
        storage_name="doc-failed.txt",
        content_type="text/plain",
        size_bytes=8,
        content_sha256="c" * 64,
    )
    failed = await repo.set_document_status(
        job_id="job-failed",
        status="failed",
        failure_code="lightrag_processing_failed",
        failure_reason="LightRAG 处理文档失败。",
    )
    assert failed is not None
    assert failed["failure_code"] == "lightrag_processing_failed"
    assert failed["failure_reason"] == "LightRAG 处理文档失败。"
    assert await repo.document_stats_for_user("alice") == {
        "total": 2,
        "pending": 0,
        "indexing": 0,
        "ready": 1,
        "failed": 1,
    }

    await KnowledgeScopeRepository(sf).update_for_user("alice", enabled=False)
    assert await repo.list_ready_candidates_for_user("alice") == []

    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeDocumentRow)) == 2
        assert await session.scalar(select(func.count()).select_from(KnowledgeIngestionJobRow)) == 2


@pytest_asyncio.fixture
async def sqlite_document_sf(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge-documents.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sf
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_repository_contract(sqlite_document_sf) -> None:
    await _exercise_contract(sqlite_document_sf)


@pytest.mark.asyncio
async def test_document_and_job_roll_back_together(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)

    with patch.object(AsyncSession, "commit", side_effect=RuntimeError("commit failed")):
        with pytest.raises(RuntimeError, match="commit failed"):
            await repo.create_document_with_job(
                document_id="doc-rollback",
                job_id="job-rollback",
                scope_id="scope-stable",
                owner_user_id="alice",
                idempotency_key="upload-rollback",
                original_filename="回滚.txt",
                storage_name="doc-rollback.txt",
                content_type="text/plain",
                size_bytes=8,
                content_sha256="d" * 64,
            )

    async with sqlite_document_sf() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeDocumentRow)) == 0
        assert await session.scalar(select(func.count()).select_from(KnowledgeIngestionJobRow)) == 0


@pytest.mark.asyncio
async def test_document_quota_is_checked_inside_the_creation_transaction(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-first",
        job_id="job-first",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-first",
        original_filename="first.txt",
        storage_name="doc-first.txt",
        content_type="text/plain",
        size_bytes=4,
        content_sha256="a" * 64,
        max_total_size_bytes=6,
    )

    with pytest.raises(KnowledgeDocumentQuotaExceededError):
        await repo.create_document_with_job(
            document_id="doc-second",
            job_id="job-second",
            scope_id="scope-stable",
            owner_user_id="alice",
            idempotency_key="upload-second",
            original_filename="second.txt",
            storage_name="doc-second.txt",
            content_type="text/plain",
            size_bytes=3,
            content_sha256="b" * 64,
            max_total_size_bytes=6,
        )

    assert await repo.document_stats_for_user("alice") == {
        "total": 1,
        "pending": 1,
        "indexing": 0,
        "ready": 0,
        "failed": 0,
    }


@pytest.mark.asyncio
async def test_raced_idempotent_replay_wins_before_quota_check(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    existing = await repo.create_document_with_job(
        document_id="doc-first",
        job_id="job-first",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-first",
        original_filename="first.txt",
        storage_name="doc-first.txt",
        content_type="text/plain",
        size_bytes=4,
        content_sha256="a" * 64,
        max_total_size_bytes=4,
    )
    existing = replace(existing, created=False)

    with patch.object(
        repo,
        "_existing_result",
        new=AsyncMock(side_effect=[None, existing]),
    ):
        replay = await repo.create_document_with_job(
            document_id="doc-raced",
            job_id="job-raced",
            scope_id="scope-stable",
            owner_user_id="alice",
            idempotency_key="upload-first",
            original_filename="first.txt",
            storage_name="doc-raced.txt",
            content_type="text/plain",
            size_bytes=4,
            content_sha256="a" * 64,
            max_total_size_bytes=4,
        )

    assert replay.created is False
    assert replay.document["id"] == "doc-first"
    assert replay.job["id"] == "job-first"


@pytest_asyncio.fixture
async def postgres_document_sf() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.getenv("DEER_FLOW_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("DEER_FLOW_TEST_POSTGRES_URL is not configured")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    tables = [KnowledgeScopeRow.__table__, KnowledgeDocumentRow.__table__, KnowledgeIngestionJobRow.__table__]
    try:
        async with engine.begin() as connection:
            for table in tables:
                await connection.run_sync(table.create, checkfirst=True)
        async with sf() as session:
            await session.execute(delete(KnowledgeIngestionJobRow))
            await session.execute(delete(KnowledgeDocumentRow))
            await session.execute(delete(KnowledgeScopeRow))
            await session.commit()
        yield sf
    finally:
        async with engine.begin() as connection:
            for table in reversed(tables):
                await connection.run_sync(table.drop, checkfirst=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_repository_contract(postgres_document_sf) -> None:
    await _exercise_contract(postgres_document_sf)
