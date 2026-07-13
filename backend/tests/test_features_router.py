from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from app.gateway.routers import features
from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGContractError,
    LightRAGHealth,
    LightRAGOfflineError,
)


def _app_with_config(
    *,
    agents_api_enabled: bool,
    knowledge_base_enabled: bool = False,
    lightrag_base_url: str | None = None,
    lightrag_client: object | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(features.router)
    fake_config = SimpleNamespace(
        agents_api=SimpleNamespace(enabled=agents_api_enabled),
        knowledge_base=SimpleNamespace(
            enabled=knowledge_base_enabled,
            lightrag=SimpleNamespace(base_url=lightrag_base_url),
        ),
    )
    app.dependency_overrides[get_config] = lambda: fake_config
    if lightrag_client is not None:
        app.dependency_overrides[features.get_lightrag_client] = lambda: lightrag_client
    return app


def test_features_reports_agents_api_enabled() -> None:
    with TestClient(_app_with_config(agents_api_enabled=True)) as client:
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json() == {
        "agents_api": {"enabled": True},
        "knowledge_base": {
            "enabled": False,
            "status": "disabled",
            "reason": "Knowledge Base is disabled by configuration.",
            "diagnostics": {
                "workspace_mode": "single",
                "expected_tag": "v1.5.2-4-gab86f430",
                "expected_commit": "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
                "expected_core_version": "1.5.2",
                "expected_api_version": "0308",
                "observed_core_version": None,
                "observed_api_version": None,
                "service_status": None,
            },
        },
    }


def test_features_reports_agents_api_disabled() -> None:
    with TestClient(_app_with_config(agents_api_enabled=False)) as client:
        response = client.get("/api/features")
    assert response.status_code == 200
    assert response.json()["agents_api"] == {"enabled": False}
    assert response.json()["knowledge_base"]["status"] == "disabled"


def test_features_reports_knowledge_base_unconfigured_without_endpoint() -> None:
    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url=None,
        )
    ) as client:
        response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json()["knowledge_base"] == {
        "enabled": False,
        "status": "unconfigured",
        "reason": "Knowledge Base is enabled but no LightRAG service is configured.",
        "diagnostics": {
            "workspace_mode": "single",
            "expected_tag": "v1.5.2-4-gab86f430",
            "expected_commit": "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
            "expected_core_version": "1.5.2",
            "expected_api_version": "0308",
            "observed_core_version": None,
            "observed_api_version": None,
            "service_status": None,
        },
    }


class _HealthyLightRAG:
    async def health(self) -> LightRAGHealth:
        return LightRAGHealth(
            status="healthy",
            core_version="1.5.2",
            api_version="0308",
            pipeline_active=False,
        )


class _OfflineLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGOfflineError("connection_failed")


class _IncompatibleLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGContractError("missing_field")


class _AuthenticationFailedLightRAG:
    async def health(self) -> LightRAGHealth:
        raise LightRAGAuthenticationError("authentication_failed")


def test_features_reports_ready_with_redacted_diagnostics() -> None:
    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url="http://operator:secret@lightrag.internal:9621",
            lightrag_client=_HealthyLightRAG(),
        )
    ) as client:
        response = client.get("/api/features")

    assert response.status_code == 200
    knowledge = response.json()["knowledge_base"]
    assert knowledge["enabled"] is True
    assert knowledge["status"] == "ready"
    assert knowledge["reason"] == "LightRAG is ready."
    assert knowledge["diagnostics"]["observed_core_version"] == "1.5.2"
    assert knowledge["diagnostics"]["observed_api_version"] == "0308"
    assert knowledge["diagnostics"]["service_status"] == "healthy"
    serialized = response.text
    assert "lightrag.internal" not in serialized
    assert "secret" not in serialized


def test_features_reports_lightrag_offline() -> None:
    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url="http://lightrag.internal:9621",
            lightrag_client=_OfflineLightRAG(),
        )
    ) as client:
        response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json()["knowledge_base"]["status"] == "offline"
    assert response.json()["knowledge_base"]["reason"] == "LightRAG is unreachable."


def test_features_reports_lightrag_contract_incompatible() -> None:
    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url="http://lightrag.internal:9621",
            lightrag_client=_IncompatibleLightRAG(),
        )
    ) as client:
        response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json()["knowledge_base"]["status"] == "incompatible"
    assert response.json()["knowledge_base"]["reason"] == "LightRAG returned an incompatible contract."


def test_features_reports_lightrag_authentication_failure_as_incompatible() -> None:
    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url="http://operator:secret@lightrag.internal:9621",
            lightrag_client=_AuthenticationFailedLightRAG(),
        )
    ) as client:
        response = client.get("/api/features")

    knowledge = response.json()["knowledge_base"]
    assert knowledge["status"] == "incompatible"
    assert knowledge["reason"] == "LightRAG authentication failed."
    assert "secret" not in response.text


def test_features_reports_version_mismatch_as_incompatible() -> None:
    class WrongVersionLightRAG:
        async def health(self) -> LightRAGHealth:
            return LightRAGHealth(
                status="healthy",
                core_version="1.6.0",
                api_version="0400",
                pipeline_active=False,
            )

    with TestClient(
        _app_with_config(
            agents_api_enabled=True,
            knowledge_base_enabled=True,
            lightrag_base_url="http://lightrag.internal:9621",
            lightrag_client=WrongVersionLightRAG(),
        )
    ) as client:
        response = client.get("/api/features")

    knowledge = response.json()["knowledge_base"]
    assert knowledge["status"] == "incompatible"
    assert knowledge["diagnostics"]["observed_core_version"] == "1.6.0"
    assert knowledge["diagnostics"]["observed_api_version"] == "0400"
