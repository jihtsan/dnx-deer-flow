from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    KnowledgeJobRetryConflictError,
    KnowledgeJobRetryNotAllowedError,
)
from deerflow.persistence.knowledge_documents.model import (
    KnowledgeDocumentRow,
    KnowledgeIngestionJobRow,
    KnowledgeIngestionRetryRequestRow,
)
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
async def test_atomic_claim_grants_only_one_valid_lease(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-claim",
        job_id="job-claim",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-claim",
        original_filename="claim.txt",
        storage_name="doc-claim.txt",
        content_type="text/plain",
        size_bytes=5,
        content_sha256="a" * 64,
    )
    now = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)

    claims = await asyncio.gather(
        repo.claim_next_job(now=now, lease_owner="worker-a", lease_duration=timedelta(seconds=30)),
        repo.claim_next_job(now=now, lease_owner="worker-b", lease_duration=timedelta(seconds=30)),
    )

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].job["status"] == "leased"
    assert claimed[0].job["attempt_count"] == 1
    assert claimed[0].job["lease_owner"] in {"worker-a", "worker-b"}
    assert claimed[0].job["lease_expires_at"] == (now + timedelta(seconds=30)).isoformat()
    assert (
        await repo.claim_next_job(
            now=now + timedelta(seconds=29),
            lease_owner="worker-c",
            lease_duration=timedelta(seconds=30),
        )
        is None
    )


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_stale_owner_is_fenced(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-recover",
        job_id="job-recover",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-recover",
        original_filename="recover.txt",
        storage_name="doc-recover.txt",
        content_type="text/plain",
        size_bytes=7,
        content_sha256="b" * 64,
    )
    now = datetime(2026, 7, 13, 5, 0, tzinfo=UTC)
    first = await repo.claim_next_job(
        now=now,
        lease_owner="crashed-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert first is not None

    recovered = await repo.claim_next_job(
        now=now + timedelta(seconds=30),
        lease_owner="restarted-worker",
        lease_duration=timedelta(seconds=30),
    )
    assert recovered is not None
    assert recovered.job["attempt_count"] == 2

    assert (
        await repo.complete_job(
            job_id="job-recover",
            lease_owner="crashed-worker",
            attempt_count=1,
            now=now + timedelta(seconds=31),
        )
        is None
    )
    completed = await repo.complete_job(
        job_id="job-recover",
        lease_owner="restarted-worker",
        attempt_count=2,
        now=now + timedelta(seconds=31),
    )
    assert completed is not None
    assert completed.job["status"] == "succeeded"
    assert completed.document["status"] == "ready"
    assert (
        await repo.claim_next_job(
            now=now + timedelta(minutes=5),
            lease_owner="late-worker",
            lease_duration=timedelta(seconds=30),
        )
        is None
    )


@pytest.mark.asyncio
async def test_transient_failures_back_off_and_stop_at_max_attempts(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-backoff",
        job_id="job-backoff",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-backoff",
        original_filename="backoff.txt",
        storage_name="doc-backoff.txt",
        content_type="text/plain",
        size_bytes=7,
        content_sha256="c" * 64,
        max_attempts=3,
    )
    started_at = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)

    for attempt, delay in ((1, 5), (2, 10)):
        claimed = await repo.claim_next_job(
            now=started_at,
            lease_owner="worker",
            lease_duration=timedelta(minutes=1),
        )
        assert claimed is not None
        assert claimed.job["attempt_count"] == attempt
        retrying = await repo.record_job_failure(
            job_id="job-backoff",
            lease_owner="worker",
            attempt_count=attempt,
            now=started_at,
            error_code="lightrag_timeout",
            error_message="知识服务响应超时。",
            retryable=True,
            base_delay=timedelta(seconds=5),
            max_delay=timedelta(minutes=1),
        )
        assert retrying is not None
        assert retrying.job["status"] == "retry_wait"
        assert retrying.job["next_attempt_at"] == (started_at + timedelta(seconds=delay)).isoformat()
        assert retrying.document["status"] in {"pending", "indexing"}
        assert (
            await repo.claim_next_job(
                now=started_at + timedelta(seconds=delay - 1),
                lease_owner="too-early",
                lease_duration=timedelta(minutes=1),
            )
            is None
        )
        started_at += timedelta(seconds=delay)

    final_claim = await repo.claim_next_job(
        now=started_at,
        lease_owner="worker",
        lease_duration=timedelta(minutes=1),
    )
    assert final_claim is not None
    exhausted = await repo.record_job_failure(
        job_id="job-backoff",
        lease_owner="worker",
        attempt_count=3,
        now=started_at,
        error_code="lightrag_timeout",
        error_message="知识服务响应超时。",
        retryable=True,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert exhausted is not None
    assert exhausted.job["status"] == "dead"
    assert exhausted.job["next_attempt_at"] is None
    assert exhausted.document["status"] == "failed"
    assert (
        await repo.claim_next_job(
            now=started_at + timedelta(days=1),
            lease_owner="never",
            lease_duration=timedelta(minutes=1),
        )
        is None
    )


@pytest.mark.asyncio
async def test_expired_final_attempt_is_reaped_to_dead(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-final-crash",
        job_id="job-final-crash",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-final-crash",
        original_filename="final-crash.txt",
        storage_name="doc-final-crash.txt",
        content_type="text/plain",
        size_bytes=5,
        content_sha256="e" * 64,
        max_attempts=1,
    )
    now = datetime(2026, 7, 13, 6, 30, tzinfo=UTC)
    assert (
        await repo.claim_next_job(
            now=now,
            lease_owner="crashed-final-worker",
            lease_duration=timedelta(seconds=30),
        )
        is not None
    )

    assert await repo.reap_exhausted_jobs(now=now + timedelta(seconds=30)) == 1
    state = await repo.get_ingestion_state_for_user(document_id="doc-final-crash", owner_user_id="alice")
    assert state is not None
    assert state.job["status"] == "dead"
    assert state.job["last_error_code"] == "ingestion_lease_expired"
    assert state.document["status"] == "failed"
    assert state.document["failure_reason"] == "入库任务在最后一次尝试中断后未能恢复。"


@pytest.mark.asyncio
async def test_permanent_failure_is_dead_without_another_claim(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    await repo.create_document_with_job(
        document_id="doc-permanent",
        job_id="job-permanent",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-permanent",
        original_filename="permanent.txt",
        storage_name="doc-permanent.txt",
        content_type="text/plain",
        size_bytes=9,
        content_sha256="d" * 64,
    )
    now = datetime(2026, 7, 13, 7, 0, tzinfo=UTC)
    claim = await repo.claim_next_job(now=now, lease_owner="worker", lease_duration=timedelta(minutes=1))
    assert claim is not None

    failed = await repo.record_job_failure(
        job_id="job-permanent",
        lease_owner="worker",
        attempt_count=1,
        now=now,
        error_code="permanent_file_error",
        error_message="文件无法被知识服务处理。",
        retryable=False,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert failed is not None
    assert failed.job["status"] == "dead"
    assert failed.document["failure_code"] == "permanent_file_error"


@pytest.mark.asyncio
async def test_manual_retry_is_idempotent_and_never_reactivates_cancelled_or_succeeded(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)

    for suffix in ("dead", "cancelled", "succeeded"):
        await repo.create_document_with_job(
            document_id=f"doc-{suffix}",
            job_id=f"job-{suffix}",
            scope_id="scope-stable",
            owner_user_id="alice",
            idempotency_key=f"upload-{suffix}",
            original_filename=f"{suffix}.txt",
            storage_name=f"doc-{suffix}.txt",
            content_type="text/plain",
            size_bytes=4,
            content_sha256=suffix[0] * 64,
            max_attempts=1,
        )

    dead_claim = await repo.claim_next_job(now=now, lease_owner="dead-worker", lease_duration=timedelta(minutes=1))
    assert dead_claim is not None
    await repo.record_job_failure(
        job_id=dead_claim.job["id"],
        lease_owner="dead-worker",
        attempt_count=1,
        now=now,
        error_code="lightrag_timeout",
        error_message="知识服务响应超时。",
        retryable=True,
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=1),
    )

    retry_key = "retry-doc-dead-1"
    retries = await asyncio.gather(
        repo.retry_failed_document(document_id=dead_claim.document["id"], owner_user_id="alice", retry_key=retry_key, now=now),
        repo.retry_failed_document(document_id=dead_claim.document["id"], owner_user_id="alice", retry_key=retry_key, now=now),
    )
    assert sorted(result.activated for result in retries) == [False, True]
    assert all(result.job["status"] == "pending" for result in retries)
    assert all(result.job["manual_retry_count"] == 1 for result in retries)
    with pytest.raises(KnowledgeJobRetryConflictError):
        await repo.retry_failed_document(
            document_id=dead_claim.document["id"],
            owner_user_id="alice",
            retry_key="retry-doc-dead-2",
            now=now,
        )
    await repo.cancel_job(job_id=dead_claim.job["id"], now=now)

    cancelled_claim = await repo.claim_next_job(now=now, lease_owner="cancel-worker", lease_duration=timedelta(minutes=1))
    assert cancelled_claim is not None
    cancelled = await repo.cancel_job(job_id=cancelled_claim.job["id"], now=now)
    assert cancelled is not None
    assert cancelled.job["status"] == "cancelled"
    with pytest.raises(KnowledgeJobRetryNotAllowedError):
        await repo.retry_failed_document(document_id=cancelled.document["id"], owner_user_id="alice", retry_key="retry-cancelled", now=now)

    succeeded_claim = await repo.claim_next_job(now=now, lease_owner="success-worker", lease_duration=timedelta(minutes=1))
    assert succeeded_claim is not None
    succeeded = await repo.complete_job(
        job_id=succeeded_claim.job["id"],
        lease_owner="success-worker",
        attempt_count=1,
        now=now,
    )
    assert succeeded is not None
    with pytest.raises(KnowledgeJobRetryNotAllowedError):
        await repo.retry_failed_document(document_id=succeeded.document["id"], owner_user_id="alice", retry_key="retry-succeeded", now=now)


@pytest.mark.asyncio
async def test_manual_retry_remembers_older_keys_across_multiple_failed_retry_rounds(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    await repo.create_document_with_job(
        document_id="doc-retry-ledger",
        job_id="job-retry-ledger",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-retry-ledger",
        original_filename="retry-ledger.txt",
        storage_name="doc-retry-ledger.txt",
        content_type="text/plain",
        size_bytes=4,
        content_sha256="a" * 64,
        max_attempts=1,
    )

    claim = await repo.claim_next_job(now=now, lease_owner="initial-worker", lease_duration=timedelta(minutes=1))
    assert claim is not None
    failed = await repo.record_job_failure(
        job_id=claim.job["id"],
        lease_owner="initial-worker",
        attempt_count=1,
        now=now,
        error_code="lightrag_timeout",
        error_message="LightRAG 响应超时。",
        retryable=True,
        base_delay=timedelta(seconds=1),
        max_delay=timedelta(seconds=1),
    )
    assert failed is not None and failed.job["status"] == "dead"

    for round_number, retry_key in enumerate(("retry-ledger-a", "retry-ledger-b"), start=1):
        retry = await repo.retry_failed_document(
            document_id="doc-retry-ledger",
            owner_user_id="alice",
            retry_key=retry_key,
            now=now + timedelta(minutes=round_number),
        )
        assert retry.activated is True
        claim = await repo.claim_next_job(
            now=now + timedelta(minutes=round_number),
            lease_owner=f"retry-worker-{round_number}",
            lease_duration=timedelta(minutes=1),
        )
        assert claim is not None
        failed = await repo.record_job_failure(
            job_id=claim.job["id"],
            lease_owner=f"retry-worker-{round_number}",
            attempt_count=1,
            now=now + timedelta(minutes=round_number),
            error_code="lightrag_timeout",
            error_message="LightRAG 响应超时。",
            retryable=True,
            base_delay=timedelta(seconds=1),
            max_delay=timedelta(seconds=1),
        )
        assert failed is not None and failed.job["status"] == "dead"

    delayed_replay = await repo.retry_failed_document(
        document_id="doc-retry-ledger",
        owner_user_id="alice",
        retry_key="retry-ledger-a",
        now=now + timedelta(minutes=3),
    )
    assert delayed_replay.activated is False
    assert delayed_replay.job["status"] == "dead"
    assert delayed_replay.job["manual_retry_count"] == 2


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
async def test_legacy_status_helpers_cannot_overwrite_a_terminal_job(sqlite_document_sf) -> None:
    await _create_scope(sqlite_document_sf)
    repo = KnowledgeDocumentRepository(sqlite_document_sf)
    created = await repo.create_document_with_job(
        document_id="doc-legacy-fence",
        job_id="job-legacy-fence",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-legacy-fence",
        original_filename="legacy.txt",
        storage_name="doc-legacy-fence.txt",
        content_type="text/plain",
        size_bytes=6,
        content_sha256="e" * 64,
    )

    await repo.set_remote_tracking(job_id=created.job["id"], tracking_id="remote-legacy")
    completed = await repo.set_document_status(job_id=created.job["id"], status="ready")
    assert completed is not None

    assert (
        await repo.set_document_status(
            job_id=created.job["id"],
            status="failed",
            failure_code="stale_failure",
            failure_reason="stale failure",
        )
        is None
    )
    persisted = await repo.get_ingestion_state_for_user(
        document_id=created.document["id"],
        owner_user_id="alice",
    )
    assert persisted is not None
    assert persisted.job["status"] == "succeeded"
    assert persisted.document["status"] == "ready"


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
    tables = [
        KnowledgeScopeRow.__table__,
        KnowledgeDocumentRow.__table__,
        KnowledgeIngestionJobRow.__table__,
        KnowledgeIngestionRetryRequestRow.__table__,
    ]
    try:
        async with engine.begin() as connection:
            for table in tables:
                await connection.run_sync(table.create, checkfirst=True)
        async with sf() as session:
            await session.execute(delete(KnowledgeIngestionRetryRequestRow))
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


@pytest.mark.asyncio
async def test_postgresql_reliable_ingestion_job_contract(postgres_document_sf) -> None:
    await _create_scope(postgres_document_sf)
    repo = KnowledgeDocumentRepository(postgres_document_sf)
    await repo.create_document_with_job(
        document_id="doc-pg-reliable",
        job_id="job-pg-reliable",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-pg-reliable",
        original_filename="postgres.txt",
        storage_name="doc-pg-reliable.txt",
        content_type="text/plain",
        size_bytes=8,
        content_sha256="f" * 64,
        max_attempts=3,
    )
    now = datetime(2026, 7, 13, 11, 0, tzinfo=UTC)
    claims = await asyncio.gather(
        repo.claim_next_job(now=now, lease_owner="pg-a", lease_duration=timedelta(seconds=30)),
        repo.claim_next_job(now=now, lease_owner="pg-b", lease_duration=timedelta(seconds=30)),
    )
    first = next(claim for claim in claims if claim is not None)
    assert len([claim for claim in claims if claim is not None]) == 1
    assert (
        await repo.claim_next_job(
            now=now + timedelta(seconds=29),
            lease_owner="pg-c",
            lease_duration=timedelta(seconds=30),
        )
        is None
    )

    second = await repo.claim_next_job(
        now=now + timedelta(seconds=30),
        lease_owner="pg-restarted",
        lease_duration=timedelta(seconds=30),
    )
    assert second is not None and second.job["attempt_count"] == 2
    assert (
        await repo.complete_job(
            job_id=first.job["id"],
            lease_owner=first.job["lease_owner"],
            attempt_count=1,
            now=now + timedelta(seconds=31),
        )
        is None
    )
    retrying = await repo.record_job_failure(
        job_id=second.job["id"],
        lease_owner="pg-restarted",
        attempt_count=2,
        now=now + timedelta(seconds=31),
        error_code="lightrag_timeout",
        error_message="LightRAG 响应超时。",
        retryable=True,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert retrying is not None and retrying.job["status"] == "retry_wait"
    assert (
        await repo.claim_next_job(
            now=now + timedelta(seconds=40),
            lease_owner="pg-early",
            lease_duration=timedelta(seconds=30),
        )
        is None
    )
    final_claim = await repo.claim_next_job(
        now=now + timedelta(seconds=41),
        lease_owner="pg-final",
        lease_duration=timedelta(seconds=30),
    )
    assert final_claim is not None and final_claim.job["attempt_count"] == 3
    dead = await repo.record_job_failure(
        job_id=final_claim.job["id"],
        lease_owner="pg-final",
        attempt_count=3,
        now=now + timedelta(seconds=42),
        error_code="lightrag_timeout",
        error_message="LightRAG 响应超时。",
        retryable=True,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert dead is not None and dead.job["status"] == "dead"

    retry = await repo.retry_failed_document(
        document_id="doc-pg-reliable",
        owner_user_id="alice",
        retry_key="retry-pg-1",
        now=now + timedelta(seconds=43),
    )
    replay = await repo.retry_failed_document(
        document_id="doc-pg-reliable",
        owner_user_id="alice",
        retry_key="retry-pg-1",
        now=now + timedelta(seconds=43),
    )
    assert retry.activated is True and replay.activated is False
    with pytest.raises(KnowledgeJobRetryConflictError):
        await repo.retry_failed_document(
            document_id="doc-pg-reliable",
            owner_user_id="alice",
            retry_key="retry-pg-2",
            now=now + timedelta(seconds=43),
        )
    retry_claim = await repo.claim_next_job(
        now=now + timedelta(seconds=44),
        lease_owner="pg-manual-a",
        lease_duration=timedelta(seconds=30),
    )
    assert retry_claim is not None
    failed_again = await repo.record_job_failure(
        job_id=retry_claim.job["id"],
        lease_owner="pg-manual-a",
        attempt_count=1,
        now=now + timedelta(seconds=44),
        error_code="lightrag_timeout",
        error_message="LightRAG 响应超时。",
        retryable=False,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert failed_again is not None and failed_again.job["status"] == "dead"
    second_retry = await repo.retry_failed_document(
        document_id="doc-pg-reliable",
        owner_user_id="alice",
        retry_key="retry-pg-2",
        now=now + timedelta(seconds=45),
    )
    assert second_retry.activated is True
    second_retry_claim = await repo.claim_next_job(
        now=now + timedelta(seconds=46),
        lease_owner="pg-manual-b",
        lease_duration=timedelta(seconds=30),
    )
    assert second_retry_claim is not None
    failed_third_time = await repo.record_job_failure(
        job_id=second_retry_claim.job["id"],
        lease_owner="pg-manual-b",
        attempt_count=1,
        now=now + timedelta(seconds=46),
        error_code="lightrag_timeout",
        error_message="LightRAG 响应超时。",
        retryable=False,
        base_delay=timedelta(seconds=5),
        max_delay=timedelta(minutes=1),
    )
    assert failed_third_time is not None and failed_third_time.job["status"] == "dead"
    delayed_replay = await repo.retry_failed_document(
        document_id="doc-pg-reliable",
        owner_user_id="alice",
        retry_key="retry-pg-1",
        now=now + timedelta(seconds=47),
    )
    assert delayed_replay.activated is False
    assert delayed_replay.job["status"] == "dead"
    assert delayed_replay.job["manual_retry_count"] == 2
    cancelled = await repo.cancel_job(job_id="job-pg-reliable", now=now + timedelta(seconds=48))
    assert cancelled is not None and cancelled.job["status"] == "cancelled"
    with pytest.raises(KnowledgeJobRetryNotAllowedError):
        await repo.retry_failed_document(
            document_id="doc-pg-reliable",
            owner_user_id="alice",
            retry_key="retry-pg-cancelled",
            now=now + timedelta(seconds=49),
        )
