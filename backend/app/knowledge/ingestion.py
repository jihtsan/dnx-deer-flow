"""Durable leased worker for Knowledge document ingestion."""

from __future__ import annotations

import asyncio
import inspect
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGClient,
    LightRAGConflictError,
    LightRAGConnectionError,
    LightRAGContractError,
    LightRAGOfflineError,
    LightRAGRateLimitError,
    LightRAGRequestRejectedError,
    LightRAGServerError,
    LightRAGTimeoutError,
    LightRAGTrackStatus,
)
from app.knowledge.storage import KnowledgeFileStore
from deerflow.persistence.knowledge_documents import KnowledgeDocumentRepository, KnowledgeIngestionClaim

logger = logging.getLogger(__name__)

_INDEXING_STATUSES = frozenset({"parsing", "analyzing", "processing", "preprocessed"})
_KNOWN_STATUSES = _INDEXING_STATUSES | {"pending", "processed", "failed"}

# Only failures that can plausibly be repaired by an operator are offered as
# manual retries. File/remote-document failures need a new upload instead.
MANUALLY_RETRYABLE_ERROR_CODES = frozenset(
    {
        "lightrag_authentication_failed",
        "lightrag_contract_error",
        "lightrag_connection_failed",
        "lightrag_rate_limited",
        "lightrag_server_error",
        "lightrag_timeout",
        "lightrag_unavailable",
        "lightrag_not_configured",
        "lightrag_conflict",
        "ingestion_lease_expired",
        "knowledge_ingestion_failed",
    }
)


@dataclass(frozen=True)
class MappedKnowledgeStatus:
    status: str
    failure_code: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class IngestionFailure:
    code: str
    message: str
    retryable: bool


def map_lightrag_tracking_status(tracking: LightRAGTrackStatus) -> MappedKnowledgeStatus:
    """Map the pinned LightRAG states into DeerFlow's document state model."""
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

    async def find_document_by_filename(self, filename: str): ...


class KnowledgeIngestionService:
    """Claim and execute persisted ingestion jobs through the existing worker boundary."""

    def __init__(
        self,
        *,
        repo: KnowledgeDocumentRepository,
        file_store: KnowledgeFileStore,
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
        lightrag_client_provider: Callable[[], Awaitable[LightRAGClient | _LightRAGClient | None]] | None = None,
        poll_interval_seconds: float = 2.0,
        lease_duration: timedelta = timedelta(minutes=2),
        retry_base_delay: timedelta = timedelta(seconds=5),
        retry_max_delay: timedelta = timedelta(minutes=5),
        max_concurrency: int = 4,
        max_tracking_polls_per_attempt: int = 300,
        clock: Callable[[], datetime] | None = None,
        waiter: Callable[[float], Awaitable[None]] = asyncio.sleep,
        fault_injector: Callable[[str], Awaitable[None] | None] | None = None,
        lease_owner: str | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if max_tracking_polls_per_attempt <= 0:
            raise ValueError("max_tracking_polls_per_attempt must be positive")
        self._repo = repo
        self._file_store = file_store
        self._default_lightrag_client = lightrag_client
        self._lightrag_client_provider = lightrag_client_provider
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_duration = lease_duration
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._max_concurrency = max_concurrency
        self._max_tracking_polls_per_attempt = max_tracking_polls_per_attempt
        self._clock = clock or (lambda: datetime.now(UTC))
        self._waiter = waiter
        self._fault_injector = fault_injector
        self._lease_owner = lease_owner or f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self._client_overrides: dict[str, LightRAGClient | _LightRAGClient] = {}
        self._inflight: set[asyncio.Task[None]] = set()
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None

    def start(self) -> bool:
        """Start startup recovery; pending, due retry, and expired leases are immediately visible."""
        if self._worker_task is not None and not self._worker_task.done():
            return False
        self._worker_task = asyncio.create_task(self._worker_loop(), name="knowledge-ingestion-worker")
        return True

    def schedule(
        self,
        *,
        job_id: str,
        document: dict[str, Any],
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
    ) -> bool:
        """Wake the persisted worker; the SQL job, not this signal, is the source of truth."""
        del document
        if lightrag_client is not None:
            self._client_overrides[job_id] = lightrag_client
        started = self.start()
        self._wake.set()
        return started or job_id in self._client_overrides

    async def shutdown(self) -> None:
        """Stop local execution; an in-flight lease remains recoverable after expiry."""
        task = self._worker_task
        self._worker_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        inflight = list(self._inflight)
        for claim_task in inflight:
            claim_task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        self._inflight.clear()
        self._client_overrides.clear()

    async def _worker_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self._repo.reap_exhausted_jobs(now=self._clock())
                while len(self._inflight) < self._max_concurrency:
                    execution = await self._claim_execution()
                    if execution is None:
                        break
                    claim, client = execution
                    task = asyncio.create_task(
                        self._process_claim(claim, client=client),
                        name=f"knowledge-ingestion:{claim.job['id']}",
                    )
                    self._inflight.add(task)
                    task.add_done_callback(self._inflight_done)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Knowledge ingestion worker iteration failed; retrying")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(self._poll_interval_seconds, 0.05))
            except TimeoutError:
                pass

    def _inflight_done(self, task: asyncio.Task[None]) -> None:
        self._inflight.discard(task)
        if not task.cancelled():
            try:
                task.result()
            except Exception:
                logger.exception("Knowledge ingestion claim task ended unexpectedly")
        self._wake.set()

    async def _resolve_default_client(self) -> LightRAGClient | _LightRAGClient | None:
        if self._lightrag_client_provider is not None:
            return await self._lightrag_client_provider()
        return self._default_lightrag_client

    async def _claim_execution(self) -> tuple[KnowledgeIngestionClaim, LightRAGClient | _LightRAGClient] | None:
        default_client = await self._resolve_default_client()
        if default_client is None:
            # Request-time scheduling may provide a client before a hot-reload
            # provider is available. Claim only that exact job so unrelated
            # durable work does not burn an attempt without a usable client.
            for job_id, client in tuple(self._client_overrides.items()):
                claim = await self._repo.claim_job(
                    job_id=job_id,
                    now=self._clock(),
                    lease_owner=self._lease_owner,
                    lease_duration=self._lease_duration,
                )
                if claim is not None:
                    return claim, client
            return None
        claim = await self._repo.claim_next_job(
            now=self._clock(),
            lease_owner=self._lease_owner,
            lease_duration=self._lease_duration,
        )
        if claim is None:
            return None
        return claim, self._client_overrides.get(claim.job["id"], default_client)

    async def run_once(self) -> bool:
        """Atomically claim and execute one currently eligible persisted job."""
        await self._repo.reap_exhausted_jobs(now=self._clock())
        execution = await self._claim_execution()
        if execution is None:
            return False
        claim, client = execution
        await self._process_claim(claim, client=client)
        return True

    async def process(
        self,
        *,
        job_id: str,
        document: dict[str, Any],
        lightrag_client: LightRAGClient | _LightRAGClient | None = None,
    ) -> None:
        """Compatibility entry point that still claims the persisted job atomically."""
        del document
        if lightrag_client is not None:
            self._client_overrides[job_id] = lightrag_client
        client = self._client_overrides.get(job_id)
        if client is None:
            client = await self._resolve_default_client()
        if client is None:
            return
        claim = await self._repo.claim_job(
            job_id=job_id,
            now=self._clock(),
            lease_owner=self._lease_owner,
            lease_duration=self._lease_duration,
        )
        if claim is not None:
            await self._process_claim(claim, client=client)

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    async def _process_claim(
        self,
        claim: KnowledgeIngestionClaim,
        *,
        client: LightRAGClient | _LightRAGClient,
    ) -> None:
        job_id = claim.job["id"]
        attempt_count = claim.job["attempt_count"]
        document = claim.document
        try:
            await self._fault("after_claim_commit")
            tracking_id = document["lightrag_tracking_id"]
            if not tracking_id:
                content = await self._file_store.read(
                    owner_user_id=document["owner_user_id"],
                    scope_id=document["scope_id"],
                    storage_name=document["storage_name"],
                )
                try:
                    upload = await client.upload_document(
                        filename=document["storage_name"],
                        content=content,
                        content_type=document["content_type"],
                    )
                except LightRAGConflictError:
                    remote = await client.find_document_by_filename(document["storage_name"])
                    if remote is None:
                        raise LightRAGOfflineError("reconciliation_pending") from None
                    tracking_id = remote.track_id
                else:
                    if upload.status != "success":
                        raise LightRAGRequestRejectedError("upload_not_successful")
                    tracking_id = upload.track_id
                await self._fault("after_upload_before_tracking_commit")
                attached = await self._repo.attach_remote_tracking(
                    job_id=job_id,
                    lease_owner=self._lease_owner,
                    attempt_count=attempt_count,
                    now=self._clock(),
                    tracking_id=tracking_id,
                )
                if attached is None:
                    return

            tracking_polls = 0
            while True:
                now = self._clock()
                renewed = await self._repo.renew_lease(
                    job_id=job_id,
                    lease_owner=self._lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                    lease_duration=self._lease_duration,
                )
                if not renewed:
                    return
                tracking = await client.get_track_status(tracking_id)
                if tracking.track_id != tracking_id:
                    raise LightRAGContractError("tracking_id_mismatch")
                await self._fault("after_tracking_before_status_commit")
                mapped = map_lightrag_tracking_status(tracking)
                if mapped.status == "ready":
                    await self._repo.complete_job(
                        job_id=job_id,
                        lease_owner=self._lease_owner,
                        attempt_count=attempt_count,
                        now=self._clock(),
                    )
                    return
                if mapped.status == "failed":
                    await self._repo.record_job_failure(
                        job_id=job_id,
                        lease_owner=self._lease_owner,
                        attempt_count=attempt_count,
                        now=self._clock(),
                        error_code=mapped.failure_code or "lightrag_processing_failed",
                        error_message=mapped.failure_reason or "LightRAG 处理文档失败。",
                        retryable=False,
                        base_delay=self._retry_base_delay,
                        max_delay=self._retry_max_delay,
                    )
                    return
                progressed = await self._repo.mark_job_progress(
                    job_id=job_id,
                    lease_owner=self._lease_owner,
                    attempt_count=attempt_count,
                    now=self._clock(),
                    document_status=mapped.status,
                )
                if progressed is None:
                    return
                tracking_polls += 1
                if tracking_polls >= self._max_tracking_polls_per_attempt:
                    raise LightRAGTimeoutError("tracking_attempt_exhausted")
                await self._waiter(self._poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the durable boundary records only stable sanitized metadata
            failure = self.classify_failure(exc)
            await self._repo.record_job_failure(
                job_id=job_id,
                lease_owner=self._lease_owner,
                attempt_count=attempt_count,
                now=self._clock(),
                error_code=failure.code,
                error_message=failure.message,
                retryable=failure.retryable,
                base_delay=self._retry_base_delay,
                max_delay=self._retry_max_delay,
            )
        finally:
            self._client_overrides.pop(job_id, None)

    @staticmethod
    def classify_failure(exc: Exception) -> IngestionFailure:
        if isinstance(exc, LightRAGTimeoutError):
            return IngestionFailure("lightrag_timeout", "LightRAG 响应超时。", True)
        if isinstance(exc, LightRAGConnectionError):
            return IngestionFailure("lightrag_connection_failed", "无法连接 LightRAG。", True)
        if isinstance(exc, LightRAGServerError):
            return IngestionFailure("lightrag_server_error", "LightRAG 服务暂时异常。", True)
        if isinstance(exc, LightRAGRateLimitError):
            return IngestionFailure("lightrag_rate_limited", "LightRAG 请求过于频繁。", True)
        if isinstance(exc, LightRAGOfflineError):
            return IngestionFailure("lightrag_unavailable", "LightRAG 当前不可用。", True)
        if isinstance(exc, LightRAGAuthenticationError):
            return IngestionFailure("lightrag_authentication_failed", "LightRAG 身份验证失败。", False)
        if isinstance(exc, LightRAGRequestRejectedError):
            return IngestionFailure("lightrag_request_rejected", "LightRAG 拒绝处理该文件。", False)
        if isinstance(exc, LightRAGContractError):
            return IngestionFailure("lightrag_contract_error", "LightRAG 返回了不兼容的响应。", False)
        if isinstance(exc, LightRAGConflictError):
            return IngestionFailure("lightrag_conflict", "LightRAG 文档状态暂时无法对账。", True)
        if isinstance(exc, OSError):
            return IngestionFailure("knowledge_file_unavailable", "已接收的原文件当前不可用。", False)
        if isinstance(exc, RuntimeError) and str(exc) == "lightrag_client_not_configured":
            return IngestionFailure("lightrag_not_configured", "LightRAG 尚未配置。", False)
        return IngestionFailure("knowledge_ingestion_failed", "文档处理任务意外失败。", True)

    @staticmethod
    def _safe_failure(exc: Exception) -> tuple[str, str]:
        """Compatibility helper retained for callers that only need safe text."""
        failure = KnowledgeIngestionService.classify_failure(exc)
        return failure.code, failure.message
