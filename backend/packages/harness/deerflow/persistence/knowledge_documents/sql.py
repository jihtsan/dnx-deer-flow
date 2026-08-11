from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, false, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.knowledge_documents.model import (
    KnowledgeDirectoryRow,
    KnowledgeDocumentRow,
    KnowledgeIngestionJobRow,
    KnowledgeIngestionRetryRequestRow,
    KnowledgeRemoteDocumentRow,
)
from deerflow.persistence.knowledge_scope.model import KnowledgeScopeRow
from deerflow.utils.time import coerce_iso

DOCUMENT_STATUSES = frozenset({"pending", "indexing", "ready", "failed"})
LIGHTRAG_STAGES = frozenset({"pending", "parsing", "analyzing", "processing", "preprocessed", "processed", "failed"})
INGESTION_JOB_STATUSES = frozenset({"pending", "leased", "retry_wait", "succeeded", "dead", "cancelled"})


class KnowledgeIdempotencyConflictError(RuntimeError):
    """An idempotency key was reused for a different file fingerprint."""


class KnowledgeDocumentQuotaExceededError(RuntimeError):
    """Creating a document would exceed the Scope's transactional byte quota."""


class KnowledgeJobRetryConflictError(RuntimeError):
    """A different manual retry is already active for the document."""


class KnowledgeJobRetryNotAllowedError(RuntimeError):
    """The job is terminal and cannot be manually retried."""


class KnowledgeDirectoryConflictError(RuntimeError):
    """A sibling directory already uses the requested normalized name."""


class KnowledgeDirectoryNotEmptyError(RuntimeError):
    """A directory with child directories or documents cannot be deleted."""


class KnowledgeDocumentDeleteConflictError(RuntimeError):
    """A document with active ingestion work cannot be deleted."""


@dataclass(frozen=True)
class KnowledgeDocumentCreateResult:
    document: dict[str, Any]
    job: dict[str, Any]
    created: bool


@dataclass(frozen=True)
class KnowledgeIngestionClaim:
    document: dict[str, Any]
    job: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeManualRetryResult:
    document: dict[str, Any]
    job: dict[str, Any]
    activated: bool


@dataclass(frozen=True)
class KnowledgeDocumentDeleteCandidate:
    document: dict[str, Any]
    job: dict[str, Any] | None


@dataclass(frozen=True)
class KnowledgeRemoteDocumentSnapshot:
    remote_document_id: str
    track_id: str
    filename: str
    content_type: str
    content_length: int
    status: str
    created_at: datetime
    updated_at: datetime
    lightrag_stage: str | None = None
    lightrag_chunks_count: int | None = None
    failure_code: str | None = None
    failure_reason: str | None = None


class KnowledgeDocumentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _document_to_dict(row: KnowledgeDocumentRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_id": row.scope_id,
            "owner_user_id": row.owner_user_id,
            "directory_id": row.directory_id,
            "original_filename": row.original_filename,
            "storage_name": row.storage_name,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "content_sha256": row.content_sha256,
            "status": row.status,
            "lightrag_tracking_id": row.lightrag_tracking_id,
            "lightrag_stage": row.lightrag_stage,
            "lightrag_chunks_count": row.lightrag_chunks_count,
            "lightrag_stage_updated_at": (coerce_iso(row.lightrag_stage_updated_at) if row.lightrag_stage_updated_at is not None else None),
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "ingestion_job_id": row.ingestion_job_id,
            "created_at": coerce_iso(row.created_at),
            "updated_at": coerce_iso(row.updated_at),
            "completed_at": coerce_iso(row.completed_at) if row.completed_at is not None else None,
            "source": "managed",
            "original_available": True,
        }

    @staticmethod
    def _job_to_dict(row: KnowledgeIngestionJobRow) -> dict[str, Any]:
        data = row.to_dict()
        for key in (
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "next_attempt_at",
            "lease_expires_at",
            "last_attempt_at",
        ):
            if data.get(key) is not None:
                data[key] = coerce_iso(data[key])
        return data

    @staticmethod
    def _remote_document_to_dict(row: KnowledgeRemoteDocumentRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_id": row.scope_id,
            "owner_user_id": row.owner_user_id,
            "directory_id": row.directory_id,
            "remote_document_id": row.remote_document_id,
            "original_filename": row.original_filename,
            "content_type": row.content_type,
            "content_length": row.content_length,
            "status": row.status,
            "lightrag_tracking_id": row.lightrag_tracking_id,
            "lightrag_stage": row.lightrag_stage,
            "lightrag_chunks_count": row.lightrag_chunks_count,
            "lightrag_stage_updated_at": (coerce_iso(row.lightrag_stage_updated_at) if row.lightrag_stage_updated_at is not None else None),
            "failure_code": row.failure_code,
            "failure_reason": row.failure_reason,
            "created_at": coerce_iso(row.created_at),
            "updated_at": coerce_iso(row.updated_at),
            "last_synced_at": coerce_iso(row.last_synced_at),
            "source": "remote",
            "original_available": False,
        }

    @staticmethod
    def _directory_to_dict(row: KnowledgeDirectoryRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_id": row.scope_id,
            "owner_user_id": row.owner_user_id,
            "parent_id": row.parent_id,
            "name": row.name,
            "created_at": coerce_iso(row.created_at),
            "updated_at": coerce_iso(row.updated_at),
        }

    async def create_directory(
        self,
        *,
        directory_id: str,
        scope_id: str,
        owner_user_id: str,
        parent_id: str | None,
        name: str,
        normalized_name: str,
        now: datetime,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            scope_exists = await session.scalar(
                select(KnowledgeScopeRow.id).where(
                    KnowledgeScopeRow.id == scope_id,
                    KnowledgeScopeRow.owner_user_id == owner_user_id,
                )
            )
            if scope_exists is None:
                raise LookupError("Knowledge Scope not found")
            if parent_id is not None:
                parent_exists = await session.scalar(
                    select(KnowledgeDirectoryRow.id).where(
                        KnowledgeDirectoryRow.id == parent_id,
                        KnowledgeDirectoryRow.scope_id == scope_id,
                        KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                    )
                )
                if parent_exists is None:
                    raise LookupError("Knowledge directory not found")
            row = KnowledgeDirectoryRow(
                id=directory_id,
                scope_id=scope_id,
                owner_user_id=owner_user_id,
                parent_id=parent_id,
                name=name,
                normalized_name=normalized_name,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise KnowledgeDirectoryConflictError("knowledge_directory_name_conflict") from exc
            return self._directory_to_dict(row)

    async def list_directories_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        async with self._sf() as session:
            directories = list(
                (
                    await session.execute(
                        select(KnowledgeDirectoryRow)
                        .join(KnowledgeScopeRow, KnowledgeDirectoryRow.scope_id == KnowledgeScopeRow.id)
                        .where(
                            KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                            KnowledgeScopeRow.owner_user_id == owner_user_id,
                        )
                        .order_by(KnowledgeDirectoryRow.created_at, KnowledgeDirectoryRow.id)
                    )
                ).scalars()
            )
            managed_counts = {
                directory_id: count
                for directory_id, count in await session.execute(
                    select(KnowledgeDocumentRow.directory_id, func.count())
                    .where(
                        KnowledgeDocumentRow.owner_user_id == owner_user_id,
                        KnowledgeDocumentRow.directory_id.is_not(None),
                    )
                    .group_by(KnowledgeDocumentRow.directory_id)
                )
            }
            remote_counts = {
                directory_id: count
                for directory_id, count in await session.execute(
                    select(KnowledgeRemoteDocumentRow.directory_id, func.count())
                    .where(
                        KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                        KnowledgeRemoteDocumentRow.directory_id.is_not(None),
                    )
                    .group_by(KnowledgeRemoteDocumentRow.directory_id)
                )
            }
        child_counts: dict[str, int] = {}
        for directory in directories:
            if directory.parent_id is not None:
                child_counts[directory.parent_id] = child_counts.get(directory.parent_id, 0) + 1
        return [
            {
                **self._directory_to_dict(directory),
                "document_count": managed_counts.get(directory.id, 0) + remote_counts.get(directory.id, 0),
                "child_count": child_counts.get(directory.id, 0),
            }
            for directory in directories
        ]

    async def rename_directory(
        self,
        *,
        directory_id: str,
        owner_user_id: str,
        name: str,
        normalized_name: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(KnowledgeDirectoryRow).where(
                        KnowledgeDirectoryRow.id == directory_id,
                        KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.name = name
            row.normalized_name = normalized_name
            row.updated_at = now
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise KnowledgeDirectoryConflictError("knowledge_directory_name_conflict") from exc
            return self._directory_to_dict(row)

    async def delete_directory(self, *, directory_id: str, owner_user_id: str) -> bool:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(KnowledgeDirectoryRow).where(
                        KnowledgeDirectoryRow.id == directory_id,
                        KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            child_exists = await session.scalar(select(KnowledgeDirectoryRow.id).where(KnowledgeDirectoryRow.parent_id == row.id).limit(1))
            managed_exists = await session.scalar(select(KnowledgeDocumentRow.id).where(KnowledgeDocumentRow.directory_id == row.id).limit(1))
            remote_exists = await session.scalar(select(KnowledgeRemoteDocumentRow.id).where(KnowledgeRemoteDocumentRow.directory_id == row.id).limit(1))
            if child_exists is not None or managed_exists is not None or remote_exists is not None:
                raise KnowledgeDirectoryNotEmptyError("knowledge_directory_not_empty")
            await session.delete(row)
            await session.commit()
            return True

    async def move_document_to_directory(
        self,
        *,
        document_id: str,
        owner_user_id: str,
        directory_id: str | None,
        now: datetime,
    ) -> dict[str, Any] | None:
        async with self._sf() as session:
            if directory_id is not None:
                directory_exists = await session.scalar(
                    select(KnowledgeDirectoryRow.id).where(
                        KnowledgeDirectoryRow.id == directory_id,
                        KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                    )
                )
                if directory_exists is None:
                    raise LookupError("Knowledge directory not found")
            managed = (
                await session.execute(
                    select(KnowledgeDocumentRow).where(
                        KnowledgeDocumentRow.id == document_id,
                        KnowledgeDocumentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if managed is not None:
                managed.directory_id = directory_id
                managed.updated_at = now
                await session.commit()
                return self._document_to_dict(managed)
            remote = (
                await session.execute(
                    select(KnowledgeRemoteDocumentRow).where(
                        KnowledgeRemoteDocumentRow.id == document_id,
                        KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if remote is None:
                return None
            remote.directory_id = directory_id
            await session.commit()
            return self._remote_document_to_dict(remote)

    @staticmethod
    def _assert_document_delete_allowed(
        document: KnowledgeDocumentRow | KnowledgeRemoteDocumentRow,
        job: KnowledgeIngestionJobRow | None,
    ) -> None:
        if isinstance(document, KnowledgeRemoteDocumentRow):
            if document.status in {"pending", "indexing"}:
                raise KnowledgeDocumentDeleteConflictError("knowledge_document_delete_active")
            return
        terminal_job_statuses = {"succeeded", "dead", "cancelled"}
        if document.status == "indexing" or job is None or job.status not in terminal_job_statuses:
            raise KnowledgeDocumentDeleteConflictError("knowledge_document_delete_active")

    async def get_document_delete_candidate_for_user(
        self,
        *,
        document_id: str,
        owner_user_id: str,
    ) -> KnowledgeDocumentDeleteCandidate | None:
        async with self._sf() as session:
            managed_result = await session.execute(
                select(KnowledgeDocumentRow, KnowledgeIngestionJobRow)
                .join(KnowledgeIngestionJobRow, KnowledgeIngestionJobRow.document_id == KnowledgeDocumentRow.id)
                .where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.owner_user_id == owner_user_id,
                )
            )
            managed = managed_result.one_or_none()
            if managed is not None:
                document, job = managed
                self._assert_document_delete_allowed(document, job)
                return KnowledgeDocumentDeleteCandidate(
                    document=self._document_to_dict(document),
                    job=self._job_to_dict(job),
                )

            remote = (
                await session.execute(
                    select(KnowledgeRemoteDocumentRow).where(
                        KnowledgeRemoteDocumentRow.id == document_id,
                        KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if remote is None:
                return None
            self._assert_document_delete_allowed(remote, None)
            return KnowledgeDocumentDeleteCandidate(
                document=self._remote_document_to_dict(remote),
                job=None,
            )

    async def delete_document_for_user(self, *, document_id: str, owner_user_id: str) -> bool:
        async with self._sf() as session:
            managed_result = await session.execute(
                select(KnowledgeDocumentRow, KnowledgeIngestionJobRow)
                .join(KnowledgeIngestionJobRow, KnowledgeIngestionJobRow.document_id == KnowledgeDocumentRow.id)
                .where(
                    KnowledgeDocumentRow.id == document_id,
                    KnowledgeDocumentRow.owner_user_id == owner_user_id,
                )
                .with_for_update()
            )
            managed = managed_result.one_or_none()
            if managed is not None:
                document, job = managed
                self._assert_document_delete_allowed(document, job)
                await session.execute(delete(KnowledgeIngestionRetryRequestRow).where(KnowledgeIngestionRetryRequestRow.job_id == job.id))
                await session.delete(job)
                await session.delete(document)
                await session.commit()
                return True

            remote = (
                await session.execute(
                    select(KnowledgeRemoteDocumentRow)
                    .where(
                        KnowledgeRemoteDocumentRow.id == document_id,
                        KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if remote is None:
                await session.rollback()
                return False
            self._assert_document_delete_allowed(remote, None)
            await session.delete(remote)
            await session.commit()
            return True

    @staticmethod
    def _remote_document_id(scope_id: str, remote_document_id: str) -> str:
        digest = hashlib.sha256(f"{scope_id}:{remote_document_id}".encode()).hexdigest()[:32]
        return f"krd-{digest}"

    async def sync_remote_documents(
        self,
        *,
        scope_id: str,
        owner_user_id: str,
        documents: tuple[KnowledgeRemoteDocumentSnapshot, ...],
        synced_at: datetime,
    ) -> int:
        if any(document.status not in DOCUMENT_STATUSES for document in documents):
            raise ValueError("Unsupported remote knowledge document status")
        if any(document.content_length < 0 for document in documents):
            raise ValueError("Remote knowledge document content length must not be negative")
        if any(document.lightrag_stage is not None and document.lightrag_stage not in LIGHTRAG_STAGES for document in documents):
            raise ValueError("Unsupported LightRAG progress stage")

        async with self._sf() as session:
            scope_exists = await session.scalar(
                select(KnowledgeScopeRow.id).where(
                    KnowledgeScopeRow.id == scope_id,
                    KnowledgeScopeRow.owner_user_id == owner_user_id,
                )
            )
            if scope_exists is None:
                raise LookupError("Knowledge Scope not found")

            managed_rows = (
                await session.execute(
                    select(
                        KnowledgeDocumentRow.lightrag_tracking_id,
                        KnowledgeDocumentRow.storage_name,
                    ).where(
                        KnowledgeDocumentRow.scope_id == scope_id,
                        KnowledgeDocumentRow.owner_user_id == owner_user_id,
                    )
                )
            ).all()
            managed_tracking_ids = {tracking_id for tracking_id, _storage_name in managed_rows if tracking_id}
            managed_storage_names = {storage_name for _tracking_id, storage_name in managed_rows}
            unmanaged = tuple(document for document in documents if document.track_id not in managed_tracking_ids and document.filename not in managed_storage_names)
            remote_ids = {document.remote_document_id for document in unmanaged}
            for document in unmanaged:
                values = {
                    "id": self._remote_document_id(scope_id, document.remote_document_id),
                    "scope_id": scope_id,
                    "owner_user_id": owner_user_id,
                    "remote_document_id": document.remote_document_id,
                    "original_filename": document.filename,
                    "content_type": document.content_type,
                    "content_length": document.content_length,
                    "status": document.status,
                    "lightrag_tracking_id": document.track_id,
                    "lightrag_stage": document.lightrag_stage,
                    "lightrag_chunks_count": document.lightrag_chunks_count,
                    "lightrag_stage_updated_at": document.updated_at,
                    "failure_code": document.failure_code,
                    "failure_reason": document.failure_reason,
                    "created_at": document.created_at,
                    "updated_at": document.updated_at,
                    "last_synced_at": synced_at,
                }
                dialect_name = session.get_bind().dialect.name
                if dialect_name == "sqlite":
                    statement = sqlite_insert(KnowledgeRemoteDocumentRow).values(**values)
                elif dialect_name == "postgresql":
                    statement = postgresql_insert(KnowledgeRemoteDocumentRow).values(**values)
                else:
                    raise RuntimeError(f"Unsupported database backend: {dialect_name}")
                excluded = statement.excluded
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        KnowledgeRemoteDocumentRow.scope_id,
                        KnowledgeRemoteDocumentRow.remote_document_id,
                    ],
                    set_={
                        "owner_user_id": excluded.owner_user_id,
                        "original_filename": excluded.original_filename,
                        "content_type": excluded.content_type,
                        "content_length": excluded.content_length,
                        "status": excluded.status,
                        "lightrag_tracking_id": excluded.lightrag_tracking_id,
                        "lightrag_stage": excluded.lightrag_stage,
                        "lightrag_chunks_count": excluded.lightrag_chunks_count,
                        "lightrag_stage_updated_at": excluded.lightrag_stage_updated_at,
                        "failure_code": excluded.failure_code,
                        "failure_reason": excluded.failure_reason,
                        "created_at": excluded.created_at,
                        "updated_at": excluded.updated_at,
                        "last_synced_at": excluded.last_synced_at,
                    },
                    where=KnowledgeRemoteDocumentRow.last_synced_at < synced_at,
                )
                await session.execute(statement)

            await session.execute(
                delete(KnowledgeRemoteDocumentRow).where(
                    KnowledgeRemoteDocumentRow.scope_id == scope_id,
                    KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                    KnowledgeRemoteDocumentRow.remote_document_id.not_in(remote_ids),
                    KnowledgeRemoteDocumentRow.last_synced_at < synced_at,
                )
            )
            await session.commit()
            return len(unmanaged)

    async def list_remote_documents_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        statement = (
            select(KnowledgeRemoteDocumentRow)
            .join(KnowledgeScopeRow, KnowledgeRemoteDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .order_by(KnowledgeRemoteDocumentRow.created_at.desc(), KnowledgeRemoteDocumentRow.id.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(statement)).scalars()
            return [self._remote_document_to_dict(row) for row in rows]

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
        directory_id: str | None = None,
        max_total_size_bytes: int | None = None,
        max_attempts: int = 5,
    ) -> KnowledgeDocumentCreateResult:
        if max_total_size_bytes is not None and max_total_size_bytes < 0:
            raise ValueError("max_total_size_bytes must not be negative")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
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

            if directory_id is not None:
                directory_exists = await session.scalar(
                    select(KnowledgeDirectoryRow.id).where(
                        KnowledgeDirectoryRow.id == directory_id,
                        KnowledgeDirectoryRow.scope_id == scope_id,
                        KnowledgeDirectoryRow.owner_user_id == owner_user_id,
                    )
                )
                if directory_exists is None:
                    raise LookupError("Knowledge directory not found")

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
                directory_id=directory_id,
                idempotency_key=idempotency_key,
                original_filename=original_filename,
                storage_name=storage_name,
                content_type=content_type,
                size_bytes=size_bytes,
                content_sha256=content_sha256,
                status="pending",
                lightrag_stage="pending",
                lightrag_stage_updated_at=now,
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
                attempt_count=0,
                max_attempts=max_attempts,
                next_attempt_at=now,
                manual_retry_count=0,
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

    @staticmethod
    def _claim_eligibility(now: datetime):
        return (
            KnowledgeIngestionJobRow.attempt_count < KnowledgeIngestionJobRow.max_attempts,
            or_(
                KnowledgeIngestionJobRow.status == "pending",
                ((KnowledgeIngestionJobRow.status == "retry_wait") & (KnowledgeIngestionJobRow.next_attempt_at.is_not(None)) & (KnowledgeIngestionJobRow.next_attempt_at <= now)),
                ((KnowledgeIngestionJobRow.status == "leased") & (KnowledgeIngestionJobRow.lease_expires_at.is_not(None)) & (KnowledgeIngestionJobRow.lease_expires_at <= now)),
            ),
        )

    async def claim_next_job(
        self,
        *,
        now: datetime,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> KnowledgeIngestionClaim | None:
        if not lease_owner:
            raise ValueError("lease_owner must not be empty")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")

        eligible = self._claim_eligibility(now)
        candidate = select(KnowledgeIngestionJobRow.id).where(*eligible).order_by(KnowledgeIngestionJobRow.created_at, KnowledgeIngestionJobRow.id).limit(1).scalar_subquery()
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(KnowledgeIngestionJobRow.id == candidate, *eligible)
            .values(
                status="leased",
                attempt_count=KnowledgeIngestionJobRow.attempt_count + 1,
                next_attempt_at=None,
                lease_owner=lease_owner,
                lease_expires_at=now + lease_duration,
                last_attempt_at=now,
                started_at=func.coalesce(KnowledgeIngestionJobRow.started_at, now),
                updated_at=now,
                completed_at=None,
            )
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def claim_job(
        self,
        *,
        job_id: str,
        now: datetime,
        lease_owner: str,
        lease_duration: timedelta,
    ) -> KnowledgeIngestionClaim | None:
        if not lease_owner:
            raise ValueError("lease_owner must not be empty")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(KnowledgeIngestionJobRow.id == job_id, *self._claim_eligibility(now))
            .values(
                status="leased",
                attempt_count=KnowledgeIngestionJobRow.attempt_count + 1,
                next_attempt_at=None,
                lease_owner=lease_owner,
                lease_expires_at=now + lease_duration,
                last_attempt_at=now,
                started_at=func.coalesce(KnowledgeIngestionJobRow.started_at, now),
                updated_at=now,
                completed_at=None,
            )
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def reap_exhausted_jobs(self, *, now: datetime) -> int:
        """Terminalize due jobs that cannot legally receive another attempt."""
        exhausted = (
            KnowledgeIngestionJobRow.attempt_count >= KnowledgeIngestionJobRow.max_attempts,
            or_(
                KnowledgeIngestionJobRow.status == "pending",
                ((KnowledgeIngestionJobRow.status == "retry_wait") & (KnowledgeIngestionJobRow.next_attempt_at.is_not(None)) & (KnowledgeIngestionJobRow.next_attempt_at <= now)),
                ((KnowledgeIngestionJobRow.status == "leased") & (KnowledgeIngestionJobRow.lease_expires_at.is_not(None)) & (KnowledgeIngestionJobRow.lease_expires_at <= now)),
            ),
        )
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(*exhausted)
            .values(
                status="dead",
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code="ingestion_lease_expired",
                last_error_message="入库任务在最后一次尝试中断后未能恢复。",
                completed_at=now,
                updated_at=now,
            )
            .returning(KnowledgeIngestionJobRow.document_id)
        )
        async with self._sf() as session:
            document_ids = list((await session.execute(statement)).scalars())
            if document_ids:
                await session.execute(
                    update(KnowledgeDocumentRow)
                    .where(KnowledgeDocumentRow.id.in_(document_ids))
                    .values(
                        status="failed",
                        lightrag_stage="failed",
                        lightrag_stage_updated_at=now,
                        failure_code="ingestion_lease_expired",
                        failure_reason="入库任务在最后一次尝试中断后未能恢复。",
                        completed_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
            return len(document_ids)

    @staticmethod
    def _active_lease_fence(
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
    ):
        return (
            KnowledgeIngestionJobRow.id == job_id,
            KnowledgeIngestionJobRow.status == "leased",
            KnowledgeIngestionJobRow.lease_owner == lease_owner,
            KnowledgeIngestionJobRow.attempt_count == attempt_count,
            KnowledgeIngestionJobRow.lease_expires_at.is_not(None),
            KnowledgeIngestionJobRow.lease_expires_at > now,
        )

    async def renew_lease(
        self,
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                *self._active_lease_fence(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                )
            )
            .values(lease_expires_at=now + lease_duration, updated_at=now)
            .returning(KnowledgeIngestionJobRow.id)
        )
        async with self._sf() as session:
            renewed = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            return renewed is not None

    async def attach_remote_tracking(
        self,
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
        tracking_id: str,
    ) -> KnowledgeIngestionClaim | None:
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                *self._active_lease_fence(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                )
            )
            .values(updated_at=now)
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            if document.lightrag_tracking_id not in {None, tracking_id}:
                await session.rollback()
                return None
            document.lightrag_tracking_id = tracking_id
            document.status = "indexing"
            document.lightrag_stage = "pending"
            document.lightrag_chunks_count = None
            document.lightrag_stage_updated_at = now
            document.failure_code = None
            document.failure_reason = None
            document.completed_at = None
            document.updated_at = now
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def mark_job_progress(
        self,
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
        document_status: str,
        lightrag_stage: str | None = None,
        lightrag_chunks_count: int | None = None,
    ) -> KnowledgeIngestionClaim | None:
        if document_status not in {"pending", "indexing"}:
            raise ValueError("document_status must be pending or indexing")
        if lightrag_stage is not None and lightrag_stage not in LIGHTRAG_STAGES - {"processed", "failed"}:
            raise ValueError("Unsupported active LightRAG progress stage")
        if lightrag_chunks_count is not None and lightrag_chunks_count < 0:
            raise ValueError("lightrag_chunks_count must not be negative")
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                *self._active_lease_fence(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                )
            )
            .values(updated_at=now)
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            document.status = "indexing" if document.lightrag_tracking_id else document_status
            if lightrag_stage is not None and document.lightrag_stage != lightrag_stage:
                document.lightrag_stage = lightrag_stage
                document.lightrag_stage_updated_at = now
            if lightrag_chunks_count is not None:
                document.lightrag_chunks_count = lightrag_chunks_count
            document.failure_code = None
            document.failure_reason = None
            document.completed_at = None
            document.updated_at = now
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def complete_job(
        self,
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
        lightrag_chunks_count: int | None = None,
    ) -> KnowledgeIngestionClaim | None:
        if lightrag_chunks_count is not None and lightrag_chunks_count < 0:
            raise ValueError("lightrag_chunks_count must not be negative")
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                *self._active_lease_fence(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                )
            )
            .values(
                status="succeeded",
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=None,
                last_error_code=None,
                last_error_message=None,
                completed_at=now,
                updated_at=now,
            )
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            document.status = "ready"
            document.lightrag_stage = "processed"
            if lightrag_chunks_count is not None:
                document.lightrag_chunks_count = lightrag_chunks_count
            document.lightrag_stage_updated_at = now
            document.failure_code = None
            document.failure_reason = None
            document.completed_at = now
            document.updated_at = now
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def record_job_failure(
        self,
        *,
        job_id: str,
        lease_owner: str,
        attempt_count: int,
        now: datetime,
        error_code: str,
        error_message: str,
        retryable: bool,
        base_delay: timedelta,
        max_delay: timedelta,
        lightrag_chunks_count: int | None = None,
    ) -> KnowledgeIngestionClaim | None:
        if base_delay <= timedelta(0) or max_delay <= timedelta(0):
            raise ValueError("retry delays must be positive")
        if lightrag_chunks_count is not None and lightrag_chunks_count < 0:
            raise ValueError("lightrag_chunks_count must not be negative")
        delay_seconds = min(
            max_delay.total_seconds(),
            base_delay.total_seconds() * (2 ** max(attempt_count - 1, 0)),
        )
        can_retry = KnowledgeIngestionJobRow.attempt_count < KnowledgeIngestionJobRow.max_attempts if retryable else false()
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                *self._active_lease_fence(
                    job_id=job_id,
                    lease_owner=lease_owner,
                    attempt_count=attempt_count,
                    now=now,
                )
            )
            .values(
                status=case((can_retry, "retry_wait"), else_="dead"),
                next_attempt_at=case(
                    (can_retry, now + timedelta(seconds=delay_seconds)),
                    else_=None,
                ),
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error_code,
                last_error_message=error_message,
                completed_at=case((can_retry, None), else_=now),
                updated_at=now,
            )
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            if job.status == "retry_wait":
                document.status = "indexing" if document.lightrag_tracking_id else "pending"
                document.failure_code = None
                document.failure_reason = None
                document.completed_at = None
            else:
                document.status = "failed"
                document.lightrag_stage = "failed"
                if lightrag_chunks_count is not None:
                    document.lightrag_chunks_count = lightrag_chunks_count
                document.lightrag_stage_updated_at = now
                document.failure_code = error_code
                document.failure_reason = error_message
                document.completed_at = now
            document.updated_at = now
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def cancel_job(self, *, job_id: str, now: datetime) -> KnowledgeIngestionClaim | None:
        statement = (
            update(KnowledgeIngestionJobRow)
            .where(
                KnowledgeIngestionJobRow.id == job_id,
                KnowledgeIngestionJobRow.status.in_(("pending", "leased", "retry_wait", "dead")),
            )
            .values(
                status="cancelled",
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code="ingestion_cancelled",
                last_error_message="入库任务已取消。",
                completed_at=now,
                updated_at=now,
            )
            .returning(KnowledgeIngestionJobRow)
        )
        async with self._sf() as session:
            job = (await session.execute(statement)).scalar_one_or_none()
            if job is None:
                await session.rollback()
                return None
            document = await session.get(KnowledgeDocumentRow, job.document_id)
            if document is None:
                await session.rollback()
                return None
            document.status = "failed"
            document.lightrag_stage = "failed"
            document.lightrag_stage_updated_at = now
            document.failure_code = "ingestion_cancelled"
            document.failure_reason = "入库任务已取消。"
            document.completed_at = now
            document.updated_at = now
            await session.commit()
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def retry_failed_document(
        self,
        *,
        document_id: str,
        owner_user_id: str,
        retry_key: str,
        now: datetime,
        allowed_error_codes: frozenset[str] | set[str] | None = None,
    ) -> KnowledgeManualRetryResult:
        if not retry_key:
            raise ValueError("retry_key must not be empty")
        async with self._sf() as session:
            document = (
                await session.execute(
                    select(KnowledgeDocumentRow).where(
                        KnowledgeDocumentRow.id == document_id,
                        KnowledgeDocumentRow.owner_user_id == owner_user_id,
                    )
                )
            ).scalar_one_or_none()
            if document is None:
                raise LookupError("Knowledge document not found")
            job = (
                await session.execute(
                    select(KnowledgeIngestionJobRow).where(
                        KnowledgeIngestionJobRow.document_id == document.id,
                    )
                )
            ).scalar_one()
            job_id = job.id
            persisted_document_id = document.id
            retry_request = await session.get(
                KnowledgeIngestionRetryRequestRow,
                (job.id, retry_key),
            )
            if retry_request is not None:
                return KnowledgeManualRetryResult(
                    document=self._document_to_dict(document),
                    job=self._job_to_dict(job),
                    activated=False,
                )
            if job.status in {"cancelled", "succeeded"}:
                raise KnowledgeJobRetryNotAllowedError("knowledge_job_retry_not_allowed")
            if job.status != "dead":
                raise KnowledgeJobRetryConflictError("knowledge_job_retry_already_active")
            if allowed_error_codes is not None and job.last_error_code not in allowed_error_codes:
                raise KnowledgeJobRetryNotAllowedError("knowledge_job_retry_not_allowed")

            statement = (
                update(KnowledgeIngestionJobRow)
                .where(
                    KnowledgeIngestionJobRow.id == job_id,
                    KnowledgeIngestionJobRow.status == "dead",
                    or_(
                        KnowledgeIngestionJobRow.last_manual_retry_key.is_(None),
                        KnowledgeIngestionJobRow.last_manual_retry_key != retry_key,
                    ),
                )
                .values(
                    status="pending",
                    attempt_count=0,
                    next_attempt_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=None,
                    last_error_message=None,
                    last_manual_retry_key=retry_key,
                    manual_retry_count=KnowledgeIngestionJobRow.manual_retry_count + 1,
                    completed_at=None,
                    updated_at=now,
                )
                .returning(KnowledgeIngestionJobRow)
            )
            activated_job = (await session.execute(statement)).scalar_one_or_none()
            if activated_job is None:
                await session.rollback()
                async with self._sf() as replay_session:
                    replay_job = (await replay_session.execute(select(KnowledgeIngestionJobRow).where(KnowledgeIngestionJobRow.id == job_id))).scalar_one()
                    replay_document = await replay_session.get(KnowledgeDocumentRow, persisted_document_id)
                    replay_request = await replay_session.get(
                        KnowledgeIngestionRetryRequestRow,
                        (job_id, retry_key),
                    )
                    if replay_request is not None and replay_document is not None:
                        return KnowledgeManualRetryResult(
                            document=self._document_to_dict(replay_document),
                            job=self._job_to_dict(replay_job),
                            activated=False,
                        )
                raise KnowledgeJobRetryConflictError("knowledge_job_retry_already_active")

            document.status = "indexing" if document.lightrag_tracking_id else "pending"
            document.lightrag_stage = "pending"
            document.lightrag_stage_updated_at = now
            document.failure_code = None
            document.failure_reason = None
            document.completed_at = None
            document.updated_at = now
            session.add(
                KnowledgeIngestionRetryRequestRow(
                    job_id=job_id,
                    retry_key=retry_key,
                    created_at=now,
                )
            )
            await session.commit()
            return KnowledgeManualRetryResult(
                document=self._document_to_dict(document),
                job=self._job_to_dict(activated_job),
                activated=True,
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

    async def get_ingestion_state_for_user(
        self,
        *,
        document_id: str,
        owner_user_id: str,
    ) -> KnowledgeIngestionClaim | None:
        statement = (
            select(KnowledgeDocumentRow, KnowledgeIngestionJobRow)
            .join(
                KnowledgeIngestionJobRow,
                KnowledgeIngestionJobRow.document_id == KnowledgeDocumentRow.id,
            )
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.id == document_id,
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
        )
        async with self._sf() as session:
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return None
            document, job = row
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
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

    async def list_ingestion_states_for_user(self, owner_user_id: str) -> list[KnowledgeIngestionClaim]:
        statement = (
            select(KnowledgeDocumentRow, KnowledgeIngestionJobRow)
            .join(
                KnowledgeIngestionJobRow,
                KnowledgeIngestionJobRow.document_id == KnowledgeDocumentRow.id,
            )
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .order_by(KnowledgeDocumentRow.created_at.desc(), KnowledgeDocumentRow.id.desc())
        )
        async with self._sf() as session:
            rows = (await session.execute(statement)).all()
            return [
                KnowledgeIngestionClaim(
                    document=self._document_to_dict(document),
                    job=self._job_to_dict(job),
                )
                for document, job in rows
            ]

    async def document_stats_for_user(self, owner_user_id: str) -> dict[str, int]:
        managed_statement = (
            select(KnowledgeDocumentRow.status, func.count())
            .join(KnowledgeScopeRow, KnowledgeDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .group_by(KnowledgeDocumentRow.status)
        )
        remote_statement = (
            select(KnowledgeRemoteDocumentRow.status, func.count())
            .join(KnowledgeScopeRow, KnowledgeRemoteDocumentRow.scope_id == KnowledgeScopeRow.id)
            .where(
                KnowledgeRemoteDocumentRow.owner_user_id == owner_user_id,
                KnowledgeScopeRow.owner_user_id == owner_user_id,
            )
            .group_by(KnowledgeRemoteDocumentRow.status)
        )
        async with self._sf() as session:
            counts = {status: count for status, count in await session.execute(managed_statement)}
            for status, count in await session.execute(remote_statement):
                counts[status] = counts.get(status, 0) + count
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

    @staticmethod
    def _legacy_lease_owner(job_id: str) -> str:
        return f"legacy:{job_id}"[:128]

    async def _legacy_claim_state(self, *, job_id: str, now: datetime) -> KnowledgeIngestionClaim | None:
        """Keep the pre-lease repository helpers behind the same fencing rules."""
        lease_owner = self._legacy_lease_owner(job_id)
        claim = await self.claim_job(
            job_id=job_id,
            now=now,
            lease_owner=lease_owner,
            lease_duration=timedelta(minutes=5),
        )
        if claim is not None:
            return claim
        async with self._sf() as session:
            statement = (
                select(KnowledgeDocumentRow, KnowledgeIngestionJobRow)
                .join(
                    KnowledgeIngestionJobRow,
                    KnowledgeIngestionJobRow.document_id == KnowledgeDocumentRow.id,
                )
                .where(
                    KnowledgeIngestionJobRow.id == job_id,
                    KnowledgeIngestionJobRow.status == "leased",
                    KnowledgeIngestionJobRow.lease_owner == lease_owner,
                    KnowledgeIngestionJobRow.lease_expires_at.is_not(None),
                    KnowledgeIngestionJobRow.lease_expires_at > now,
                )
            )
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return None
            document, job = row
            return KnowledgeIngestionClaim(
                document=self._document_to_dict(document),
                job=self._job_to_dict(job),
            )

    async def set_remote_tracking(self, *, job_id: str, tracking_id: str) -> dict[str, Any] | None:
        """Compatibility helper; new workers use :meth:`attach_remote_tracking`."""
        now = datetime.now(UTC)
        claim = await self._legacy_claim_state(job_id=job_id, now=now)
        if claim is None:
            return None
        attached = await self.attach_remote_tracking(
            job_id=job_id,
            lease_owner=claim.job["lease_owner"],
            attempt_count=claim.job["attempt_count"],
            now=now,
            tracking_id=tracking_id,
        )
        return attached.document if attached is not None else None

    async def set_document_status(
        self,
        *,
        job_id: str,
        status: str,
        failure_code: str | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Compatibility helper that cannot bypass lease or terminal-state fences."""
        if status not in DOCUMENT_STATUSES:
            raise ValueError(f"Unsupported knowledge document status: {status}")
        now = datetime.now(UTC)
        claim = await self._legacy_claim_state(job_id=job_id, now=now)
        if claim is None:
            return None
        kwargs = {
            "job_id": job_id,
            "lease_owner": claim.job["lease_owner"],
            "attempt_count": claim.job["attempt_count"],
            "now": now,
        }
        if status == "ready":
            result = await self.complete_job(**kwargs)
        elif status == "failed":
            result = await self.record_job_failure(
                **kwargs,
                error_code=failure_code or "knowledge_ingestion_failed",
                error_message=failure_reason or "文档处理失败。",
                retryable=False,
                base_delay=timedelta(seconds=1),
                max_delay=timedelta(seconds=1),
            )
        else:
            result = await self.mark_job_progress(
                **kwargs,
                document_status=status,
            )
        return result.document if result is not None else None
