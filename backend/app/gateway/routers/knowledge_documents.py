"""Upload and list documents in the singleton Knowledge Scope."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.gateway.authz import require_permission
from app.gateway.deps import (
    get_config,
    get_knowledge_document_repo,
    get_knowledge_file_store,
    get_knowledge_ingestion_service,
    get_knowledge_scope_repo,
)
from app.gateway.routers.features import get_lightrag_client, resolve_knowledge_base_feature
from app.knowledge.ingestion import KnowledgeIngestionService
from app.knowledge.lightrag import LightRAGClient
from app.knowledge.storage import (
    KnowledgeFileStore,
    KnowledgeFileTooLargeError,
    KnowledgeQuotaExceededError,
    KnowledgeUploadFilenameError,
    KnowledgeUploadTypeError,
    normalize_knowledge_filename,
    validate_knowledge_upload_type,
)
from deerflow.config.app_config import AppConfig
from deerflow.persistence.knowledge_documents import (
    KnowledgeDocumentQuotaExceededError,
    KnowledgeDocumentRepository,
    KnowledgeIdempotencyConflictError,
)
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/documents", tags=["knowledge"])

MAX_KNOWLEDGE_FILE_SIZE_BYTES = 25 * 1024 * 1024
MAX_KNOWLEDGE_TOTAL_SIZE_BYTES = 1024 * 1024 * 1024


class KnowledgeDocumentResponse(BaseModel):
    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    status: Literal["pending", "indexing", "ready", "failed"]
    lightrag_tracking_id: str | None
    failure_code: str | None
    failure_reason: str | None
    ingestion_job_id: str
    created_at: str
    updated_at: str
    completed_at: str | None


class KnowledgeDocumentsEnvelope(BaseModel):
    documents: list[KnowledgeDocumentResponse]


class KnowledgeDocumentAccepted(BaseModel):
    document: KnowledgeDocumentResponse
    deduplicated: bool


def _user_id(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    if auth is None or auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _document_response(document: dict[str, Any]) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document["id"],
        original_filename=document["original_filename"],
        content_type=document["content_type"],
        size_bytes=document["size_bytes"],
        status=document["status"],
        lightrag_tracking_id=document["lightrag_tracking_id"],
        failure_code=document["failure_code"],
        failure_reason=document["failure_reason"],
        ingestion_job_id=document["ingestion_job_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        completed_at=document["completed_at"],
    )


def _error(status_code: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **details},
    )


@router.get("", response_model=KnowledgeDocumentsEnvelope)
@require_permission("knowledge", "read")
async def list_knowledge_documents(
    request: Request,
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDocumentsEnvelope:
    documents = await repo.list_for_user(_user_id(request))
    return KnowledgeDocumentsEnvelope(documents=[_document_response(document) for document in documents])


@router.post(
    "",
    response_model=KnowledgeDocumentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_permission("knowledge", "write")
async def upload_knowledge_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    scope_repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
    document_repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
    file_store: KnowledgeFileStore = Depends(get_knowledge_file_store),
    ingestion_service: KnowledgeIngestionService = Depends(get_knowledge_ingestion_service),
) -> KnowledgeDocumentAccepted:
    owner_user_id = _user_id(request)
    scope = await scope_repo.get_for_user(owner_user_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge Scope not found")
    if not scope["enabled"]:
        raise _error(409, "knowledge_scope_disabled", "Knowledge Scope 已禁用。")

    data_plane = await resolve_knowledge_base_feature(config, lightrag_client)
    if data_plane.status != "ready" or lightrag_client is None:
        raise _error(
            503,
            "knowledge_data_plane_unavailable",
            data_plane.reason,
            status=data_plane.status,
        )

    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise _error(400, "knowledge_invalid_idempotency_key", "Idempotency-Key 不能为空。")
    try:
        original_filename = normalize_knowledge_filename(file.filename or "")
        content_type = file.content_type or "application/octet-stream"
        validate_knowledge_upload_type(original_filename, content_type)
    except KnowledgeUploadFilenameError as exc:
        raise _error(400, "knowledge_invalid_filename", "文件名不安全或无效。") from exc
    except KnowledgeUploadTypeError as exc:
        raise _error(400, "knowledge_unsupported_file_type", "不支持该文件类型或类型与扩展名不匹配。") from exc

    try:
        existing = await document_repo.get_idempotent_result_for_user(
            scope_id=scope["id"],
            owner_user_id=owner_user_id,
            idempotency_key=normalized_key,
        )
    except Exception as exc:
        logger.error("Knowledge idempotency lookup failed")
        raise _error(
            500,
            "knowledge_document_persistence_failed",
            "无法读取文档追踪记录。",
        ) from exc
    if existing is not None:
        try:
            fingerprint = await file_store.fingerprint(
                file.file,
                max_file_size_bytes=MAX_KNOWLEDGE_FILE_SIZE_BYTES,
            )
        except KnowledgeFileTooLargeError as exc:
            raise _error(413, "knowledge_file_too_large", "文件超过单文件大小限制。") from exc
        except OSError as exc:
            logger.error("Knowledge replay fingerprint failed")
            raise _error(500, "knowledge_file_read_failed", "无法安全读取上传文件。") from exc
        if existing.document["size_bytes"] != fingerprint.size_bytes or existing.document["content_sha256"] != fingerprint.content_sha256:
            raise _error(
                409,
                "knowledge_idempotency_conflict",
                "该 Idempotency-Key 已用于不同文件。",
            )
        return KnowledgeDocumentAccepted(
            document=_document_response(existing.document),
            deduplicated=True,
        )

    document_id = f"kd-{uuid.uuid4().hex}"
    job_id = f"kij-{uuid.uuid4().hex}"
    extension = Path(original_filename).suffix.lower()
    storage_name = f"{document_id}{extension}"
    try:
        saved = await file_store.save(
            file.file,
            owner_user_id=owner_user_id,
            scope_id=scope["id"],
            storage_name=storage_name,
            original_filename=original_filename,
            content_type=content_type,
            max_file_size_bytes=MAX_KNOWLEDGE_FILE_SIZE_BYTES,
            max_total_size_bytes=MAX_KNOWLEDGE_TOTAL_SIZE_BYTES,
        )
    except KnowledgeFileTooLargeError as exc:
        raise _error(413, "knowledge_file_too_large", "文件超过单文件大小限制。") from exc
    except KnowledgeQuotaExceededError as exc:
        raise _error(413, "knowledge_quota_exceeded", "知识库文件总配额不足。") from exc
    except KnowledgeUploadTypeError as exc:
        raise _error(400, "knowledge_unsupported_file_type", "文件内容与声明的文档类型不匹配。") from exc
    except OSError as exc:
        logger.error("Knowledge file persistence failed for an upload")
        raise _error(500, "knowledge_file_write_failed", "无法安全保存上传文件。") from exc

    try:
        result = await document_repo.create_document_with_job(
            document_id=document_id,
            job_id=job_id,
            scope_id=scope["id"],
            owner_user_id=owner_user_id,
            idempotency_key=normalized_key,
            original_filename=saved.original_filename,
            storage_name=saved.storage_name,
            content_type=saved.content_type,
            size_bytes=saved.size_bytes,
            content_sha256=saved.content_sha256,
            max_total_size_bytes=MAX_KNOWLEDGE_TOTAL_SIZE_BYTES,
        )
    except KnowledgeDocumentQuotaExceededError as exc:
        await file_store.delete(saved.path)
        raise _error(413, "knowledge_quota_exceeded", "知识库文件总配额不足。") from exc
    except KnowledgeIdempotencyConflictError as exc:
        await file_store.delete(saved.path)
        raise _error(
            409,
            "knowledge_idempotency_conflict",
            "该 Idempotency-Key 已用于不同文件。",
        ) from exc
    except asyncio.CancelledError:
        try:
            await file_store.delete(saved.path)
        finally:
            raise
    except Exception as exc:
        await file_store.delete(saved.path)
        logger.error("Knowledge document transaction failed after file persistence")
        raise _error(
            500,
            "knowledge_document_persistence_failed",
            "无法创建文档追踪记录。",
        ) from exc

    if not result.created:
        await file_store.delete(saved.path)
    else:
        ingestion_service.schedule(
            job_id=result.job["id"],
            document=result.document,
            lightrag_client=lightrag_client,
        )
    return KnowledgeDocumentAccepted(
        document=_document_response(result.document),
        deduplicated=not result.created,
    )
