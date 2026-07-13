from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.knowledge.ingestion import (
    KnowledgeIngestionService,
    map_lightrag_tracking_status,
)
from app.knowledge.lightrag import (
    LightRAGContractError,
    LightRAGOfflineError,
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

    async def upload_document(self, *, filename: str, content: bytes, content_type: str) -> LightRAGUpload:
        self.upload_calls.append((filename, content, content_type))
        if self.upload_error is not None:
            raise self.upload_error
        return LightRAGUpload(status="success", message="accepted", track_id="upload-remote")

    async def get_track_status(self, track_id: str) -> LightRAGTrackStatus:
        self.track_calls.append(track_id)
        if self.tracking_error is not None:
            raise self.tracking_error
        if not self.tracking:
            raise AssertionError("unexpected tracking poll")
        return self.tracking.pop(0)


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
) -> tuple[str, dict]:
    saved = await store.save(
        io.BytesIO(b"real document content"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="doc-stable.txt",
        original_filename="产品说明.txt",
        content_type="text/plain",
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
    )
    result = await repo.create_document_with_job(
        document_id="doc-stable",
        job_id="job-stable",
        scope_id="scope-stable",
        owner_user_id="alice",
        idempotency_key="upload-stable",
        original_filename=saved.original_filename,
        storage_name=saved.storage_name,
        content_type=saved.content_type,
        size_bytes=saved.size_bytes,
        content_sha256=saved.content_sha256,
    )
    return result.job["id"], result.document


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
        (LightRAGOfflineError("https://secret.example/key=secret"), "lightrag_unavailable", "LightRAG 当前不可用。"),
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
