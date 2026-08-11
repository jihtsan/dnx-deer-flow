"""Upload and list documents in the singleton Knowledge Scope."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile, status
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
from app.knowledge.ingestion import MANUALLY_RETRYABLE_ERROR_CODES, KnowledgeIngestionService
from app.knowledge.lightrag import (
    LightRAGClient,
    LightRAGContractError,
    LightRAGError,
    LightRAGOfflineError,
    LightRAGRemoteDocument,
)
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
    KnowledgeDirectoryConflictError,
    KnowledgeDirectoryNotEmptyError,
    KnowledgeDocumentDeleteConflictError,
    KnowledgeDocumentQuotaExceededError,
    KnowledgeDocumentRepository,
    KnowledgeIdempotencyConflictError,
    KnowledgeJobRetryConflictError,
    KnowledgeJobRetryNotAllowedError,
    KnowledgeRemoteDocumentSnapshot,
)
from deerflow.persistence.knowledge_scope import KnowledgeScopeRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/documents", tags=["knowledge"])
directory_router = APIRouter(prefix="/api/knowledge/directories", tags=["knowledge"])

MAX_KNOWLEDGE_FILE_SIZE_BYTES = 25 * 1024 * 1024
MAX_KNOWLEDGE_TOTAL_SIZE_BYTES = 1024 * 1024 * 1024
REMOTE_DELETE_POLL_ATTEMPTS = 10
REMOTE_DELETE_POLL_INTERVAL_SECONDS = 0.25


class KnowledgeDocumentResponse(BaseModel):
    id: str
    directory_id: str | None
    original_filename: str
    content_type: str
    size_bytes: int | None
    content_length: int | None
    status: Literal["pending", "indexing", "ready", "failed"]
    lightrag_tracking_id: str | None
    failure_code: str | None
    failure_reason: str | None
    ingestion_job_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    source: Literal["managed", "remote"]
    original_available: bool
    ingestion: KnowledgeIngestionDiagnostics | None
    progress: KnowledgeDocumentProgress | None


class KnowledgeDocumentProgress(BaseModel):
    stage: Literal["pending", "parsing", "analyzing", "processing", "preprocessed", "processed", "failed"]
    chunks_count: int | None
    stage_updated_at: str


class KnowledgeIngestionDiagnostics(BaseModel):
    status: Literal["pending", "leased", "retry_wait", "succeeded", "dead", "cancelled"]
    attempt_count: int
    max_attempts: int
    last_attempt_at: str | None
    next_attempt_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    manual_retry_count: int
    retry_allowed: bool


class KnowledgeDocumentsEnvelope(BaseModel):
    documents: list[KnowledgeDocumentResponse]


class KnowledgeDocumentAccepted(BaseModel):
    document: KnowledgeDocumentResponse
    deduplicated: bool


class KnowledgeDirectoryResponse(BaseModel):
    id: str
    parent_id: str | None
    name: str
    document_count: int = 0
    child_count: int = 0
    created_at: str
    updated_at: str


class KnowledgeDirectoriesEnvelope(BaseModel):
    directories: list[KnowledgeDirectoryResponse]


class KnowledgeDirectoryCreate(BaseModel):
    name: str
    parent_id: str | None = None


class KnowledgeDirectoryRename(BaseModel):
    name: str


class KnowledgeDocumentMove(BaseModel):
    directory_id: str | None = None


def _user_id(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    if auth is None or auth.user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(auth.user.id)


def _document_response(document: dict[str, Any], job: dict[str, Any]) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document["id"],
        directory_id=document["directory_id"],
        original_filename=document["original_filename"],
        content_type=document["content_type"],
        size_bytes=document["size_bytes"],
        content_length=None,
        status=document["status"],
        lightrag_tracking_id=document["lightrag_tracking_id"],
        failure_code=document["failure_code"],
        failure_reason=document["failure_reason"],
        ingestion_job_id=document["ingestion_job_id"],
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        completed_at=document["completed_at"],
        source="managed",
        original_available=True,
        ingestion=KnowledgeIngestionDiagnostics(
            status=job["status"],
            attempt_count=job["attempt_count"],
            max_attempts=job["max_attempts"],
            last_attempt_at=job["last_attempt_at"],
            next_attempt_at=job["next_attempt_at"],
            last_error_code=job["last_error_code"],
            last_error_message=job["last_error_message"],
            manual_retry_count=job["manual_retry_count"],
            retry_allowed=(job["status"] == "dead" and job["last_error_code"] in MANUALLY_RETRYABLE_ERROR_CODES),
        ),
        progress=_progress_response(document),
    )


def _remote_document_response(document: dict[str, Any]) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document["id"],
        directory_id=document["directory_id"],
        original_filename=document["original_filename"],
        content_type=document["content_type"],
        size_bytes=None,
        content_length=document["content_length"],
        status=document["status"],
        lightrag_tracking_id=document["lightrag_tracking_id"],
        failure_code=document["failure_code"],
        failure_reason=document["failure_reason"],
        ingestion_job_id=None,
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        completed_at=None,
        source="remote",
        original_available=False,
        ingestion=None,
        progress=_progress_response(document),
    )


def _progress_response(document: dict[str, Any]) -> KnowledgeDocumentProgress | None:
    stage = document.get("lightrag_stage")
    stage_updated_at = document.get("lightrag_stage_updated_at")
    if stage is None or stage_updated_at is None:
        return None
    return KnowledgeDocumentProgress(
        stage=stage,
        chunks_count=document.get("lightrag_chunks_count"),
        stage_updated_at=stage_updated_at,
    )


def _error(status_code: int, code: str, message: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, **details},
    )


def _delete_upstream_error(*, offline: bool) -> HTTPException:
    if offline:
        return _error(
            503,
            "knowledge_data_plane_unavailable",
            "知识服务暂时不可用，文档未删除。",
        )
    return _error(
        502,
        "knowledge_data_plane_invalid_response",
        "知识服务未能确认删除，文档未删除。",
    )


async def _delete_remote_document(lightrag_client: LightRAGClient, remote_document_id: str) -> None:
    try:
        result = await lightrag_client.delete_documents(
            [remote_document_id],
            delete_file=True,
        )
    except LightRAGOfflineError as exc:
        raise _delete_upstream_error(offline=True) from exc
    except LightRAGError as exc:
        raise _delete_upstream_error(offline=False) from exc
    if result.status == "busy":
        raise _error(409, "knowledge_document_delete_busy", "知识服务正在处理其他任务，请稍后重试。")
    if result.status != "deletion_started" or result.doc_id != remote_document_id:
        raise _delete_upstream_error(offline=False)
    for attempt in range(REMOTE_DELETE_POLL_ATTEMPTS):
        try:
            documents = await lightrag_client.list_documents()
        except LightRAGOfflineError as exc:
            raise _delete_upstream_error(offline=True) from exc
        except LightRAGError as exc:
            raise _delete_upstream_error(offline=False) from exc
        if all(document.id != remote_document_id for document in documents):
            return
        if attempt + 1 < REMOTE_DELETE_POLL_ATTEMPTS:
            await asyncio.sleep(REMOTE_DELETE_POLL_INTERVAL_SECONDS)
    raise _delete_upstream_error(offline=False)


def _directory_name(value: str) -> tuple[str, str]:
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name or name in {".", ".."} or len(name) > 255 or any(character in name for character in ("/", "\\", "\x00")) or any(unicodedata.category(character).startswith("C") for character in name):
        raise _error(400, "knowledge_invalid_directory_name", "目录名称无效。")
    return name, name.casefold()


def _remote_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LightRAGContractError("invalid_document_timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


_REMOTE_STATUS_MAP: dict[str, Literal["pending", "indexing", "ready", "failed"]] = {
    "pending": "pending",
    "parsing": "indexing",
    "analyzing": "indexing",
    "preprocessed": "indexing",
    "processing": "indexing",
    "processed": "ready",
    "failed": "failed",
}


def _remote_snapshot(document: LightRAGRemoteDocument) -> KnowledgeRemoteDocumentSnapshot:
    status_value = _REMOTE_STATUS_MAP.get(document.status.casefold())
    if status_value is None:
        raise LightRAGContractError("unsupported_document_status")
    filename = normalize_knowledge_filename(document.file_path.replace("\\", "/").rsplit("/", 1)[-1])
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    failed = status_value == "failed"
    return KnowledgeRemoteDocumentSnapshot(
        remote_document_id=document.id,
        track_id=document.track_id,
        filename=filename,
        content_type=content_type,
        content_length=document.content_length,
        status=status_value,
        created_at=_remote_datetime(document.created_at),
        updated_at=_remote_datetime(document.updated_at),
        lightrag_stage=document.status.casefold(),
        failure_code="lightrag_processing_failed" if failed else None,
        failure_reason="知识服务处理失败。" if failed else None,
    )


def _directory_response(directory: dict[str, Any]) -> KnowledgeDirectoryResponse:
    return KnowledgeDirectoryResponse(
        id=directory["id"],
        parent_id=directory["parent_id"],
        name=directory["name"],
        document_count=directory.get("document_count", 0),
        child_count=directory.get("child_count", 0),
        created_at=directory["created_at"],
        updated_at=directory["updated_at"],
    )


async def _directory_exists(
    repo: KnowledgeDocumentRepository,
    *,
    owner_user_id: str,
    directory_id: str,
) -> bool:
    directories = await repo.list_directories_for_user(owner_user_id)
    return any(directory["id"] == directory_id for directory in directories)


@router.get("", response_model=KnowledgeDocumentsEnvelope)
@require_permission("knowledge", "read")
async def list_knowledge_documents(
    request: Request,
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    scope_repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDocumentsEnvelope:
    owner_user_id = _user_id(request)
    scope = await scope_repo.get_for_user(owner_user_id)
    if scope is not None and lightrag_client is not None:
        try:
            remote_documents = await lightrag_client.list_documents()
            snapshots = tuple(_remote_snapshot(document) for document in remote_documents)
        except (LightRAGError, KnowledgeUploadFilenameError) as exc:
            logger.warning("Knowledge remote document reconciliation skipped: %s", type(exc).__name__)
        else:
            await repo.sync_remote_documents(
                scope_id=scope["id"],
                owner_user_id=owner_user_id,
                documents=snapshots,
                synced_at=datetime.now(UTC),
            )

    states = await repo.list_ingestion_states_for_user(owner_user_id)
    remote_documents = await repo.list_remote_documents_for_user(owner_user_id)
    documents = [
        *(_document_response(state.document, state.job) for state in states),
        *(_remote_document_response(document) for document in remote_documents),
    ]
    documents.sort(key=lambda document: (document.created_at, document.id), reverse=True)
    return KnowledgeDocumentsEnvelope(documents=documents)


@directory_router.get("", response_model=KnowledgeDirectoriesEnvelope)
@require_permission("knowledge", "read")
async def list_knowledge_directories(
    request: Request,
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDirectoriesEnvelope:
    directories = await repo.list_directories_for_user(_user_id(request))
    return KnowledgeDirectoriesEnvelope(directories=[_directory_response(directory) for directory in directories])


@directory_router.post(
    "",
    response_model=KnowledgeDirectoryResponse,
    status_code=status.HTTP_201_CREATED,
)
@require_permission("knowledge", "write")
async def create_knowledge_directory(
    payload: KnowledgeDirectoryCreate,
    request: Request,
    scope_repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDirectoryResponse:
    owner_user_id = _user_id(request)
    scope = await scope_repo.get_for_user(owner_user_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge Scope not found")
    name, normalized_name = _directory_name(payload.name)
    try:
        directory = await repo.create_directory(
            directory_id=f"kdir-{uuid.uuid4().hex}",
            scope_id=scope["id"],
            owner_user_id=owner_user_id,
            parent_id=payload.parent_id,
            name=name,
            normalized_name=normalized_name,
            now=datetime.now(UTC),
        )
    except LookupError as exc:
        raise _error(404, "knowledge_directory_not_found", "目录不存在。") from exc
    except KnowledgeDirectoryConflictError as exc:
        raise _error(409, "knowledge_directory_name_conflict", "同级目录下已存在同名目录。") from exc
    return _directory_response(directory)


@directory_router.patch("/{directory_id}", response_model=KnowledgeDirectoryResponse)
@require_permission("knowledge", "write")
async def rename_knowledge_directory(
    directory_id: str,
    payload: KnowledgeDirectoryRename,
    request: Request,
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDirectoryResponse:
    name, normalized_name = _directory_name(payload.name)
    try:
        directory = await repo.rename_directory(
            directory_id=directory_id,
            owner_user_id=_user_id(request),
            name=name,
            normalized_name=normalized_name,
            now=datetime.now(UTC),
        )
    except KnowledgeDirectoryConflictError as exc:
        raise _error(409, "knowledge_directory_name_conflict", "同级目录下已存在同名目录。") from exc
    if directory is None:
        raise _error(404, "knowledge_directory_not_found", "目录不存在。")
    return _directory_response(directory)


@directory_router.delete("/{directory_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_permission("knowledge", "write")
async def delete_knowledge_directory(
    directory_id: str,
    request: Request,
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> Response:
    try:
        deleted = await repo.delete_directory(
            directory_id=directory_id,
            owner_user_id=_user_id(request),
        )
    except KnowledgeDirectoryNotEmptyError as exc:
        raise _error(409, "knowledge_directory_not_empty", "只能删除空目录。") from exc
    if not deleted:
        raise _error(404, "knowledge_directory_not_found", "目录不存在。")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{document_id}", response_model=KnowledgeDocumentResponse)
@require_permission("knowledge", "write")
async def move_knowledge_document(
    document_id: str,
    payload: KnowledgeDocumentMove,
    request: Request,
    repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
) -> KnowledgeDocumentResponse:
    owner_user_id = _user_id(request)
    try:
        moved = await repo.move_document_to_directory(
            document_id=document_id,
            owner_user_id=owner_user_id,
            directory_id=payload.directory_id,
            now=datetime.now(UTC),
        )
    except LookupError as exc:
        raise _error(404, "knowledge_directory_not_found", "目录不存在。") from exc
    if moved is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    if moved["source"] == "remote":
        return _remote_document_response(moved)
    state = await repo.get_ingestion_state_for_user(
        document_id=document_id,
        owner_user_id=owner_user_id,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    return _document_response(state.document, state.job)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_permission("knowledge", "write")
async def delete_knowledge_document(
    document_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    document_repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
    file_store: KnowledgeFileStore = Depends(get_knowledge_file_store),
) -> Response:
    owner_user_id = _user_id(request)
    try:
        candidate = await document_repo.get_document_delete_candidate_for_user(
            document_id=document_id,
            owner_user_id=owner_user_id,
        )
    except KnowledgeDocumentDeleteConflictError as exc:
        raise _error(409, "knowledge_document_delete_active", "文档正在处理，暂时无法删除。") from exc
    if candidate is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    remote_document_id: str | None = None
    has_remote_state = candidate.document["source"] == "remote" or candidate.document["lightrag_tracking_id"] is not None
    if has_remote_state:
        data_plane = await resolve_knowledge_base_feature(config, lightrag_client)
        if data_plane.status != "ready" or lightrag_client is None:
            raise _delete_upstream_error(offline=True)

    if candidate.document["source"] == "remote":
        remote_document_id = candidate.document["remote_document_id"]
    elif candidate.document["lightrag_tracking_id"] is not None:
        assert lightrag_client is not None
        try:
            remote_document = await lightrag_client.find_document_by_filename(candidate.document["storage_name"])
        except LightRAGOfflineError as exc:
            raise _delete_upstream_error(offline=True) from exc
        except LightRAGError as exc:
            raise _delete_upstream_error(offline=False) from exc
        if remote_document is not None:
            if remote_document.track_id != candidate.document["lightrag_tracking_id"]:
                raise _delete_upstream_error(offline=False)
            remote_document_id = remote_document.id

    if remote_document_id is not None:
        assert lightrag_client is not None
        await _delete_remote_document(lightrag_client, remote_document_id)

    try:
        deleted = await document_repo.delete_document_for_user(
            document_id=document_id,
            owner_user_id=owner_user_id,
        )
    except KnowledgeDocumentDeleteConflictError as exc:
        raise _error(409, "knowledge_document_delete_active", "文档正在处理，暂时无法删除。") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge document not found")

    if candidate.document["source"] == "managed":
        relative_path = Path(owner_user_id) / candidate.document["scope_id"] / candidate.document["storage_name"]
        try:
            await file_store.delete(relative_path)
        except Exception:
            logger.exception(
                "Failed to delete managed knowledge document file after database deletion",
                extra={"knowledge_document_id": document_id},
            )
            raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/retry",
    response_model=KnowledgeDocumentAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
@require_permission("knowledge", "write")
async def retry_knowledge_document(
    document_id: str,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
    scope_repo: KnowledgeScopeRepository = Depends(get_knowledge_scope_repo),
    document_repo: KnowledgeDocumentRepository = Depends(get_knowledge_document_repo),
    ingestion_service: KnowledgeIngestionService = Depends(get_knowledge_ingestion_service),
) -> KnowledgeDocumentAccepted:
    owner_user_id = _user_id(request)
    state = await document_repo.get_ingestion_state_for_user(
        document_id=document_id,
        owner_user_id=owner_user_id,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    scope = await scope_repo.get_for_user(owner_user_id)
    if scope is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    if not scope["enabled"]:
        raise _error(409, "knowledge_scope_disabled", "Knowledge Scope 已禁用。")

    data_plane = await resolve_knowledge_base_feature(config, lightrag_client)
    if data_plane.status != "ready" or lightrag_client is None:
        raise _error(503, "knowledge_data_plane_unavailable", data_plane.reason, status=data_plane.status)
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise _error(400, "knowledge_invalid_idempotency_key", "Idempotency-Key 不能为空。")
    try:
        result = await document_repo.retry_failed_document(
            document_id=document_id,
            owner_user_id=owner_user_id,
            retry_key=normalized_key,
            now=datetime.now(UTC),
            allowed_error_codes=MANUALLY_RETRYABLE_ERROR_CODES,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Knowledge document not found") from exc
    except KnowledgeJobRetryNotAllowedError as exc:
        raise _error(409, "knowledge_retry_not_allowed", "该任务当前不允许人工重试。") from exc
    except KnowledgeJobRetryConflictError as exc:
        raise _error(409, "knowledge_retry_already_active", "该文档已有进行中的入库任务。") from exc

    if result.activated:
        ingestion_service.schedule(
            job_id=result.job["id"],
            document=result.document,
            lightrag_client=lightrag_client,
        )
    return KnowledgeDocumentAccepted(
        document=_document_response(result.document, result.job),
        deduplicated=not result.activated,
    )


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
    directory_id: Annotated[str | None, Form()] = None,
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

    normalized_directory_id = directory_id.strip() if directory_id is not None else None
    if not normalized_directory_id:
        normalized_directory_id = None
    elif not await _directory_exists(
        document_repo,
        owner_user_id=owner_user_id,
        directory_id=normalized_directory_id,
    ):
        raise _error(404, "knowledge_directory_not_found", "目录不存在。")

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
            document=_document_response(existing.document, existing.job),
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
            directory_id=normalized_directory_id,
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
        document=_document_response(result.document, result.job),
        deduplicated=not result.created,
    )
