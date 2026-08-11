from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.knowledge.ingestion import (
    KnowledgeIngestionService,
    map_lightrag_tracking_status,
)
from app.knowledge.lightrag import (
    LightRAGConflictError,
    LightRAGContractError,
    LightRAGOfflineError,
    LightRAGRemoteDocument,
    LightRAGTrackStatus,
    LightRAGUpload,
)
from app.knowledge.storage import KnowledgeFileStore
from deerflow.persistence.base import Base
from deerflow.persistence.knowledge_documents import KnowledgeDocumentRepository
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository


def _tracking(*statuses: str) -> LightRAGTrackStatus:
    return LightRAGTrackStatus(
        track_id="upload-remote",
        documents=tuple({"status": status} for status in statuses),
        total_count=len(statuses),
        status_summary={},
    )


@pytest.mark.parametrize(
    ("remote_status", "local_status"),
    [
        ("pending", "pending"),
        ("parsing", "indexing"),
        ("analyzing", "indexing"),
        ("processing", "indexing"),
        ("preprocessed", "indexing"),
        ("processed", "ready"),
        ("failed", "failed"),
    ],
)
def test_lightrag_statuses_map_to_the_closed_deerflow_state_machine(
    remote_status: str,
    local_status: str,
) -> None:
    assert map_lightrag_tracking_status(_tracking(remote_status)).status == local_status


def test_lightrag_status_mapping_preserves_real_stage_and_chunk_count() -> None:
    tracking = LightRAGTrackStatus(
        track_id="upload-remote",
        documents=(
            {
                "id": "remote-document",
                "status": "processing",
                "chunks_count": 5,
                "updated_at": "2026-07-15T01:02:00Z",
            },
        ),
        total_count=1,
        status_summary={"processing": 1},
    )

    mapped = map_lightrag_tracking_status(tracking)

    assert mapped.status == "indexing"
    assert mapped.stage == "processing"
    assert mapped.chunks_count == 5


def test_empty_tracking_result_remains_pending() -> None:
    assert map_lightrag_tracking_status(_tracking()).status == "pending"


def test_unknown_lightrag_status_fails_closed_instead_of_becoming_ready() -> None:
    mapped = map_lightrag_tracking_status(_tracking("surprising-new-state"))

    assert mapped.status == "failed"
    assert mapped.failure_code == "lightrag_unknown_status"
    assert mapped.failure_reason == "LightRAG 返回了暂不支持的文档状态。"


class FakeLightRAGClient:
    def __init__(
        self,
        *,
        tracking: list[LightRAGTrackStatus] | None = None,
        upload_error: Exception | None = None,
        tracking_error: Exception | None = None,
    ) -> None:
        self.tracking = list(tracking or [])
        self.upload_error = upload_error
        self.tracking_error = tracking_error
        self.upload_calls: list[tuple[str, bytes, str]] = []
        self.track_calls: list[str] = []
        self.remote_upload: LightRAGUpload | None = None
        self.reconcile_calls: list[str] = []

    async def upload_document(self, *, filename: str, content: bytes, content_type: str) -> LightRAGUpload:
        self.upload_calls.append((filename, content, content_type))
        if self.upload_error is not None:
            raise self.upload_error
        if self.remote_upload is not None:
            raise LightRAGConflictError("conflict")
        self.remote_upload = LightRAGUpload(status="success", message="accepted", track_id="upload-remote")
        return self.remote_upload

    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        self.reconcile_calls.append(filename)
        if self.remote_upload is None:
            return None
        return LightRAGRemoteDocument(
            id="remote-document",
            track_id=self.remote_upload.track_id,
            status="pending",
            file_path=filename,
        )

    async def get_track_status(self, track_id: str) -> LightRAGTrackStatus:
        self.track_calls.append(track_id)
        if self.tracking_error is not None:
            raise self.tracking_error
        if not self.tracking:
            raise AssertionError("unexpected tracking poll")
        return self.tracking.pop(0)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class InterruptOnce:
    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.triggered = False

    async def __call__(self, stage: str) -> None:
        if stage == self.stage and not self.triggered:
            self.triggered = True
            raise asyncio.CancelledError


@pytest_asyncio.fixture
async def document_runtime(tmp_path: Path) -> AsyncIterator[tuple[KnowledgeDocumentRepository, KnowledgeFileStore]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingestion.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    await KnowledgeScopeRepository(sf).create(
        scope_id="scope-stable",
        owner_user_id="alice",
        name="统一知识库",
        description="",
        enabled=True,
    )
    try:
        yield KnowledgeDocumentRepository(sf), KnowledgeFileStore(tmp_path / "files")
    finally:
        await engine.dispose()


async def _accepted_document(
    repo: KnowledgeDocumentRepository,
    store: KnowledgeFileStore,
    *,
    suffix: str = "stable",
) -> tuple[str, dict]:
    saved = await store.save(
        io.BytesIO(b"real document content"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name=f"doc-{suffix}.txt",
        original_filename="产品说明.txt",
        content_type="text/plain",
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
    )
    result = await repo.create_document_with_job(
        document_id=f"doc-{suffix}",
        job_id=f"job-{suffix}",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key=f"upload-{suffix}",
        original_filename=saved.original_filename,
        storage_name=saved.storage_name,
        content_type=saved.content_type,
        size_bytes=saved.size_bytes,
        content_sha256=saved.content_sha256,
    )
    return result.job["id"], result.document


class BlockingLightRAGClient(FakeLightRAGClient):
    def __init__(self) -> None:
        super().__init__(tracking=[_tracking("processed")])
        self.tracking_started = asyncio.Event()
        self.release_tracking = asyncio.Event()

    async def get_track_status(self, track_id: str) -> LightRAGTrackStatus:
        self.tracking_started.set()
        await self.release_tracking.wait()
        return await super().get_track_status(track_id)


@pytest.mark.asyncio
async def test_ingestion_uploads_server_named_file_tracks_real_states_and_finishes_ready(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(
        tracking=[
            _tracking("pending"),
            _tracking("parsing"),
            _tracking("processing"),
            _tracking("processed"),
        ]
    )
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    await service.process(job_id=job_id, document=document)

    assert client.upload_calls == [("doc-stable.txt", b"real document content", "text/plain")]
    assert client.track_calls == ["upload-remote"] * 4
    listed = await repo.list_for_user("alice")
    assert listed[0]["lightrag_tracking_id"] == "upload-remote"
    assert listed[0]["status"] == "ready"
    assert listed[0]["completed_at"] is not None
    assert [item["id"] for item in await repo.list_ready_candidates_for_user("alice")] == ["doc-stable"]


@pytest.mark.asyncio
async def test_ingestion_persists_real_stage_and_chunks_between_polls(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store, suffix="progress")
    client = FakeLightRAGClient(
        tracking=[
            LightRAGTrackStatus(
                track_id="upload-remote",
                documents=(
                    {
                        "id": "remote-document",
                        "status": "processing",
                        "chunks_count": 5,
                        "updated_at": "2026-07-15T01:02:00Z",
                    },
                ),
                total_count=1,
                status_summary={"processing": 1},
            )
        ]
    )
    clock = MutableClock(datetime(2026, 7, 15, 1, 2, tzinfo=UTC))

    async def stop_after_first_poll(_seconds: float) -> None:
        raise asyncio.CancelledError

    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
        clock=clock,
        waiter=stop_after_first_poll,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.process(job_id=job_id, document=document)

    persisted = (await repo.list_for_user("alice"))[0]
    assert persisted["lightrag_stage"] == "processing"
    assert persisted["lightrag_chunks_count"] == 5
    assert persisted["lightrag_stage_updated_at"] == clock.value.isoformat()


@pytest.mark.asyncio
async def test_remote_failed_status_records_only_a_stable_sanitized_reason(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(tracking=[_tracking("failed")])
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    await service.process(job_id=job_id, document=document)

    failed = (await repo.list_for_user("alice"))[0]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "lightrag_processing_failed"
    assert failed["failure_reason"] == "LightRAG 处理文档失败。"
    assert await repo.list_ready_candidates_for_user("alice") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "reason"),
    [
        (LightRAGContractError("raw response with document body"), "lightrag_contract_error", "LightRAG 返回了不兼容的响应。"),
    ],
)
async def test_adapter_errors_are_persisted_without_sensitive_exception_text(
    document_runtime,
    error: Exception,
    code: str,
    reason: str,
) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(upload_error=error)
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    await service.process(job_id=job_id, document=document)

    failed = (await repo.list_for_user("alice"))[0]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == code
    assert failed["failure_reason"] == reason
    assert "secret" not in failed["failure_reason"]
    assert "document body" not in failed["failure_reason"]


@pytest.mark.asyncio
async def test_transient_adapter_error_waits_for_bounded_retry_without_exposing_exception_text(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(upload_error=LightRAGOfflineError("https://secret.example/key=secret"))
    clock = MutableClock(datetime(2026, 7, 13, 9, 0, tzinfo=UTC))
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
        clock=clock,
    )

    await service.process(job_id=job_id, document=document)

    state = await repo.get_ingestion_state_for_user(document_id=document["id"], owner_user_id="alice")
    assert state is not None
    assert state.job["status"] == "retry_wait"
    assert state.job["last_error_code"] == "lightrag_unavailable"
    assert state.job["last_error_message"] == "LightRAG 当前不可用。"
    assert state.job["next_attempt_at"] == (clock.value + timedelta(seconds=5)).isoformat()
    assert "secret" not in state.job["last_error_message"]
    assert state.document["status"] == "pending"


@pytest.mark.asyncio
async def test_tracking_contract_failure_is_sanitized(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(tracking_error=LightRAGContractError("raw body"))
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    await service.process(job_id=job_id, document=document)

    failed = (await repo.list_for_user("alice"))[0]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "lightrag_contract_error"
    assert failed["failure_reason"] == "LightRAG 返回了不兼容的响应。"


@pytest.mark.asyncio
async def test_startup_discovers_pending_jobs_without_an_upload_request_wakeup(document_runtime) -> None:
    repo, store = document_runtime
    _job_id, _document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(tracking=[_tracking("processed")])
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    service.start()
    try:
        for _ in range(100):
            if (await repo.list_for_user("alice"))[0]["status"] == "ready":
                break
            await asyncio.sleep(0)
        assert (await repo.list_for_user("alice"))[0]["status"] == "ready"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_current_client_provider_waits_without_claiming_then_uses_rotated_config_on_retry(
    document_runtime,
) -> None:
    repo, store = document_runtime
    _job_id, document = await _accepted_document(repo, store)
    offline_client = FakeLightRAGClient(upload_error=LightRAGOfflineError("old endpoint"))
    rotated_client = FakeLightRAGClient(tracking=[_tracking("processed")])
    current_client: FakeLightRAGClient | None = None

    async def provide_current_client() -> FakeLightRAGClient | None:
        return current_client

    clock = MutableClock(datetime(2026, 7, 13, 9, 0, tzinfo=UTC))
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client_provider=provide_current_client,
        poll_interval_seconds=0,
        clock=clock,
    )

    assert await service.run_once() is False
    waiting = await repo.get_ingestion_state_for_user(
        document_id=document["id"],
        owner_user_id="alice",
    )
    assert waiting is not None
    assert waiting.job["status"] == "pending"
    assert waiting.job["attempt_count"] == 0

    current_client = offline_client
    assert await service.run_once() is True
    retrying = await repo.get_ingestion_state_for_user(
        document_id=document["id"],
        owner_user_id="alice",
    )
    assert retrying is not None and retrying.job["status"] == "retry_wait"

    current_client = rotated_client
    clock.advance(timedelta(seconds=5))
    assert await service.run_once() is True
    recovered = await repo.get_ingestion_state_for_user(
        document_id=document["id"],
        owner_user_id="alice",
    )
    assert recovered is not None and recovered.job["status"] == "succeeded"
    assert offline_client.upload_calls
    assert rotated_client.upload_calls


@pytest.mark.asyncio
async def test_bounded_worker_concurrency_prevents_one_tracking_job_from_starving_later_jobs(
    document_runtime,
) -> None:
    repo, store = document_runtime
    first_job_id, first_document = await _accepted_document(repo, store, suffix="first")
    second_job_id, second_document = await _accepted_document(repo, store, suffix="second")
    blocking_client = BlockingLightRAGClient()
    ready_client = FakeLightRAGClient(tracking=[_tracking("processed")])
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        poll_interval_seconds=0,
        max_concurrency=2,
    )

    service.schedule(job_id=first_job_id, document=first_document, lightrag_client=blocking_client)
    service.schedule(job_id=second_job_id, document=second_document, lightrag_client=ready_client)
    try:
        await asyncio.wait_for(blocking_client.tracking_started.wait(), timeout=1)
        for _ in range(100):
            second = await repo.get_ingestion_state_for_user(
                document_id=second_document["id"],
                owner_user_id="alice",
            )
            if second is not None and second.job["status"] == "succeeded":
                break
            await asyncio.sleep(0.001)
        assert second is not None and second.job["status"] == "succeeded"
    finally:
        blocking_client.release_tracking.set()
        await service.shutdown()


@pytest.mark.asyncio
async def test_tracking_poll_cap_releases_the_lease_into_bounded_retry_wait(document_runtime) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    client = FakeLightRAGClient(tracking=[_tracking("pending")])
    clock = MutableClock(datetime(2026, 7, 13, 9, 0, tzinfo=UTC))
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
        max_tracking_polls_per_attempt=1,
        clock=clock,
    )

    await service.process(job_id=job_id, document=document)

    state = await repo.get_ingestion_state_for_user(
        document_id=document["id"],
        owner_user_id="alice",
    )
    assert state is not None
    assert state.job["status"] == "retry_wait"
    assert state.job["last_error_code"] == "lightrag_timeout"
    assert state.job["next_attempt_at"] == (clock.value + timedelta(seconds=5)).isoformat()


@pytest.mark.asyncio
async def test_worker_loop_recovers_after_a_transient_repository_error(
    document_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, store = document_runtime
    await _accepted_document(repo, store)
    client = FakeLightRAGClient(tracking=[_tracking("processed")])
    original_claim = repo.claim_next_job
    claim_calls = 0

    async def flaky_claim(**kwargs):
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            raise RuntimeError("database temporarily unavailable")
        return await original_claim(**kwargs)

    monkeypatch.setattr(repo, "claim_next_job", flaky_claim)
    service = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
    )

    service.start()
    try:
        for _ in range(100):
            if (await repo.list_for_user("alice"))[0]["status"] == "ready":
                break
            await asyncio.sleep(0.001)
        assert claim_calls >= 2
        assert (await repo.list_for_user("alice"))[0]["status"] == "ready"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    [
        "after_claim_commit",
        "after_upload_before_tracking_commit",
        "after_tracking_before_status_commit",
    ],
)
async def test_interrupted_worker_is_recovered_after_lease_expiry_and_converges(
    document_runtime,
    stage: str,
) -> None:
    repo, store = document_runtime
    job_id, document = await _accepted_document(repo, store)
    tracking = [_tracking("processed")]
    if stage == "after_tracking_before_status_commit":
        tracking.append(_tracking("processed"))
    client = FakeLightRAGClient(tracking=tracking)
    clock = MutableClock(datetime(2026, 7, 13, 10, 0, tzinfo=UTC))
    interrupted = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
        clock=clock,
        lease_duration=timedelta(seconds=30),
        fault_injector=InterruptOnce(stage),
    )

    with pytest.raises(asyncio.CancelledError):
        await interrupted.process(job_id=job_id, document=document)

    interrupted_state = await repo.get_ingestion_state_for_user(
        document_id=document["id"],
        owner_user_id="alice",
    )
    assert interrupted_state is not None
    assert interrupted_state.job["status"] == "leased"
    assert interrupted_state.job["attempt_count"] == 1
    if stage == "after_upload_before_tracking_commit":
        assert interrupted_state.document["lightrag_tracking_id"] is None
    if stage == "after_tracking_before_status_commit":
        assert interrupted_state.document["lightrag_tracking_id"] == "upload-remote"

    clock.advance(timedelta(seconds=30))
    restarted = KnowledgeIngestionService(
        repo=repo,
        file_store=store,
        lightrag_client=client,
        poll_interval_seconds=0,
        clock=clock,
        lease_duration=timedelta(seconds=30),
    )
    assert await restarted.run_once() is True

    recovered = await repo.get_ingestion_state_for_user(document_id=document["id"], owner_user_id="alice")
    assert recovered is not None
    assert recovered.job["status"] == "succeeded"
    assert recovered.job["attempt_count"] == 2
    assert recovered.document["status"] == "ready"
    if stage == "after_upload_before_tracking_commit":
        assert client.reconcile_calls == ["doc-stable.txt"]
    if stage == "after_tracking_before_status_commit":
        assert len(client.upload_calls) == 1
