"""Read-only Knowledge Graph and structured retrieval endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.gateway.authz import require_permission
from app.gateway.deps import get_config, get_knowledge_scope_repo
from app.gateway.routers.features import get_lightrag_client, resolve_knowledge_base_feature
from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGClient,
    LightRAGConflictError,
    LightRAGContractError,
    LightRAGOfflineError,
)
from deerflow.config.app_config import AppConfig
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

GraphLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
RetrievalQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
RETRIEVAL_TOP_K_DEFAULT = 40
RETRIEVAL_CHUNK_TOP_K_DEFAULT = 20
RETRIEVAL_MAX_TOTAL_TOKENS_DEFAULT = 30000
GLOBAL_GRAPH_MAX_DEPTH = 5
GLOBAL_GRAPH_POPULAR_LABEL_LIMIT = 50
GLOBAL_GRAPH_MAX_COMPONENTS = 100
GLOBAL_GRAPH_MAX_REQUESTS = 200
GLOBAL_GRAPH_TOTAL_TIMEOUT_SECONDS = 30.0


class GraphLabelsResponse(BaseModel):
    labels: list[str]


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    entity_type: str
    description: str
    file_path: str


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    relation_type: str
    description: str
    keywords: str
    weight: float
    file_path: str


class ConnectedGraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    is_truncated: bool


class GlobalGraphResponse(ConnectedGraphResponse):
    total_labels: int
    components: int


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: RetrievalQuery
    mode: Literal["local", "global", "hybrid", "naive", "mix"] = "mix"
    top_k: int = Field(default=RETRIEVAL_TOP_K_DEFAULT, ge=1, le=100)
    chunk_top_k: int = Field(default=RETRIEVAL_CHUNK_TOP_K_DEFAULT, ge=1, le=100)
    max_total_tokens: int = Field(default=RETRIEVAL_MAX_TOTAL_TOKENS_DEFAULT, ge=256, le=32000)


class RetrievalEntityResponse(BaseModel):
    entity_name: str
    entity_type: str
    description: str
    file_path: str
    reference_id: str


class RetrievalRelationshipResponse(BaseModel):
    src_id: str
    tgt_id: str
    description: str
    keywords: str
    weight: float
    file_path: str
    reference_id: str


class RetrievalChunkResponse(BaseModel):
    chunk_id: str
    content: str
    file_path: str
    reference_id: str


class RetrievalReferenceResponse(BaseModel):
    reference_id: str
    file_path: str


class RetrievalKeywordsResponse(BaseModel):
    high_level: list[str]
    low_level: list[str]


class RetrievalProcessingInfoResponse(BaseModel):
    total_entities_found: int
    total_relations_found: int
    entities_after_truncation: int
    relations_after_truncation: int
    final_chunks_count: int


class RetrievalMetadataResponse(BaseModel):
    query_mode: Literal["local", "global", "hybrid", "naive", "mix"]
    keywords: RetrievalKeywordsResponse
    processing_info: RetrievalProcessingInfoResponse


class RetrievalResponse(BaseModel):
    entities: list[RetrievalEntityResponse]
    relationships: list[RetrievalRelationshipResponse]
    chunks: list[RetrievalChunkResponse]
    references: list[RetrievalReferenceResponse]
    metadata: RetrievalMetadataResponse


def _user_id(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    if auth is None or auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _unavailable(*, status: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_data_plane_unavailable",
            "message": message,
            "status": status,
        },
    )


async def _ready_client(
    request: Request,
    config: AppConfig,
    client: LightRAGClient | None,
    repo: KnowledgeScopeRepository,
) -> LightRAGClient:
    scope = await repo.get_for_user(_user_id(request))
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge Scope not found")
    if not scope["enabled"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "knowledge_scope_disabled",
                "message": "Knowledge Scope is disabled",
            },
        )
    data_plane = await resolve_knowledge_base_feature(config, client)
    if data_plane.status != "ready" or client is None:
        raise _unavailable(status=data_plane.status, message=data_plane.reason)
    return client


async def _read_upstream[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except LightRAGOfflineError as exc:
        raise _unavailable(status="offline", message="LightRAG is unreachable.") from exc
    except (LightRAGAuthenticationError, LightRAGContractError, LightRAGConflictError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "knowledge_data_plane_invalid_response",
                "message": "Knowledge data plane returned an incompatible response.",
            },
        ) from exc


async def _before_deadline[T](operation: Awaitable[T], *, deadline: float) -> T:
    async with asyncio.timeout_at(deadline):
        return await operation


@router.get("/graph/labels", response_model=GraphLabelsResponse)
@require_permission("knowledge", "read")
async def get_popular_graph_labels(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    config: AppConfig = Depends(get_config),
    client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> GraphLabelsResponse:
    ready = await _ready_client(request, config, client, repo)
    labels = await _read_upstream(ready.popular_graph_labels(limit=limit))
    return GraphLabelsResponse(labels=list(labels))


@router.get("/graph/search", response_model=GraphLabelsResponse)
@require_permission("knowledge", "read")
async def search_graph_labels(
    request: Request,
    q: Annotated[GraphLabel, Query()],
    limit: int = Query(default=20, ge=1, le=50),
    config: AppConfig = Depends(get_config),
    client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> GraphLabelsResponse:
    ready = await _ready_client(request, config, client, repo)
    labels = await _read_upstream(ready.search_graph_labels(q, limit=limit))
    return GraphLabelsResponse(labels=list(labels))


def _unique_labels(labels: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(labels))


@router.get("/graph/global", response_model=GlobalGraphResponse)
@require_permission("knowledge", "read")
async def get_global_graph(
    request: Request,
    max_nodes: int = Query(default=5000, ge=1, le=5000),
    config: AppConfig = Depends(get_config),
    client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> GlobalGraphResponse:
    deadline = asyncio.get_running_loop().time() + GLOBAL_GRAPH_TOTAL_TIMEOUT_SECONDS
    try:
        ready = await _before_deadline(_ready_client(request, config, client, repo), deadline=deadline)
        all_labels = _unique_labels(await _before_deadline(_read_upstream(ready.all_graph_labels()), deadline=deadline))
    except TimeoutError as exc:
        raise _unavailable(status="offline", message="Knowledge graph request exceeded its time limit.") from exc

    total_labels = len(all_labels)
    if total_labels == 0:
        return GlobalGraphResponse(nodes=[], edges=[], is_truncated=False, total_labels=0, components=0)

    try:
        popular_labels = _unique_labels(
            await _before_deadline(
                _read_upstream(ready.popular_graph_labels(limit=min(total_labels, GLOBAL_GRAPH_POPULAR_LABEL_LIMIT))),
                deadline=deadline,
            )
        )
    except TimeoutError:
        return GlobalGraphResponse(nodes=[], edges=[], is_truncated=True, total_labels=total_labels, components=0)

    all_label_set = set(all_labels)
    popular_label_set = set(popular_labels)
    discovery_order = tuple(label for label in popular_labels if label in all_label_set) + tuple(label for label in all_labels if label not in popular_label_set)

    nodes_by_id: dict[str, GraphNodeResponse] = {}
    edge_candidates: dict[str, GraphEdgeResponse] = {}
    covered_labels: set[str] = set()
    components = 0
    requests = 0
    is_truncated = False

    for label in discovery_order:
        if label in covered_labels:
            continue
        if len(nodes_by_id) >= max_nodes or components >= GLOBAL_GRAPH_MAX_COMPONENTS or requests >= GLOBAL_GRAPH_MAX_REQUESTS:
            is_truncated = True
            break

        remaining_nodes = max_nodes - len(nodes_by_id)
        try:
            graph = await _before_deadline(
                _read_upstream(
                    ready.connected_graph(
                        label,
                        max_depth=GLOBAL_GRAPH_MAX_DEPTH,
                        max_nodes=remaining_nodes,
                    )
                ),
                deadline=deadline,
            )
        except TimeoutError:
            is_truncated = True
            break
        requests += 1
        covered_labels.add(label)
        for node in graph.nodes:
            covered_labels.add(node["id"])
            covered_labels.add(node["label"])

        component_added = False
        for node in graph.nodes:
            if node["id"] in nodes_by_id:
                continue
            if len(nodes_by_id) >= max_nodes:
                is_truncated = True
                break
            nodes_by_id[node["id"]] = GraphNodeResponse(**node)
            component_added = True
        if component_added:
            components += 1

        for edge in graph.edges:
            edge_candidates.setdefault(edge["id"], GraphEdgeResponse(**edge))
        is_truncated = is_truncated or graph.is_truncated

    if any(label not in covered_labels for label in all_labels):
        is_truncated = True

    node_ids = set(nodes_by_id)
    edges = [edge for edge in edge_candidates.values() if edge.source in node_ids and edge.target in node_ids]
    return GlobalGraphResponse(
        nodes=list(nodes_by_id.values()),
        edges=edges,
        is_truncated=is_truncated,
        total_labels=total_labels,
        components=components,
    )


@router.get("/graph", response_model=ConnectedGraphResponse)
@require_permission("knowledge", "read")
async def get_connected_graph(
    request: Request,
    label: Annotated[GraphLabel, Query()],
    max_depth: int = Query(default=2, ge=1, le=5),
    max_nodes: int = Query(default=100, ge=1, le=200),
    config: AppConfig = Depends(get_config),
    client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> ConnectedGraphResponse:
    ready = await _ready_client(request, config, client, repo)
    graph = await _read_upstream(ready.connected_graph(label, max_depth=max_depth, max_nodes=max_nodes))
    return ConnectedGraphResponse(
        nodes=[GraphNodeResponse(**node) for node in graph.nodes],
        edges=[GraphEdgeResponse(**edge) for edge in graph.edges],
        is_truncated=graph.is_truncated,
    )


@router.post("/retrieval", response_model=RetrievalResponse)
@require_permission("knowledge", "read")
async def retrieve_knowledge_data(
    body: RetrievalRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> RetrievalResponse:
    ready = await _ready_client(request, config, client, repo)
    result = await _read_upstream(
        ready.query_data(
            body.query,
            mode=body.mode,
            top_k=body.top_k,
            chunk_top_k=body.chunk_top_k,
            max_total_tokens=body.max_total_tokens,
        )
    )
    return RetrievalResponse(
        entities=[RetrievalEntityResponse(**item) for item in result.entities],
        relationships=[RetrievalRelationshipResponse(**item) for item in result.relationships],
        chunks=[RetrievalChunkResponse(**item) for item in result.chunks],
        references=[RetrievalReferenceResponse(**item) for item in result.references],
        metadata=RetrievalMetadataResponse(**result.metadata),
    )
