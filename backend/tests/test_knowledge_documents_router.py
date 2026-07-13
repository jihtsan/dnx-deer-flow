from __future__ import annotations

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
from app.knowledge.lightrag import LightRAGHealth, LightRAGOfflineError
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


def _upload(client: TestClient, *, key: str = "upload-stable", content: bytes = b"knowledge body"):
    return client.post(
        "/api/knowledge/documents",
        headers={"Idempotency-Key": key},
        files={"file": ("产品说明.txt", content, "text/plain")},
    )


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
        "original_filename": "产品说明.txt",
        "content_type": "text/plain",
        "size_bytes": len(b"knowledge body"),
        "status": "pending",
        "lightrag_tracking_id": None,
        "failure_code": None,
        "failure_reason": None,
        "ingestion_job_id": payload["document"]["ingestion_job_id"],
        "created_at": payload["document"]["created_at"],
        "updated_at": payload["document"]["updated_at"],
        "completed_at": None,
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
    app.dependency_overrides[get_config] = lambda: _config()
    app.dependency_overrides[get_lightrag_client] = lambda: HealthyLightRAG()

    with TestClient(app) as client:
        assert client.get("/api/knowledge/documents").status_code == 403
        assert _upload(client).status_code == 403
