from __future__ import annotations

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
from app.gateway.routers import knowledge_scope
from app.gateway.routers.features import get_lightrag_client
from app.knowledge.lightrag import LightRAGContractError, LightRAGHealth, LightRAGOfflineError
from deerflow.persistence.base import Base
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository

ALICE_ID = UUID("11111111-2222-3333-4444-555555555555")
BOB_ID = UUID("99999999-8888-7777-6666-555555555555")


def _user(user_id: UUID = ALICE_ID) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role="user")


class HealthyLightRAG:
    async def health(self) -> LightRAGHealth:
        return LightRAGHealth(status="healthy", core_version="1.5.2", api_version="0308", pipeline_active=False)


class OfflineLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGOfflineError("connection_failed")


class IncompatibleLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGContractError("missing_field")


async def _make_repo(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope-router.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return KnowledgeScopeRepository(async_sessionmaker(engine, expire_on_commit=False)), engine


@pytest.fixture
def scope_repo(tmp_path):
    repo, engine = anyio.run(_make_repo, tmp_path)
    yield repo
    anyio.run(engine.dispose)


def _config(*, enabled: bool = True, configured: bool = True):
    return SimpleNamespace(
        knowledge_base=SimpleNamespace(
            enabled=enabled,
            lightrag=SimpleNamespace(base_url="http://lightrag.invalid" if configured else None),
        )
    )


def _app(repo, *, user_id: UUID = ALICE_ID, enabled: bool = True, configured: bool = True, lightrag_client=None):
    app = make_authed_test_app(user_factory=lambda: _user(user_id))
    app.state.knowledge_scope_repo = repo
    app.include_router(knowledge_scope.router)
    app.dependency_overrides[get_config] = lambda: _config(enabled=enabled, configured=configured)
    app.dependency_overrides[get_lightrag_client] = lambda: lightrag_client or HealthyLightRAG()
    return app


def test_create_read_update_and_toggle_scope(scope_repo) -> None:
    with TestClient(_app(scope_repo)) as client:
        empty = client.get("/api/knowledge/scope")
        assert empty.status_code == 200
        assert empty.json()["scope"] is None
        assert empty.json()["data_plane"]["status"] == "ready"

        created = client.post(
            "/api/knowledge/scope",
            json={"name": "统一知识库", "description": "产品资料", "enabled": True},
        )
        assert created.status_code == 201
        scope = created.json()["scope"]
        assert scope["id"]
        assert scope["name"] == "统一知识库"
        assert scope["description"] == "产品资料"
        assert scope["enabled"] is True
        assert scope["document_stats"] == {"total": 0, "pending": 0, "indexing": 0, "ready": 0, "failed": 0}
        assert scope["created_at"]
        assert scope["updated_at"]

        reloaded = client.get("/api/knowledge/scope")
        assert reloaded.json()["scope"]["id"] == scope["id"]

        updated = client.patch("/api/knowledge/scope", json={"name": "客服知识库", "description": "退款与交付"})
        assert updated.json()["scope"]["name"] == "客服知识库"
        assert updated.json()["scope"]["description"] == "退款与交付"

        disabled = client.patch("/api/knowledge/scope", json={"enabled": False})
        assert disabled.json()["scope"]["enabled"] is False
        assert disabled.json()["scope"]["available_for_retrieval"] is False

        reenabled = client.patch("/api/knowledge/scope", json={"enabled": True})
        assert reenabled.json()["scope"]["enabled"] is True
        assert reenabled.json()["scope"]["available_for_retrieval"] is True


def test_duplicate_create_returns_stable_conflict(scope_repo) -> None:
    with TestClient(_app(scope_repo)) as client:
        assert client.post("/api/knowledge/scope", json={"name": "唯一", "description": "", "enabled": True}).status_code == 201
        response = client.post("/api/knowledge/scope", json={"name": "重复", "description": "", "enabled": True})
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_scope_already_exists", "message": "Knowledge Scope already exists"}}


def test_duplicate_create_stays_a_conflict_when_data_plane_is_offline(scope_repo) -> None:
    with TestClient(_app(scope_repo)) as client:
        assert client.post("/api/knowledge/scope", json={"name": "唯一", "description": "", "enabled": True}).status_code == 201

    with TestClient(_app(scope_repo, lightrag_client=OfflineLightRAG())) as client:
        response = client.post(
            "/api/knowledge/scope",
            json={"name": "重复", "description": "", "enabled": True},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "knowledge_scope_already_exists"


def test_foreign_and_missing_scope_have_the_same_invisible_response(scope_repo) -> None:
    with TestClient(_app(scope_repo, user_id=ALICE_ID)) as alice:
        assert alice.post("/api/knowledge/scope", json={"name": "Alice", "description": "", "enabled": True}).status_code == 201

    with TestClient(_app(scope_repo, user_id=BOB_ID)) as bob:
        invisible_get = bob.get("/api/knowledge/scope")
        invisible_update = bob.patch("/api/knowledge/scope", json={"name": "Bob"})
        invisible_create = bob.post("/api/knowledge/scope", json={"name": "Bob", "description": "", "enabled": True})

    assert invisible_get.status_code == 200
    assert invisible_get.json()["scope"] is None
    assert invisible_update.status_code == invisible_create.status_code == 404
    assert invisible_update.json() == invisible_create.json() == {"detail": "Knowledge Scope not found"}


def test_missing_scope_update_matches_invisible_response(scope_repo) -> None:
    with TestClient(_app(scope_repo, user_id=BOB_ID)) as client:
        response = client.patch("/api/knowledge/scope", json={"name": "Bob"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Knowledge Scope not found"}


@pytest.mark.parametrize("field", ["name", "description", "enabled"])
def test_update_rejects_explicit_null_fields(scope_repo, field) -> None:
    with TestClient(_app(scope_repo)) as client:
        assert client.post("/api/knowledge/scope", json={"name": "唯一", "description": "", "enabled": True}).status_code == 201
        response = client.patch("/api/knowledge/scope", json={field: None})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("enabled", "configured", "lightrag_client", "status"),
    [
        (False, True, HealthyLightRAG(), "disabled"),
        (True, False, HealthyLightRAG(), "unconfigured"),
        (True, True, OfflineLightRAG(), "offline"),
        (True, True, IncompatibleLightRAG(), "incompatible"),
    ],
)
def test_unavailable_data_plane_is_diagnostic_and_blocks_enable(scope_repo, enabled, configured, lightrag_client, status) -> None:
    anyio.run(
        lambda: scope_repo.create(
            scope_id="scope-disabled",
            owner_user_id=str(ALICE_ID),
            name="知识库",
            description="",
            enabled=False,
        )
    )
    with TestClient(_app(scope_repo, enabled=enabled, configured=configured, lightrag_client=lightrag_client)) as client:
        read = client.get("/api/knowledge/scope")
        edit = client.patch("/api/knowledge/scope", json={"description": "仍可编辑"})
        enable = client.patch("/api/knowledge/scope", json={"enabled": True})

    assert read.json()["data_plane"]["status"] == status
    assert edit.status_code == 200
    assert enable.status_code == 503
    assert enable.json()["detail"]["code"] == "knowledge_data_plane_unavailable"
    assert enable.json()["detail"]["status"] == status
    assert "lightrag.invalid" not in enable.text


def test_routes_require_knowledge_permission(scope_repo) -> None:
    class NoKnowledgePermission(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            user = _user()
            request.state.user = user
            request.state.auth = AuthContext(user=user, permissions=[])
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(NoKnowledgePermission)
    app.state.knowledge_scope_repo = scope_repo
    app.include_router(knowledge_scope.router)
    app.dependency_overrides[get_config] = lambda: _config()
    app.dependency_overrides[get_lightrag_client] = lambda: HealthyLightRAG()

    with TestClient(app) as client:
        assert client.get("/api/knowledge/scope").status_code == 403
        assert client.post("/api/knowledge/scope", json={"name": "x", "description": "", "enabled": True}).status_code == 403
