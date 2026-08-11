from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.gateway.authz import require_permission
from app.gateway.deps import get_config, get_knowledge_scope_repo
from app.gateway.routers.features import (
    KnowledgeBaseFeature,
    get_lightrag_client,
    resolve_knowledge_base_feature,
)
from app.knowledge.lightrag import LightRAGClient
from deerflow.config.app_config import AppConfig
from deerflow.persistence.knowledge_scope import (
    KnowledgeScopeAlreadyExistsError,
    KnowledgeScopeRepository,
)

router = APIRouter(prefix="/api/knowledge/scope", tags=["knowledge"])


class KnowledgeDocumentStats(BaseModel):
    total: int = 0
    pending: int = 0
    indexing: int = 0
    ready: int = 0
    failed: int = 0


class KnowledgeScopeResponse(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    available_for_retrieval: bool
    document_stats: KnowledgeDocumentStats = Field(default_factory=KnowledgeDocumentStats)
    created_at: str
    updated_at: str


class KnowledgeScopeEnvelope(BaseModel):
    scope: KnowledgeScopeResponse | None
    data_plane: KnowledgeBaseFeature


class KnowledgeScopeCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("name must not be blank")
        return value


class KnowledgeScopeUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_one_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("updated fields cannot be null")
        return self


def _user_id(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    if auth is None or auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _scope_response(
    scope: dict[str, Any],
    data_plane: KnowledgeBaseFeature,
    document_stats: dict[str, int] | None = None,
) -> KnowledgeScopeResponse:
    return KnowledgeScopeResponse(
        id=scope["id"],
        name=scope["name"],
        description=scope["description"],
        enabled=scope["enabled"],
        available_for_retrieval=bool(scope["enabled"] and data_plane.status == "ready"),
        document_stats=KnowledgeDocumentStats(**(document_stats or {})),
        created_at=scope["created_at"],
        updated_at=scope["updated_at"],
    )


def _unavailable(data_plane: KnowledgeBaseFeature) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_data_plane_unavailable",
            "message": data_plane.reason,
            "status": data_plane.status,
        },
    )


def _already_exists() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "knowledge_scope_already_exists",
            "message": "Knowledge Scope already exists",
        },
    )


async def _data_plane(
    config: AppConfig,
    lightrag_client: LightRAGClient | None,
) -> KnowledgeBaseFeature:
    return await resolve_knowledge_base_feature(config, lightrag_client)


async def _document_stats(request: Request, owner_user_id: str) -> dict[str, int]:
    repo = getattr(request.app.state, "knowledge_document_repo", None)
    if repo is None:
        return {}
    return await repo.document_stats_for_user(owner_user_id)


@router.get("", response_model=KnowledgeScopeEnvelope)
@require_permission("knowledge", "read")
async def get_knowledge_scope(
    request: Request,
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> KnowledgeScopeEnvelope:
    data_plane = await _data_plane(config, lightrag_client)
    user_id = _user_id(request)
    scope = await repo.get_for_user(user_id)
    return KnowledgeScopeEnvelope(
        scope=_scope_response(scope, data_plane, await _document_stats(request, user_id)) if scope is not None else None,
        data_plane=data_plane,
    )


@router.post("", response_model=KnowledgeScopeEnvelope, status_code=status.HTTP_201_CREATED)
@require_permission("knowledge", "write")
async def create_knowledge_scope(
    body: KnowledgeScopeCreateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> KnowledgeScopeEnvelope:
    user_id = _user_id(request)
    if await repo.get_for_user(user_id) is not None:
        raise _already_exists()
    data_plane = await _data_plane(config, lightrag_client)
    if body.enabled and data_plane.status != "ready":
        raise _unavailable(data_plane)
    try:
        scope = await repo.create(
            scope_id=f"ks-{uuid.uuid4().hex}",
            owner_user_id=user_id,
            name=body.name,
            description=body.description,
            enabled=body.enabled,
        )
    except KnowledgeScopeAlreadyExistsError as exc:
        if await repo.get_for_user(user_id) is None:
            raise HTTPException(status_code=404, detail="Knowledge Scope not found") from exc
        raise _already_exists() from exc
    return KnowledgeScopeEnvelope(
        scope=_scope_response(scope, data_plane, await _document_stats(request, user_id)),
        data_plane=data_plane,
    )


@router.patch("", response_model=KnowledgeScopeEnvelope)
@require_permission("knowledge", "write")
async def update_knowledge_scope(
    body: KnowledgeScopeUpdateRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
) -> KnowledgeScopeEnvelope:
    user_id = _user_id(request)
    existing = await repo.get_for_user(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Knowledge Scope not found")

    data_plane = await _data_plane(config, lightrag_client)
    if body.enabled is True and data_plane.status != "ready":
        raise _unavailable(data_plane)

    scope = await repo.update_for_user(user_id, **body.model_dump(exclude_unset=True))
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge Scope not found")
    return KnowledgeScopeEnvelope(
        scope=_scope_response(scope, data_plane, await _document_stats(request, user_id)),
        data_plane=data_plane,
    )
