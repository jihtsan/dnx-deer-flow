from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import anyio
import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.gateway.auth.models import User
from app.gateway.authz import AuthContext
from app.gateway.deps import get_config
from app.gateway.routers import knowledge_documents, knowledge_scope
from app.gateway.routers.features import get_lightrag_client
from app.knowledge.lightrag import LightRAGDelete, LightRAGHealth, LightRAGOfflineError, LightRAGRemoteDocument
from app.knowledge.storage import KnowledgeFileStore
from deerflow.persistence.base import Base
from deerflow.persistence.knowledge_documents import KnowledgeDocumentRepository
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository

ALICE_ID = UUID("11111111-2222-3333-4444-555555555555")
BOB_ID = UUID("99999999-8888-7777-6666-555555555555")


def _user(user_id: UUID = ALICE_ID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


class HealthyLightRAG:
    def __init__(self) -> None:
        self.upload_calls = 0

    async def health(self) -> LightRAGHealth:
        return LightRAGHealth(status="healthy", core_version="1.5.2", api_version="0308", pipeline_active=False)

    async def upload_document(self, **_kwargs):
        self.upload_calls += 1
        raise AssertionError("HTTP request must not await or perform LightRAG upload")

    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        return ()


class RemoteDocumentsLightRAG(HealthyLightRAG):
    def __init__(self) -> None:
        super().__init__()
        self.available = True

    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        if not self.available:
            raise LightRAGOfflineError("connection_failed")
        return (
            LightRAGRemoteDocument(
                id="remote-existing",
                track_id="track-existing",
                status="processed",
                file_path="/private/operator/AI-V1.1.pptx",
                content_length=4096,
                created_at="2026-07-10T08:00:00Z",
                updated_at="2026-07-10T08:05:00Z",
            ),
        )


class DeletingLightRAG(HealthyLightRAG):
    def __init__(
        self,
        *,
        track_id: str = "track-managed",
        remote_id: str = "remote-managed",
        delete_status: str = "deletion_started",
        delete_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.track_id = track_id
        self.remote_id = remote_id
        self.delete_status = delete_status
        self.delete_error = delete_error
        self.deleted = False
        self.find_calls: list[str] = []
        self.delete_calls: list[tuple[list[str], bool, bool]] = []

    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        self.find_calls.append(filename)
        return LightRAGRemoteDocument(
            id=self.remote_id,
            track_id=self.track_id,
            status="processed",
            file_path=filename,
        )

    async def delete_documents(
        self,
        doc_ids: list[str],
        *,
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> LightRAGDelete:
        self.delete_calls.append((doc_ids, delete_file, delete_llm_cache))
        if self.delete_error is not None:
            raise self.delete_error
        if self.delete_status == "deletion_started":
            self.deleted = True
        return LightRAGDelete(
            status=self.delete_status,
            message="accepted",
            doc_id=", ".join(doc_ids),
        )

    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        if self.deleted:
            return ()
        return (
            LightRAGRemoteDocument(
                id=self.remote_id,
                track_id=self.track_id,
                status="processed",
                file_path="managed.txt",
            ),
        )


class RemoteDeletingLightRAG(RemoteDocumentsLightRAG):
    def __init__(self) -> None:
        super().__init__()
        self.deleted = False
        self.delete_calls: list[tuple[list[str], bool, bool]] = []

    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        if self.deleted:
            return ()
        return await super().list_documents()

    async def delete_documents(
        self,
        doc_ids: list[str],
        *,
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> LightRAGDelete:
        self.delete_calls.append((doc_ids, delete_file, delete_llm_cache))
        self.deleted = True
        return LightRAGDelete(
            status="deletion_started",
            message="accepted",
            doc_id=", ".join(doc_ids),
        )


class MissingRemoteLightRAG(DeletingLightRAG):
    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        self.find_calls.append(filename)
        return None


class PersistentDeletingLightRAG(DeletingLightRAG):
    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        return (
            LightRAGRemoteDocument(
                id=self.remote_id,
                track_id=self.track_id,
                status="processed",
                file_path="managed.txt",
            ),
        )


class OfflineAfterDeleteLightRAG(DeletingLightRAG):
    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        if self.deleted:
            raise LightRAGOfflineError("private polling endpoint")
        return await super().list_documents()


class IncompatibleDeletingLightRAG(DeletingLightRAG):
    async def health(self) -> LightRAGHealth:
        return LightRAGHealth(status="healthy", core_version="0.0.0", api_version="0308", pipeline_active=False)

    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        raise AssertionError("readiness must be checked before remote lookup")


class FailingDeleteFileStore:
    def __init__(self) -> None:
        self.delete_calls: list[Path] = []

    async def delete(self, path: Path) -> None:
        self.delete_calls.append(path)
        raise OSError("simulated file delete failure")


class OfflineLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGOfflineError("secret endpoint")


class RecordingIngestionService:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, dict]] = []

    def schedule(self, *, job_id: str, document: dict, lightrag_client=None) -> bool:
        self.scheduled.append((job_id, document))
        return True


async def _make_repositories(tmp_path: Path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'documents-router.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return KnowledgeScopeRepository(sf), KnowledgeDocumentRepository(sf), engine


@pytest.fixture
def document_runtime(tmp_path: Path):
    scope_repo, document_repo, engine = anyio.run(_make_repositories, tmp_path)
    runtime = SimpleNamespace(
        scope_repo=scope_repo,
        document_repo=document_repo,
        engine=engine,
        store=KnowledgeFileStore(tmp_path / "knowledge-files"),
        service=RecordingIngestionService(),
        root=tmp_path / "knowledge-files",
    )
    yield runtime
    anyio.run(engine.dispose)


def _config(*, enabled: bool = True, configured: bool = True):
    return SimpleNamespace(
        knowledge_base=SimpleNamespace(
            enabled=enabled,
            lightrag=SimpleNamespace(base_url="http://lightrag.invalid" if configured else None),
        )
    )


def _app(
    runtime,
    *,
    user_id: UUID = ALICE_ID,
    enabled: bool = True,
    configured: bool = True,
    lightrag_client=None,
):
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.state.knowledge_scope_repo = runtime.scope_repo
    app.state.knowledge_document_repo = runtime.document_repo
    app.state.knowledge_file_store = runtime.store
    app.state.knowledge_ingestion_service = runtime.service
    app.include_router(knowledge_documents.router)
    app.include_router(knowledge_documents.directory_router)
    app.include_router(knowledge_scope.router)
    app.dependency_overrides[get_config] = lambda: _config(enabled=enabled, configured=configured)
    app.dependency_overrides[get_lightrag_client] = lambda: lightrag_client or HealthyLightRAG()
    return app


def _create_scope(runtime, *, owner: UUID = ALICE_ID, enabled: bool = True) -> None:
    anyio.run(
        lambda: runtime.scope_repo.create(
            scope_id="scope-stable",
            owner_user_id=str(owner),
            name="统一知识库",
            description="",
            enabled=enabled,
        )
    )


def _upload(
    client: TestClient,
    *,
    key: str = "upload-stable",
    content: bytes = b"knowledge body",
    directory_id: str | None = None,
):
    return client.post(
        "/api/knowledge/documents",
        headers={"Idempotency-Key": key},
        data={"directory_id": directory_id} if directory_id is not None else None,
        files={"file": ("产品说明.txt", content, "text/plain")},
    )


def _cancel_ingestion(runtime, document: dict) -> None:
    cancelled = anyio.run(
        lambda: runtime.document_repo.cancel_job(
            job_id=document["ingestion_job_id"],
            now=datetime(2026, 7, 15, 7, 0, tzinfo=UTC),
        )
    )
    assert cancelled is not None


def test_upload_persists_file_and_atomic_records_then_returns_before_lightrag(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = HealthyLightRAG()

    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        response = _upload(client)

    assert response.status_code == 202
    payload = response.json()
    assert payload["deduplicated"] is False
    assert payload["document"] == {
        "id": payload["document"]["id"],
        "directory_id": None,
        "original_filename": "产品说明.txt",
        "content_type": "text/plain",
        "size_bytes": len(b"knowledge body"),
        "content_length": None,
        "status": "pending",
        "lightrag_tracking_id": None,
        "failure_code": None,
        "failure_reason": None,
        "ingestion_job_id": payload["document"]["ingestion_job_id"],
        "created_at": payload["document"]["created_at"],
        "updated_at": payload["document"]["updated_at"],
        "completed_at": None,
        "source": "managed",
        "original_available": True,
        "ingestion": {
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 5,
            "last_attempt_at": None,
            "next_attempt_at": payload["document"]["ingestion"]["next_attempt_at"],
            "last_error_code": None,
            "last_error_message": None,
            "manual_retry_count": 0,
            "retry_allowed": False,
        },
        "progress": {
            "stage": "pending",
            "chunks_count": None,
            "stage_updated_at": payload["document"]["progress"]["stage_updated_at"],
        },
    }
    assert lightrag.upload_calls == 0
    assert document_runtime.service.scheduled[0][0] == payload["document"]["ingestion_job_id"]
    persisted = [path for path in document_runtime.root.rglob("*") if path.is_file()]
    assert len(persisted) == 1
    assert persisted[0].read_bytes() == b"knowledge body"
    assert anyio.run(document_runtime.document_repo.document_stats_for_user, str(ALICE_ID)) == {
        "total": 1,
        "pending": 1,
        "indexing": 0,
        "ready": 0,
        "failed": 0,
    }


def test_idempotent_replay_returns_same_business_result_without_duplicate_file_or_task(
    document_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_scope(document_runtime)
    monkeypatch.setattr(knowledge_documents, "MAX_KNOWLEDGE_TOTAL_SIZE_BYTES", len(b"knowledge body"))
    with TestClient(_app(document_runtime)) as client:
        first = _upload(client)
        replay = _upload(client)

    assert first.status_code == replay.status_code == 202
    assert replay.json()["deduplicated"] is True
    assert replay.json()["document"] == first.json()["document"]
    assert len(document_runtime.service.scheduled) == 1
    assert len([path for path in document_runtime.root.rglob("*") if path.is_file()]) == 1
    assert anyio.run(document_runtime.document_repo.document_stats_for_user, str(ALICE_ID))["total"] == 1


def test_reusing_idempotency_key_for_different_file_is_a_stable_conflict(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        assert _upload(client, content=b"first body").status_code == 202
        conflict = _upload(client, content=b"different body")

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "knowledge_idempotency_conflict"
    assert len([path for path in document_runtime.root.rglob("*") if path.is_file()]) == 1
    assert len(document_runtime.service.scheduled) == 1


def test_route_enforces_total_file_quota_before_creating_another_document(
    document_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_scope(document_runtime)
    limit = len(b"knowledge body")
    monkeypatch.setattr(knowledge_documents, "MAX_KNOWLEDGE_TOTAL_SIZE_BYTES", limit)
    observed_limits: list[int | None] = []
    original_save = document_runtime.store.save

    async def recording_save(*args, **kwargs):
        observed_limits.append(kwargs["max_total_size_bytes"])
        return await original_save(*args, **kwargs)

    monkeypatch.setattr(document_runtime.store, "save", recording_save)
    with TestClient(_app(document_runtime)) as client:
        assert _upload(client, key="first").status_code == 202
        rejected = _upload(client, key="second", content=b"x")

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "knowledge_quota_exceeded"
    assert len([path for path in document_runtime.root.rglob("*") if path.is_file()]) == 1
    assert anyio.run(document_runtime.document_repo.document_stats_for_user, str(ALICE_ID))["total"] == 1
    assert len(document_runtime.service.scheduled) == 1
    assert observed_limits == [limit, limit]


def test_database_failure_compensates_the_already_persisted_file(document_runtime) -> None:
    _create_scope(document_runtime)

    class FailingRepository:
        async def create_document_with_job(self, **_kwargs):
            raise RuntimeError("database commit failed")

        async def list_for_user(self, owner_user_id: str):
            return await document_runtime.document_repo.list_for_user(owner_user_id)

    document_runtime.document_repo = FailingRepository()
    with TestClient(_app(document_runtime)) as client:
        response = _upload(client)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "knowledge_document_persistence_failed"
    assert [path for path in document_runtime.root.rglob("*") if path.is_file()] == []
    assert anyio.run(document_runtime.scope_repo.get_for_user, str(ALICE_ID)) is not None


def test_file_write_failure_never_creates_a_visible_document_or_job(document_runtime) -> None:
    _create_scope(document_runtime)

    class FailingStore:
        async def save(self, *_args, **_kwargs):
            raise OSError("disk path is secret")

    document_runtime.store = FailingStore()
    with TestClient(_app(document_runtime)) as client:
        response = _upload(client)

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "knowledge_file_write_failed",
        "message": "无法安全保存上传文件。",
    }
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []
    assert document_runtime.service.scheduled == []
    assert "secret" not in response.text


@pytest.mark.parametrize(
    ("filename", "content_type", "code"),
    [
        ("../secret.txt", "text/plain", "knowledge_invalid_filename"),
        ("payload.exe", "application/octet-stream", "knowledge_unsupported_file_type"),
        ("manual.pdf", "text/plain", "knowledge_unsupported_file_type"),
    ],
)
def test_upload_rejects_unsafe_names_and_types(document_runtime, filename: str, content_type: str, code: str) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        response = client.post(
            "/api/knowledge/documents",
            headers={"Idempotency-Key": "unsafe"},
            files={"file": (filename, b"body", content_type)},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []


def test_list_recovers_persisted_state_and_preserves_owner_invisibility(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as alice:
        accepted = _upload(alice).json()["document"]
        reloaded = alice.get("/api/knowledge/documents")
    with TestClient(_app(document_runtime, user_id=BOB_ID)) as bob:
        invisible = bob.get("/api/knowledge/documents")

    assert reloaded.status_code == 200
    assert reloaded.json() == {"documents": [accepted]}
    assert invisible.status_code == 200
    assert invisible.json() == {"documents": []}


def test_delete_managed_document_without_remote_state_removes_records_and_file(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        accepted = _upload(client).json()["document"]
        _cancel_ingestion(document_runtime, accepted)
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        deleted = client.delete(f"/api/knowledge/documents/{accepted['id']}")
        after_delete = client.get("/api/knowledge/documents")
        replayed_key = _upload(client)

    assert deleted.status_code == 204
    assert persisted_files and all(not path.exists() for path in persisted_files)
    assert after_delete.json() == {"documents": []}
    assert replayed_key.status_code == 202
    assert replayed_key.json()["deduplicated"] is False


def test_delete_managed_document_is_idempotent_when_original_file_is_already_missing(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        accepted = _upload(client).json()["document"]
        _cancel_ingestion(document_runtime, accepted)
        for path in document_runtime.root.rglob("*"):
            if path.is_file():
                path.unlink()

        deleted = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert deleted.status_code == 204
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []


def test_delete_rejects_pending_managed_ingestion(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        accepted = _upload(client).json()["document"]
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "knowledge_document_delete_active"
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_managed_document_resolves_and_deletes_the_remote_document_id(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = DeletingLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        deleted = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert deleted.status_code == 204
    assert len(lightrag.find_calls) == 1
    assert lightrag.find_calls[0].startswith(accepted["id"])
    assert lightrag.delete_calls == [(["remote-managed"], True, False)]
    assert persisted_files and all(not path.exists() for path in persisted_files)
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []


def test_delete_managed_document_allows_local_cleanup_when_remote_is_already_absent(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = MissingRemoteLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )

        deleted = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert deleted.status_code == 204
    assert len(lightrag.find_calls) == 1
    assert lightrag.delete_calls == []
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []


def test_delete_remote_document_uses_persisted_remote_id(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = RemoteDeletingLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        synced = client.get("/api/knowledge/documents").json()["documents"][0]
        deleted = client.delete(f"/api/knowledge/documents/{synced['id']}")

    assert deleted.status_code == 204
    assert lightrag.delete_calls == [(["remote-existing"], True, False)]
    assert anyio.run(document_runtime.document_repo.list_remote_documents_for_user, str(ALICE_ID)) == []


def test_delete_rejects_tracking_mismatch_without_changing_local_state(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = DeletingLightRAG(track_id="different-track")
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 502
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_invalid_response"
    assert lightrag.delete_calls == []
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_upstream_failure_is_sanitized_and_preserves_local_state(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = DeletingLightRAG(delete_error=LightRAGOfflineError("private endpoint failed"))
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 503
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_unavailable"
    assert "private endpoint" not in rejected.text
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_unconfirmed_upstream_response_returns_502_and_preserves_local_state(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = DeletingLightRAG(delete_status="not_allowed")
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 502
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_invalid_response"
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_waits_for_remote_disappearance_and_times_out_without_local_changes(
    document_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_scope(document_runtime)
    monkeypatch.setattr(knowledge_documents, "REMOTE_DELETE_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(knowledge_documents, "REMOTE_DELETE_POLL_INTERVAL_SECONDS", 0)
    lightrag = PersistentDeletingLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 502
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_invalid_response"
    assert lightrag.delete_calls == [(["remote-managed"], True, False)]
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_poll_offline_returns_503_without_local_changes(
    document_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_scope(document_runtime)
    monkeypatch.setattr(knowledge_documents, "REMOTE_DELETE_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(knowledge_documents, "REMOTE_DELETE_POLL_INTERVAL_SECONDS", 0)
    lightrag = OfflineAfterDeleteLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 503
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_unavailable"
    assert "private polling endpoint" not in rejected.text
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_checks_data_plane_readiness_before_remote_operations(document_runtime) -> None:
    _create_scope(document_runtime)
    lightrag = IncompatibleDeletingLightRAG()
    with TestClient(_app(document_runtime)) as client:
        accepted = _upload(client).json()["document"]
        anyio.run(
            lambda: document_runtime.document_repo.set_remote_tracking(
                job_id=accepted["ingestion_job_id"],
                tracking_id="track-managed",
            )
        )
        anyio.run(
            lambda: document_runtime.document_repo.set_document_status(
                job_id=accepted["ingestion_job_id"],
                status="ready",
            )
        )
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]

    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        rejected = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert rejected.status_code == 503
    assert rejected.json()["detail"]["code"] == "knowledge_data_plane_unavailable"
    assert lightrag.find_calls == []
    assert lightrag.delete_calls == []
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1


def test_delete_logs_file_cleanup_failure_after_committed_local_delete(
    document_runtime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _create_scope(document_runtime)
    app = _app(document_runtime)
    failing_store = FailingDeleteFileStore()
    with TestClient(app, raise_server_exceptions=False) as client:
        accepted = _upload(client).json()["document"]
        _cancel_ingestion(document_runtime, accepted)
        persisted_files = [path for path in document_runtime.root.rglob("*") if path.is_file()]
        app.state.knowledge_file_store = failing_store

        with caplog.at_level("ERROR", logger="app.gateway.routers.knowledge_documents"):
            failed = client.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert failed.status_code == 500
    assert len(failing_store.delete_calls) == 1
    assert "Failed to delete managed knowledge document file after database deletion" in caplog.text
    assert persisted_files and all(path.exists() for path in persisted_files)
    assert anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID)) == []


def test_delete_hides_foreign_documents_and_rejects_active_ingestion(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as alice:
        accepted = _upload(alice).json()["document"]

    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    claim = anyio.run(
        lambda: document_runtime.document_repo.claim_job(
            job_id=accepted["ingestion_job_id"],
            now=now,
            lease_owner="delete-test",
            lease_duration=timedelta(minutes=1),
        )
    )
    assert claim is not None

    with TestClient(_app(document_runtime, user_id=BOB_ID)) as bob:
        invisible = bob.delete(f"/api/knowledge/documents/{accepted['id']}")
    with TestClient(_app(document_runtime)) as alice:
        active = alice.delete(f"/api/knowledge/documents/{accepted['id']}")

    assert invisible.status_code == 404
    assert active.status_code == 409
    assert active.json()["detail"]["code"] == "knowledge_document_delete_active"
    assert len(anyio.run(document_runtime.document_repo.list_for_user, str(ALICE_ID))) == 1
    assert [path for path in document_runtime.root.rglob("*") if path.is_file()]


def test_list_reconciles_existing_lightrag_documents_and_uses_cache_when_remote_is_offline(
    document_runtime,
) -> None:
    _create_scope(document_runtime)
    lightrag = RemoteDocumentsLightRAG()
    with TestClient(_app(document_runtime, lightrag_client=lightrag)) as client:
        synced = client.get("/api/knowledge/documents")
        lightrag.available = False
        cached = client.get("/api/knowledge/documents")
        scope = client.get("/api/knowledge/scope")

    assert synced.status_code == cached.status_code == 200
    assert synced.json() == cached.json()
    assert synced.json() == {
        "documents": [
            {
                "id": synced.json()["documents"][0]["id"],
                "directory_id": None,
                "original_filename": "AI-V1.1.pptx",
                "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "size_bytes": None,
                "content_length": 4096,
                "status": "ready",
                "lightrag_tracking_id": "track-existing",
                "failure_code": None,
                "failure_reason": None,
                "ingestion_job_id": None,
                "created_at": "2026-07-10T08:00:00+00:00",
                "updated_at": "2026-07-10T08:05:00+00:00",
                "completed_at": None,
                "source": "remote",
                "original_available": False,
                "ingestion": None,
                "progress": {
                    "stage": "processed",
                    "chunks_count": None,
                    "stage_updated_at": "2026-07-10T08:05:00+00:00",
                },
            }
        ]
    }
    assert scope.json()["scope"]["document_stats"] == {
        "total": 1,
        "pending": 0,
        "indexing": 0,
        "ready": 1,
        "failed": 0,
    }


def test_directory_crud_upload_assignment_and_document_move(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        engineering = client.post(
            "/api/knowledge/directories",
            json={"name": "工程资料", "parent_id": None},
        )
        child = client.post(
            "/api/knowledge/directories",
            json={"name": "规程", "parent_id": engineering.json()["id"]},
        )
        duplicate = client.post(
            "/api/knowledge/directories",
            json={"name": "  规程  ", "parent_id": engineering.json()["id"]},
        )
        uploaded = _upload(client, directory_id=child.json()["id"])
        non_empty = client.delete(f"/api/knowledge/directories/{child.json()['id']}")
        moved = client.patch(
            f"/api/knowledge/documents/{uploaded.json()['document']['id']}",
            json={"directory_id": engineering.json()["id"]},
        )
        renamed = client.patch(
            f"/api/knowledge/directories/{engineering.json()['id']}",
            json={"name": "工程中心"},
        )
        deleted = client.delete(f"/api/knowledge/directories/{child.json()['id']}")
        directories = client.get("/api/knowledge/directories")

    assert engineering.status_code == child.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "knowledge_directory_name_conflict"
    assert uploaded.status_code == 202
    assert uploaded.json()["document"]["directory_id"] == child.json()["id"]
    assert non_empty.status_code == 409
    assert non_empty.json()["detail"]["code"] == "knowledge_directory_not_empty"
    assert moved.status_code == 200
    assert moved.json()["directory_id"] == engineering.json()["id"]
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "工程中心"
    assert deleted.status_code == 204
    assert directories.json() == {
        "directories": [
            {
                **renamed.json(),
                "document_count": 1,
                "child_count": 0,
            }
        ]
    }


def test_upload_rejects_unknown_directory_before_persisting_file(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        response = _upload(client, directory_id="missing-directory")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "knowledge_directory_not_found"
    assert [path for path in document_runtime.root.rglob("*") if path.is_file()] == []


def test_failed_document_exposes_diagnostics_and_manual_retry_is_idempotent(document_runtime) -> None:
    _create_scope(document_runtime)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    with TestClient(_app(document_runtime)) as client:
        accepted = _upload(client).json()["document"]

        async def fail_job() -> None:
            claim = await document_runtime.document_repo.claim_job(
                job_id=accepted["ingestion_job_id"],
                now=now,
                lease_owner="router-test",
                lease_duration=timedelta(minutes=1),
            )
            assert claim is not None
            failed = await document_runtime.document_repo.record_job_failure(
                job_id=accepted["ingestion_job_id"],
                lease_owner="router-test",
                attempt_count=1,
                now=now,
                error_code="lightrag_timeout",
                error_message="LightRAG 响应超时。",
                retryable=False,
                base_delay=timedelta(seconds=5),
                max_delay=timedelta(minutes=1),
            )
            assert failed is not None

        anyio.run(fail_job)
        listed = client.get("/api/knowledge/documents")
        retried = client.post(
            f"/api/knowledge/documents/{accepted['id']}/retry",
            headers={"Idempotency-Key": "retry-stable-1"},
        )
        replayed = client.post(
            f"/api/knowledge/documents/{accepted['id']}/retry",
            headers={"Idempotency-Key": "retry-stable-1"},
        )
        conflicted = client.post(
            f"/api/knowledge/documents/{accepted['id']}/retry",
            headers={"Idempotency-Key": "retry-stable-2"},
        )

    assert listed.status_code == 200
    failed = listed.json()["documents"][0]
    assert failed["status"] == "failed"
    assert failed["ingestion"] == {
        "status": "dead",
        "attempt_count": 1,
        "max_attempts": 5,
        "last_attempt_at": now.isoformat(),
        "next_attempt_at": None,
        "last_error_code": "lightrag_timeout",
        "last_error_message": "LightRAG 响应超时。",
        "manual_retry_count": 0,
        "retry_allowed": True,
    }
    assert retried.status_code == replayed.status_code == 202
    assert retried.json()["deduplicated"] is False
    assert replayed.json()["deduplicated"] is True
    assert retried.json()["document"]["status"] == "pending"
    assert retried.json()["document"]["ingestion"]["status"] == "pending"
    assert retried.json()["document"]["ingestion"]["attempt_count"] == 0
    assert retried.json()["document"]["ingestion"]["manual_retry_count"] == 1
    assert conflicted.status_code == 409
    assert conflicted.json()["detail"]["code"] == "knowledge_retry_already_active"
    assert len(document_runtime.service.scheduled) == 2


def test_permanent_file_failure_and_foreign_document_cannot_be_manually_retried(document_runtime) -> None:
    _create_scope(document_runtime)
    now = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
    with TestClient(_app(document_runtime)) as alice:
        accepted = _upload(alice).json()["document"]

    async def fail_job() -> None:
        claim = await document_runtime.document_repo.claim_job(
            job_id=accepted["ingestion_job_id"],
            now=now,
            lease_owner="router-test",
            lease_duration=timedelta(minutes=1),
        )
        assert claim is not None
        await document_runtime.document_repo.record_job_failure(
            job_id=accepted["ingestion_job_id"],
            lease_owner="router-test",
            attempt_count=1,
            now=now,
            error_code="knowledge_file_unavailable",
            error_message="已接收的原文件当前不可用。",
            retryable=False,
            base_delay=timedelta(seconds=5),
            max_delay=timedelta(minutes=1),
        )

    anyio.run(fail_job)
    with TestClient(_app(document_runtime)) as alice:
        rejected = alice.post(
            f"/api/knowledge/documents/{accepted['id']}/retry",
            headers={"Idempotency-Key": "retry-permanent"},
        )
    with TestClient(_app(document_runtime, user_id=BOB_ID)) as bob:
        invisible = bob.post(
            f"/api/knowledge/documents/{accepted['id']}/retry",
            headers={"Idempotency-Key": "retry-foreign"},
        )

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "knowledge_retry_not_allowed"
    assert invisible.status_code == 404


def test_scope_stats_are_derived_from_persisted_document_states(document_runtime) -> None:
    _create_scope(document_runtime)
    with TestClient(_app(document_runtime)) as client:
        assert _upload(client).status_code == 202
        scope = client.get("/api/knowledge/scope")

    assert scope.status_code == 200
    assert scope.json()["scope"]["document_stats"] == {
        "total": 1,
        "pending": 1,
        "indexing": 0,
        "ready": 0,
        "failed": 0,
    }


@pytest.mark.parametrize(
    ("scope_enabled", "feature_enabled", "configured", "client", "status", "code"),
    [
        (False, True, True, HealthyLightRAG(), 409, "knowledge_scope_disabled"),
        (True, False, True, HealthyLightRAG(), 503, "knowledge_data_plane_unavailable"),
        (True, True, False, HealthyLightRAG(), 503, "knowledge_data_plane_unavailable"),
        (True, True, True, OfflineLightRAG(), 503, "knowledge_data_plane_unavailable"),
    ],
)
def test_upload_is_disabled_when_scope_or_data_plane_is_unavailable(
    document_runtime,
    scope_enabled: bool,
    feature_enabled: bool,
    configured: bool,
    client,
    status: int,
    code: str,
) -> None:
    _create_scope(document_runtime, enabled=scope_enabled)
    with TestClient(
        _app(
            document_runtime,
            enabled=feature_enabled,
            configured=configured,
            lightrag_client=client,
        )
    ) as test_client:
        response = _upload(test_client)

    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert [path for path in document_runtime.root.rglob("*") if path.is_file()] == []


def test_missing_or_foreign_scope_has_the_same_invisible_upload_response(document_runtime) -> None:
    with TestClient(_app(document_runtime, user_id=BOB_ID)) as missing:
        missing_response = _upload(missing)
    _create_scope(document_runtime, owner=ALICE_ID)
    with TestClient(_app(document_runtime, user_id=BOB_ID)) as foreign:
        foreign_response = _upload(foreign)

    assert missing_response.status_code == foreign_response.status_code == 404
    assert missing_response.json() == foreign_response.json() == {"detail": "Knowledge Scope not found"}


def test_document_routes_require_knowledge_permissions(document_runtime) -> None:
    class NoKnowledgePermission(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            user = _user()
            request.state.user = user
            request.state.auth = AuthContext(user=user, permissions=[])
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(NoKnowledgePermission)
    app.state.knowledge_scope_repo = document_runtime.scope_repo
    app.state.knowledge_document_repo = document_runtime.document_repo
    app.state.knowledge_file_store = document_runtime.store
    app.state.knowledge_ingestion_service = document_runtime.service
    app.include_router(knowledge_documents.router)
    app.include_router(knowledge_documents.directory_router)
    app.dependency_overrides[get_config] = lambda: _config()
    app.dependency_overrides[get_lightrag_client] = lambda: HealthyLightRAG()

    with TestClient(app) as client:
        assert client.get("/api/knowledge/documents").status_code == 403
        assert client.get("/api/knowledge/directories").status_code == 403
        assert _upload(client).status_code == 403
        assert client.delete("/api/knowledge/documents/missing").status_code == 403
        assert (
            client.post(
                "/api/knowledge/documents/missing/retry",
                headers={"Idempotency-Key": "retry"},
            ).status_code
            == 403
        )
