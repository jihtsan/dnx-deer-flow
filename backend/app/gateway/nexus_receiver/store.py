"""SQLAlchemy durable store for transport-neutral receiver operations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.nexus_receiver.models import (
    ReceiverInstallCommand,
    ReceiverObservedSkill,
    ReceiverOperation,
    ReceiverOperationError,
    ReceiverOperationPhase,
)
from app.gateway.nexus_receiver.runtime import (
    _TERMINAL,
    _TRANSITIONS,
    ReceiverOperationEntry,
    ReceiverRuntimeError,
)
from deerflow.persistence.receiver_operations.model import ReceiverOperationRow


class SqlReceiverOperationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _entry(row: ReceiverOperationRow) -> ReceiverOperationEntry:
        return ReceiverOperationEntry(
            idempotency_key=row.idempotency_key,
            request_sha256=row.request_sha256,
            command=ReceiverInstallCommand.model_validate(row.command_json),
            operation=ReceiverOperation.model_validate(row.operation_json),
            execution_owner=row.execution_owner,
            execution_expires_at=SqlReceiverOperationStore._as_utc(row.execution_expires_at),
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _matches(
        row: ReceiverOperationRow,
        *,
        idempotency_key: str,
        request_sha256: str,
        command_json: dict,
    ) -> bool:
        return row.idempotency_key == idempotency_key and row.request_sha256 == request_sha256 and row.command_json == command_json

    async def reserve(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        command: ReceiverInstallCommand,
        now: datetime,
    ) -> tuple[ReceiverOperationEntry, bool]:
        operation = ReceiverOperation(
            operation_id=command.receiver_operation_id,
            phase="accepted",
            target=command.target,
            skill_version_id=command.skill_version_id,
            runtime_skill_name=command.runtime_skill_name,
            package_digest=command.package_digest,
            accepted_at=now,
            updated_at=now,
        )
        command_json = command.model_dump(mode="json", by_alias=True)
        operation_json = operation.model_dump(mode="json", by_alias=True)
        row = ReceiverOperationRow(
            operation_id=str(command.receiver_operation_id),
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            phase="accepted",
            command_json=command_json,
            operation_json=operation_json,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
                return self._entry(row), True
            except IntegrityError:
                await session.rollback()
                statement = select(ReceiverOperationRow).where(
                    or_(
                        ReceiverOperationRow.operation_id == str(command.receiver_operation_id),
                        ReceiverOperationRow.idempotency_key == idempotency_key,
                    )
                )
                existing = (await session.execute(statement)).scalars().first()
                if existing is None or not self._matches(
                    existing,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha256,
                    command_json=command_json,
                ):
                    raise ReceiverRuntimeError(
                        code="IDEMPOTENCY_KEY_REUSED",
                        detail="The idempotency key or receiver operation ID is bound to a different request.",
                    ) from None
                return self._entry(existing), False

    async def get(self, operation_id: str) -> ReceiverOperationEntry | None:
        async with self._session_factory() as session:
            row = await session.get(ReceiverOperationRow, operation_id)
            return self._entry(row) if row is not None else None

    async def list_nonterminal(self) -> list[ReceiverOperationEntry]:
        async with self._session_factory() as session:
            statement = select(ReceiverOperationRow).where(ReceiverOperationRow.phase.not_in(_TERMINAL)).order_by(ReceiverOperationRow.created_at)
            return [self._entry(row) for row in (await session.execute(statement)).scalars()]

    async def try_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        async with self._session_factory() as session:
            statement = (
                update(ReceiverOperationRow)
                .where(
                    ReceiverOperationRow.operation_id == operation_id,
                    ReceiverOperationRow.phase.not_in(_TERMINAL),
                    or_(
                        ReceiverOperationRow.execution_owner.is_(None),
                        ReceiverOperationRow.execution_expires_at.is_(None),
                        ReceiverOperationRow.execution_expires_at <= now,
                    ),
                )
                .values(execution_owner=owner, execution_expires_at=expires_at)
            )
            result = await session.execute(statement)
            await session.commit()
            return result.rowcount == 1

    async def release_claim(self, operation_id: str, *, owner: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(ReceiverOperationRow)
                .where(
                    ReceiverOperationRow.operation_id == operation_id,
                    ReceiverOperationRow.execution_owner == owner,
                )
                .values(execution_owner=None, execution_expires_at=None)
            )
            await session.commit()

    async def transition(
        self,
        operation_id: str,
        *,
        phase: ReceiverOperationPhase,
        now: datetime,
        owner: str | None = None,
        observed: ReceiverObservedSkill | None = None,
        error: ReceiverOperationError | None = None,
    ) -> ReceiverOperationEntry:
        async with self._session_factory() as session:
            statement = select(ReceiverOperationRow).where(ReceiverOperationRow.operation_id == operation_id).with_for_update()
            row = (await session.execute(statement)).scalars().one()
            execution_expires_at = self._as_utc(row.execution_expires_at)
            if owner is not None and (row.execution_owner != owner or execution_expires_at is None or execution_expires_at <= self._as_utc(now)):
                raise ReceiverRuntimeError(
                    code="OBSERVED_STATE_CONFLICT",
                    detail="The receiver operation execution claim is no longer owned by this worker.",
                )
            current = ReceiverOperation.model_validate(row.operation_json)
            if phase not in _TRANSITIONS[current.phase]:
                raise ReceiverRuntimeError(
                    code="OBSERVED_STATE_CONFLICT",
                    detail="The durable receiver operation cannot make the requested phase transition.",
                )
            operation = ReceiverOperation.model_validate(
                current.model_copy(
                    update={
                        "phase": phase,
                        "updated_at": now,
                        "completed_at": now if phase in _TERMINAL else None,
                        "observed": observed,
                        "error": error,
                    }
                )
            )
            row.phase = phase
            row.operation_json = operation.model_dump(mode="json", by_alias=True)
            row.updated_at = now
            await session.commit()
            return self._entry(row)
