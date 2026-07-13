from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.knowledge_documents.model import KnowledgeDocumentRow, KnowledgeIngestionJobRow
from deerflow.persistence.knowledge_scope.model import KnowledgeScopeRow
from deerflow.utils.time import coerce_iso

DOCUMENT_STATUSES = frozenset({"pending", "indexing", "ready", "failed"})


class KnowledgeIdempotencyConflictError(RuntimeError):
    """An idempotency key was reused for a different file fingerprint."""


class KnowledgeDocumentQuotaExceededError(RuntimeError):
    """Creating a document would exceed the Scope's transactional byte quota."""


@dataclass(frozen=True)
class KnowledgeDocumentCreateResult:
    document: dict[str, Any]
    job: dict[str, Any]
    created: bool


class KnowledgeDocumentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _document_to_dict(row: KnowledgeDocumentRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_id": row.scope_id,
            "owner_user_id": row.owner_user_id,
            "original_filename": row.original_filename,
            "storage_name": row.storage_name,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "content_sha256": row.content_sha256,
            "status": row.status,
            "lightrag_tracking_id": row.lightrag_tracking_id,
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "ingestion_job_id": row.ingestion_job_id,
            "created_at": coerce_iso(row.created_at),
            "updated_at": coerce_iso(row.updated_at),
            "completed_at": coerce_iso(row.completed_at) if row.completed_at is not None else None,
        }

    @staticmethod
    def _job_to_dict(row: KnowledgeIngestionJobRow) -> dict[str, Any]:
        data = row.to_dict()
        for key in ("created_at", "updated_at", "started_at", "completed_at"):
            if data.get(key) is not None:
                data[key] = coerce_iso(data[key])
        return data

    @staticmethod
    def _assert_matching_fingerprint(
        row: KnowledgeDocumentRow,
        *,
        content_sha256: str,
        size_bytes: int,
    ) -> None:
        if row.content_sha256 != content_sha256 or row.size_bytes != size_bytes:
            raise KnowledgeIdempotencyConflictError("idempotency_key_reused_with_different_file")

    async def _existing_result(
        self,
        session: AsyncSession,
        *,
        scope_id: str,
        idempotency_key: str,
        content_sha256: str,
        size_bytes: int,
    ) -> KnowledgeDocumentCreateResult | None:
        document_stmt = select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.scope_id == scope_id,
            KnowledgeDocumentRow.idempotency_key == idempotency_key,
        )
        document = (await session.execute(document_stmt)).scalar_one_or_none()
        if document is None:
            return None
        self._assert_matching_fingerprint(
            document,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
        )
        job_stmt = select(KnowledgeIngestionJobRow).where(KnowledgeIngestionJobRow.document_id == document.id)
        job = (await session.execute(job_stmt)).scalar_one()
        return KnowledgeDocumentCreateResult(
            document=self._document_to_dict(document),
            job=self._job_to_dict(job),
            created=False,
        )

    async def create_document_with_job(
        self,
        *,
        document_id: str,
        job_id: str,
        scope_id: str,
        owner_user_id: str,
        idempotency_key: str,
        original_filename: str,
        storage_name: str,
        content_type: str,
        size_bytes: int,
        content_sha256: str,
        max_total_size_bytes: int | None = None,
    ) -> KnowledgeDocumentCreateResult:
        if max_total_size_bytes is not None and max_total_size_bytes < 0:
            raise ValueError("max_total_size_bytes must not be negative")
        async with self._sf() as session:
            existing = await self._existing_result(
                session,
                scope_id=scope_id,
                idempotency_key=idempotency_key,
                content_sha256=content_sha256,
                size_bytes=size_bytes,
            )
            if existing is not None:
                return existing

            scope_stmt = (
                select(KnowledgeScopeRow)
                .where(
                    KnowledgeScopeRow.id == scope_id,
                    KnowledgeScopeRow.owner_user_id == owner_user_id,
                )
                .with_for_update()
            )
            if await session.scalar(scope_stmt) is None:
                raise LookupError("Knowledge Scope not found")

            # The scope lock serializes quota accounting on PostgreSQL. Re-read
            # the idempotency key after acquiring it because another request may
            # have committed the same upload between the optimistic lookup and
            # this lock. A replay must win over quota enforcement.
            existing = await self._existing_result(
                session,
                scope_id=scope_id,
                idempotency_key=idempotency_key,
                content_sha256=content_sha256,
                size_bytes=size_bytes,
            )
            if existing is not None:
                return existing

            if max_total_size_bytes is not None:
                persisted_size = await session.scalar(
                    select(func.coalesce(func.sum(KnowledgeDocumentRow.size_bytes), 0)).where(
                        KnowledgeDocumentRow.scope_id == scope_id,
                    )
                )
                if int(persisted_size or 0) + size_bytes > max_total_size_bytes:
                    raise KnowledgeDocumentQuotaExceededError("knowledge_document_quota_exceeded")

            now = datetime.now(UTC)
            document = KnowledgeDocumentRow(
                id=document_id,
                scope_id=scope_id,
                owner_user_id=owner_user_id,
                idempotency_key=idempotency_key,
                original_filename=original_filename,
                storage_name=storage_name,
                content_type=content_type,
                size_bytes=size_bytes,
                content_sha256=content_sha256,
                status="pending",
                ingestion_job_id=job_id,
                created_at=now,
                updated_at=now,
            )
            job = KnowledgeIngestionJobRow(
                id=job_id,
                document_id=document_id,
                scope_id=scope_id,
                idempotency_key=idempotency_key,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            session.add_all((document, job))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                async with self._sf() as replay_session:
                    replay = await self._existing_result(
                        replay_session,
                        scope_id=scope_id,
                        idempotency_key=idempotency_key,
                        content_sha256=content_sha256,
                        size_bytes=size_bytes,
                    )
                if replay is not None:
                    return replay
                raise
            except Exception:
                await session.rollback()
                raise

            return KnowledgeDocumentCreateResult(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
                created=True,
            )

    async def get_idempotent_result_for_user(
        self,
        *,
        scope_id: str,
        owner_user_id: str,
        idempotency_key: str,
    ) -> KnowledgeDocumentCreateResult | None:
        document_stmt = (
            select(KnowledgeDocumentRow)
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.scope_id == scope_id,
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeDocumentRow.idempotency_key == idempotency_key,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
        )
        async with self._sf() as session:
            document = (await session.execute(document_stmt)).scalar_one_or_none()
            if document is None:
                return None
            job = (
                await session.execute(
                    select(KnowledgeIngestionJobRow).where(
                        KnowledgeIngestionJobRow.document_id == document.id,
                    )
                )
            ).scalar_one()
            return KnowledgeDocumentCreateResult(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
                created=False,
            )

    async def list_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(KnowledgeDocumentRow)
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .order_by(KnowledgeDocumentRow.created_at.desc(), KnowledgeDocumentRow.id.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars()
            return [self._document_to_dict(row) for row in rows]

    async def document_stats_for_user(self, owner_user_id: str) -> dict[str, int]:
        stmt = (
            select(KnowledgeDocumentRow.status, func.count())
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .group_by(KnowledgeDocumentRow.status)
        )
        async with self._sf() as session:
            counts = {status: count for status, count in await session.execute(stmt)}
        return {
            "total": sum(counts.values()),
            "pending": counts.get("pending", 0),
            "indexing": counts.get("indexing", 0),
            "ready": counts.get("ready", 0),
            "failed": counts.get("failed", 0),
        }

    async def list_ready_candidates_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(KnowledgeDocumentRow)
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.enabled.is_(True),
                KnowledgeDocumentRow.status == "ready",
            )
            .order_by(KnowledgeDocumentRow.created_at.desc(), KnowledgeDocumentRow.id.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars()
            return [self._document_to_dict(row) for row in rows]

    async def set_remote_tracking(self, *, job_id: str, tracking_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            job = await session.get(KnowledgeIngestionJobRow, job_id)
            if job is None:
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                return None
            now = datetime.now(UTC)
            document.lightrag_tracking_id = tracking_id
            document.updated_at = now
            job.status = "tracking"
            job.started_at = job.started_at or now
            job.updated_at = now
            await session.commit()
            return self._document_to_dict(document)

    async def set_document_status(
        self,
        *,
        job_id: str,
        status: str,
        failure_code: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Unsupported knowledge document status: {status}")
        async with self._sf() as session:
            job = await session.get(KnowledgeIngestionJobRow, job_id)
            if job is None:
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                return None

            now = datetime.now(UTC)
            document.status = status
            document.failure_code = failure_code if status == "failed" else None
            document.failure_reason = failure_reason if status == "failed" else None
            document.completed_at = now if status in {"ready", "failed"} else None
            document.updated_at = now
            job.status = "succeeded" if status == "ready" else "failed" if status == "failed" else status
            job.started_at = job.started_at or now
            job.completed_at = now if status in {"ready", "failed"} else None
            job.updated_at = now
            await session.commit()
            return self._document_to_dict(document)
