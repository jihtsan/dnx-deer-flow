"""Background upload and tracking for accepted Knowledge documents."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGClient,
    LightRAGContractError,
    LightRAGOfflineError,
    LightRAGTrackStatus,
)
from app.knowledge.storage import KnowledgeFileStore
from deerflow.persistence.knowledge_documents import KnowledgeDocumentRepository

logger = logging.getLogger(__name__)

_INDEXING_STATUSES = frozenset({"parsing", "analyzing", "processing", "preprocessed"})
_KNOWN_STATUSES = _INDEXING_STATUSES | {"pending", "processed", "failed"}


@dataclass(frozen=True)
class MappedKnowledgeStatus:
    status: str
    failure_code: str | None = None
    failure_reason: str | None = None


def map_lightrag_tracking_status(tracking: LightRAGTrackStatus) -> MappedKnowledgeStatus:
    """Map the pinned LightRAG states into DeerFlow's closed state machine."""
    statuses = [document.get("status") for document in tracking.documents]
    if not statuses:
        return MappedKnowledgeStatus(status="pending")
    if any(not isinstance(status, str) or status not in _KNOWN_STATUSES for status in statuses):
        return MappedKnowledgeStatus(
            status="failed",
            failure_code="lightrag_unknown_status",
            failure_reason="LightRAG 返回了暂不支持的文档状态。",
        )
    if "failed" in statuses:
        return MappedKnowledgeStatus(
            status="failed",
            failure_code="lightrag_processing_failed",
            failure_reason="LightRAG 处理文档失败。",
        )
    if all(status == "processed" for status in statuses):
        return MappedKnowledgeStatus(status="ready")
    if any(status in _INDEXING_STATUSES or status == "processed" for status in statuses):
        return MappedKnowledgeStatus(status="indexing")
    return MappedKnowledgeStatus(status="pending")


class _LightRAGClient(Protocol):
    async def upload_document(self, *, filename: str, content: bytes, content_type: str): ...

    async def get_track_status(self, track_id: str) -> LightRAGTrackStatus: ...


_SAFE_FAILURES: tuple[tuple[type[Exception], str, str], ...] = (
    (LightRAGAuthenticationError, "lightrag_authentication_failed", "LightRAG 身份验证失败。"),
    (LightRAGOfflineError, "lightrag_unavailable", "LightRAG 当前不可用。"),
    (LightRAGContractError, "lightrag_contract_error", "LightRAG 返回了不兼容的响应。"),
)


class KnowledgeIngestionService:
    """Own process-local ingestion tasks without introducing #5 retry semantics."""

    def __init__(
        self,
        *,
        repo: KnowledgeDocumentRepository,
        file_store: KnowledgeFileStore,
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._repo = repo
        self._file_store = file_store
        self._default_lightrag_client = lightrag_client
        self._poll_interval_seconds = poll_interval_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(
        self,
        *,
        job_id: str,
        document: dict[str, Any],
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
    ) -> bool:
        """Schedule one accepted job once in this process and return immediately."""
        active = self._tasks.get(job_id)
        if active is not None and not active.done():
            return False
        task = asyncio.create_task(
            self.process(job_id=job_id, document=document, lightrag_client=lightrag_client),
            name=f"knowledge-ingestion:{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda completed, current_job_id=job_id: self._task_done(current_job_id, completed))
        return True

    def _task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.error("Knowledge ingestion task ended unexpectedly for job %s", job_id)

    async def shutdown(self) -> None:
        """Cancel process-local polls before persistence is torn down."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def process(
        self,
        *,
        job_id: str,
        document: dict[str, Any],
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
    ) -> None:
        try:
            client = lightrag_client or self._default_lightrag_client
            if client is None:
                raise RuntimeError("LightRAG client is required")
            content = await self._file_store.read(
                owner_user_id=document["owner_user_id"],
                scope_id=document["scope_id"],
                storage_name=document["storage_name"],
            )
            upload = await client.upload_document(
                filename=document["storage_name"],
                content=content,
                content_type=document["content_type"],
            )
            if upload.status != "success":
                raise LightRAGContractError("upload_not_successful")
            await self._repo.set_remote_tracking(job_id=job_id, tracking_id=upload.track_id)

            while True:
                tracking = await client.get_track_status(upload.track_id)
                if tracking.track_id != upload.track_id:
                    raise LightRAGContractError("tracking_id_mismatch")
                mapped = map_lightrag_tracking_status(tracking)
                await self._repo.set_document_status(
                    job_id=job_id,
                    status=mapped.status,
                    failure_code=mapped.failure_code,
                    failure_reason=mapped.failure_reason,
                )
                if mapped.status in {"ready", "failed"}:
                    return
                await asyncio.sleep(self._poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background boundary must persist a safe terminal result
            code, reason = self._safe_failure(exc)
            await self._repo.set_document_status(
                job_id=job_id,
                status="failed",
                failure_code=code,
                failure_reason=reason,
            )

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, str]:
        for error_type, code, reason in _SAFE_FAILURES:
            if isinstance(exc, error_type):
                return code, reason
        if isinstance(exc, OSError):
            return "knowledge_file_unavailable", "已接收的原文件当前不可用。"
        return "knowledge_ingestion_failed", "文档处理任务意外失败。"
