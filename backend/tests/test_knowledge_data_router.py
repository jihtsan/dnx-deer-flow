from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config, get_knowledge_scope_repo
from app.gateway.routers import knowledge_data
from app.gateway.routers.features import get_lightrag_client
from app.knowledge.lightrag import (
    LightRAGContractError,
    LightRAGGraph,
    LightRAGHealth,
    LightRAGOfflineError,
    LightRAGQueryData,
)

USER_ID = UUID("11111111-2222-3333-4444-555555555555")


def _user() -> User:
    return User(id=USER_ID, email="knowledge@example.com", password_hash="x", system_role="user")


class ScopeRepo:
    def __init__(self, scope: dict | None) -> None:
        self.scope = scope

    async def get_for_user(self, owner_user_id: str):
        assert owner_user_id == str(USER_ID)
        return self.scope


class ReadyLightRAG:
    def __init__(self) -> None:
        self.query_call: tuple[str, str, int | None, int | None, int | None] | None = None

    async def health(self) -> LightRAGHealth:
        return LightRAGHealth(status="healthy", core_version="1.5.2", api_version="0308", pipeline_active=False)

    async def popular_graph_labels(self, *, limit: int):
        assert limit == 20
        return ("Alpha", "Beta")

    async def all_graph_labels(self):
        return ("Alpha", "Beta")

    async def search_graph_labels(self, query: str, *, limit: int):
        assert (query, limit) == ("alp", 7)
        return ("Alpha",)

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        assert (label, max_depth, max_nodes) == ("Alpha", 2, 100)
        return LightRAGGraph(
            nodes=(
                {
                    "id": "Alpha",
                    "label": "Alpha",
                    "entity_type": "concept",
                    "description": "A node",
                    "file_path": "doc.txt",
                },
            ),
            edges=(
                {
                    "id": "Alpha-Beta",
                    "source": "Alpha",
                    "target": "Beta",
                    "relation_type": "DIRECTED",
                    "description": "connects",
                    "keywords": "related",
                    "weight": 1.0,
                    "file_path": "doc.txt",
                },
            ),
            is_truncated=False,
        )

    async def query_data(self, query: str, *, mode: str, top_k=None, chunk_top_k=None, max_total_tokens=None):
        self.query_call = (query, mode, top_k, chunk_top_k, max_total_tokens)
        return LightRAGQueryData(
            status="success",
            entities=(
                {
                    "entity_name": "Alpha",
                    "entity_type": "concept",
                    "description": "A node",
                    "file_path": "doc.txt",
                    "reference_id": "1",
                },
            ),
            relationships=(),
            chunks=(
                {
                    "chunk_id": "chunk-1",
                    "content": "Relevant text",
                    "file_path": "doc.txt",
                    "reference_id": "1",
                },
            ),
            references=({"reference_id": "1", "file_path": "doc.txt"},),
            metadata={
                "query_mode": "mix",
                "keywords": {"high_level": ["energy"], "low_level": ["storage"]},
                "processing_info": {
                    "total_entities_found": 1,
                    "total_relations_found": 0,
                    "entities_after_truncation": 1,
                    "relations_after_truncation": 0,
                    "final_chunks_count": 1,
                },
            },
        )


class FailsAfterHealth(ReadyLightRAG):
    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        raise LightRAGOfflineError("private upstream detail")


class DisconnectedGraphLightRAG(ReadyLightRAG):
    def __init__(self) -> None:
        super().__init__()
        self.graph_calls: list[tuple[str, int, int]] = []

    async def all_graph_labels(self):
        return ("Small", "Tiny", "Hub", "HubPeer")

    async def popular_graph_labels(self, *, limit: int):
        assert limit == 4
        return ("Hub",)

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        self.graph_calls.append((label, max_depth, max_nodes))
        if label == "Hub":
            return _graph_component("Hub", "HubPeer", "large.txt")
        if label == "Small":
            return _graph_component("Small", "Tiny", "small.txt")
        raise AssertionError(f"covered label requested again: {label}")


class OverLimitGraphLightRAG(DisconnectedGraphLightRAG):
    async def all_graph_labels(self):
        return ("A", "B", "C")

    async def popular_graph_labels(self, *, limit: int):
        assert limit == 3
        return ("A",)

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        self.graph_calls.append((label, max_depth, max_nodes))
        assert (label, max_depth, max_nodes) == ("A", 5, 2)
        return LightRAGGraph(
            nodes=(
                _graph_node("A", "large.txt"),
                _graph_node("B", "large.txt"),
                _graph_node("C", "large.txt"),
            ),
            edges=(
                _graph_edge("A-B", "A", "B", "large.txt"),
                _graph_edge("B-C", "B", "C", "large.txt"),
            ),
            is_truncated=False,
        )


class IsolatedGraphLightRAG(DisconnectedGraphLightRAG):
    def __init__(self, *, empty: bool = False) -> None:
        super().__init__()
        self.empty = empty

    async def all_graph_labels(self):
        return ("A", "B", "C")

    async def popular_graph_labels(self, *, limit: int):
        assert limit == 3
        return ("A",)

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        self.graph_calls.append((label, max_depth, max_nodes))
        return LightRAGGraph(
            nodes=() if self.empty else (_graph_node(label, f"{label}.txt"),),
            edges=(),
            is_truncated=False,
        )


class DeadlineGraphLightRAG(IsolatedGraphLightRAG):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled_labels: list[str] = []

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int):
        self.graph_calls.append((label, max_depth, max_nodes))
        if label == "B":
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.cancelled_labels.append(label)
                raise
        return LightRAGGraph(nodes=(_graph_node(label, f"{label}.txt"),), edges=(), is_truncated=False)


class SearchContractFailureLightRAG(ReadyLightRAG):
    async def search_graph_labels(self, query: str, *, limit: int):
        raise LightRAGContractError("private malformed response")


class GlobalOfflineLightRAG(ReadyLightRAG):
    async def all_graph_labels(self):
        raise LightRAGOfflineError("private global endpoint")


def _graph_node(label: str, file_path: str) -> dict:
    return {
        "id": label,
        "label": label,
        "entity_type": "concept",
        "description": f"{label} node",
        "file_path": file_path,
    }


def _graph_edge(edge_id: str, source: str, target: str, file_path: str) -> dict:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation_type": "DIRECTED",
        "description": "connects",
        "keywords": "related",
        "weight": 1.0,
        "file_path": file_path,
    }


def _graph_component(first: str, second: str, file_path: str) -> LightRAGGraph:
    return LightRAGGraph(
        nodes=(_graph_node(first, file_path), _graph_node(second, file_path)),
        edges=(_graph_edge(f"{first}-{second}", first, second, file_path),),
        is_truncated=False,
    )


def _config():
    return SimpleNamespace(
        knowledge_base=SimpleNamespace(
            enabled=True,
            lightrag=SimpleNamespace(base_url="http://lightrag.invalid"),
        )
    )


def _app(*, scope: dict | None = None, client=None):
    app = make_authed_test_app(user_factory=_user)
    app.include_router(knowledge_data.router)
    app.dependency_overrides[get_config] = _config
    app.dependency_overrides[get_lightrag_client] = lambda: client or ReadyLightRAG()
    app.dependency_overrides[get_knowledge_scope_repo] = lambda: ScopeRepo({"id": "scope-1", "enabled": True} if scope is None else scope)
    return app


def test_graph_and_retrieval_routes_expose_normalized_contract() -> None:
    upstream = ReadyLightRAG()
    with TestClient(_app(client=upstream)) as client:
        labels = client.get("/api/knowledge/graph/labels")
        graph = client.get("/api/knowledge/graph", params={"label": "Alpha"})
        retrieval = client.post(
            "/api/knowledge/retrieval",
            json={
                "query": "energy storage",
                "mode": "mix",
                "top_k": 10,
                "chunk_top_k": 5,
                "max_total_tokens": 2000,
            },
        )

    assert labels.status_code == 200
    assert labels.json() == {"labels": ["Alpha", "Beta"]}
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["file_path"] == "doc.txt"
    assert graph.json()["is_truncated"] is False
    assert retrieval.status_code == 200
    assert retrieval.json()["entities"][0]["entity_name"] == "Alpha"
    assert retrieval.json()["metadata"]["processing_info"]["final_chunks_count"] == 1
    assert "status" not in retrieval.json()
    assert upstream.query_call == ("energy storage", "mix", 10, 5, 2000)


def test_global_graph_discovers_disconnected_components_without_losing_small_documents() -> None:
    upstream = DisconnectedGraphLightRAG()
    with TestClient(_app(client=upstream)) as client:
        response = client.get("/api/knowledge/graph/global")

    assert response.status_code == 200
    assert response.json() == {
        "nodes": [
            _graph_node("Hub", "large.txt"),
            _graph_node("HubPeer", "large.txt"),
            _graph_node("Small", "small.txt"),
            _graph_node("Tiny", "small.txt"),
        ],
        "edges": [
            _graph_edge("Hub-HubPeer", "Hub", "HubPeer", "large.txt"),
            _graph_edge("Small-Tiny", "Small", "Tiny", "small.txt"),
        ],
        "is_truncated": False,
        "total_labels": 4,
        "components": 2,
    }
    assert upstream.graph_calls == [("Hub", 5, 5000), ("Small", 5, 4998)]


def test_global_graph_enforces_node_limit_and_reports_truncation() -> None:
    upstream = OverLimitGraphLightRAG()
    with TestClient(_app(client=upstream)) as client:
        response = client.get("/api/knowledge/graph/global", params={"max_nodes": 2})

    assert response.status_code == 200
    assert [node["id"] for node in response.json()["nodes"]] == ["A", "B"]
    assert [edge["id"] for edge in response.json()["edges"]] == ["A-B"]
    assert response.json()["is_truncated"] is True
    assert response.json()["total_labels"] == 3
    assert response.json()["components"] == 1


def test_global_graph_component_and_request_caps_report_truncation(monkeypatch) -> None:
    component_limited = IsolatedGraphLightRAG()
    monkeypatch.setattr(knowledge_data, "GLOBAL_GRAPH_MAX_COMPONENTS", 1)
    with TestClient(_app(client=component_limited)) as client:
        component_response = client.get("/api/knowledge/graph/global")

    assert component_response.status_code == 200
    assert component_response.json()["components"] == 1
    assert component_response.json()["is_truncated"] is True
    assert [call[0] for call in component_limited.graph_calls] == ["A"]

    monkeypatch.setattr(knowledge_data, "GLOBAL_GRAPH_MAX_COMPONENTS", 100)
    monkeypatch.setattr(knowledge_data, "GLOBAL_GRAPH_MAX_REQUESTS", 1)
    request_limited = IsolatedGraphLightRAG(empty=True)
    with TestClient(_app(client=request_limited)) as client:
        request_response = client.get("/api/knowledge/graph/global")

    assert request_response.status_code == 200
    assert request_response.json()["components"] == 0
    assert request_response.json()["is_truncated"] is True
    assert [call[0] for call in request_limited.graph_calls] == ["A"]


def test_global_graph_deadline_cancels_upstream_and_returns_partial_graph(monkeypatch) -> None:
    upstream = DeadlineGraphLightRAG()
    monkeypatch.setattr(knowledge_data, "GLOBAL_GRAPH_TOTAL_TIMEOUT_SECONDS", 0.05)

    with TestClient(_app(client=upstream)) as client:
        response = client.get("/api/knowledge/graph/global")

    assert response.status_code == 200
    assert [node["id"] for node in response.json()["nodes"]] == ["A"]
    assert response.json()["is_truncated"] is True
    assert response.json()["total_labels"] == 3
    assert response.json()["components"] == 1
    assert [call[0] for call in upstream.graph_calls] == ["A", "B"]
    assert upstream.cancelled_labels == ["B"]


def test_graph_search_exposes_matching_labels() -> None:
    upstream = ReadyLightRAG()
    with TestClient(_app(client=upstream)) as client:
        response = client.get("/api/knowledge/graph/search", params={"q": "alp", "limit": 7})

    assert response.status_code == 200
    assert response.json() == {"labels": ["Alpha"]}


def test_retrieval_uses_gateway_owned_defaults_when_limits_are_omitted() -> None:
    upstream = ReadyLightRAG()
    with TestClient(_app(client=upstream)) as client:
        response = client.post(
            "/api/knowledge/retrieval",
            json={"query": "energy storage", "mode": "hybrid"},
        )

    assert response.status_code == 200
    assert upstream.query_call == ("energy storage", "hybrid", 40, 20, 30000)


def test_routes_require_an_enabled_owner_visible_scope() -> None:
    app = _app()
    app.dependency_overrides[get_knowledge_scope_repo] = lambda: ScopeRepo(None)
    with TestClient(app) as client:
        missing = client.get("/api/knowledge/graph/labels")

    with TestClient(_app(scope={"id": "scope-1", "enabled": False})) as client:
        disabled = client.get("/api/knowledge/graph/labels")

    assert missing.status_code == 404
    assert disabled.status_code == 409
    assert disabled.json()["detail"]["code"] == "knowledge_scope_disabled"


def test_validation_bounds_and_bypass_are_rejected_before_upstream() -> None:
    with TestClient(_app()) as client:
        assert client.get("/api/knowledge/graph/labels", params={"limit": 51}).status_code == 422
        assert client.get("/api/knowledge/graph", params={"label": "", "max_depth": 6}).status_code == 422
        assert client.get("/api/knowledge/graph/global", params={"max_nodes": 5001}).status_code == 422
        assert client.get("/api/knowledge/graph/search", params={"q": "", "limit": 51}).status_code == 422
        assert client.post("/api/knowledge/retrieval", json={"query": "ab", "mode": "mix"}).status_code == 422
        assert client.post("/api/knowledge/retrieval", json={"query": "valid query", "mode": "bypass"}).status_code == 422
        assert client.post("/api/knowledge/retrieval", json={"query": "valid query", "top_k": 101}).status_code == 422


def test_upstream_errors_are_sanitized() -> None:
    with TestClient(_app(client=FailsAfterHealth())) as client:
        response = client.get("/api/knowledge/graph", params={"label": "Alpha"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "knowledge_data_plane_unavailable"
    assert "private upstream detail" not in response.text
    assert "lightrag.invalid" not in response.text

    with TestClient(_app(client=GlobalOfflineLightRAG())) as client:
        global_response = client.get("/api/knowledge/graph/global")
    assert global_response.status_code == 503
    assert "private global endpoint" not in global_response.text

    with TestClient(_app(client=SearchContractFailureLightRAG())) as client:
        search_response = client.get("/api/knowledge/graph/search", params={"q": "alp"})
    assert search_response.status_code == 502
    assert "private malformed response" not in search_response.text
