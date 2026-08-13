"""Real PostgreSQL contract for Nexus receiver cross-process coordination."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.nexus_receiver.auth import ReceiverProviderError
from app.gateway.nexus_receiver.coordination import SqlReceiverCoordinationStore
from app.gateway.nexus_receiver.runtime import ReceiverRuntimeHandler
from app.gateway.nexus_receiver.store import SqlReceiverOperationStore
from deerflow.persistence.receiver_catalog.model import ReceiverCatalogStateRow, ReceiverGlobalSkillRow, ReceiverRecoveryLeaseRow
from deerflow.persistence.receiver_operations.model import ReceiverOperationRow

POSTGRES_URL = os.environ.get("DEER_FLOW_TEST_POSTGRES_URL")
_LIBPQ_ONLY_QUERY_KEYS = {"sslmode", "channel_binding"}


def _asyncpg_url(url: str | None) -> str | None:
    if not url:
        return url
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    parts = urlsplit(url)
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in _LIBPQ_ONLY_QUERY_KEYS]
    return urlunsplit(parts._replace(query=urlencode(kept)))


pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="requires DEER_FLOW_TEST_POSTGRES_URL")


@pytest_asyncio.fixture
async def postgres_session_factory():
    engine = create_async_engine(_asyncpg_url(POSTGRES_URL))
    async with engine.begin() as connection:
        await connection.run_sync(
            ReceiverOperationRow.metadata.create_all,
            tables=[
                ReceiverOperationRow.__table__,
                ReceiverCatalogStateRow.__table__,
                ReceiverGlobalSkillRow.__table__,
                ReceiverRecoveryLeaseRow.__table__,
            ],
            checkfirst=True,
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(ReceiverOperationRow))
        await session.execute(delete(ReceiverGlobalSkillRow))
        await session.execute(delete(ReceiverCatalogStateRow))
        await session.execute(delete(ReceiverRecoveryLeaseRow))
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_gateways_share_singleton_recovery_and_catalog_revision(postgres_session_factory) -> None:
    first = SqlReceiverCoordinationStore(postgres_session_factory)
    second = SqlReceiverCoordinationStore(postgres_session_factory)
    now = datetime(2026, 8, 13, 5, 0, tzinfo=UTC)

    token = await first.try_acquire_recovery_lease(owner="gateway-a", now=now, lease_duration=timedelta(seconds=5))
    assert token is not None
    assert await second.try_acquire_recovery_lease(owner="gateway-b", now=now, lease_duration=timedelta(seconds=5)) is None
    takeover = await second.try_acquire_recovery_lease(owner="gateway-b", now=now + timedelta(seconds=6), lease_duration=timedelta(seconds=5))
    assert takeover is not None
    assert not await first.renew_recovery_lease(owner="gateway-a", token=token, now=now + timedelta(seconds=6), lease_duration=timedelta(seconds=5))

    entries = await asyncio.gather(
        first.commit_global_skill(
            runtime_skill_name="skill-a",
            skill_version_id="sv.a.1",
            version="1.0.0",
            package_digest="sha256:" + "a" * 64,
            now=now,
            load_probe_succeeded=True,
        ),
        second.commit_global_skill(
            runtime_skill_name="skill-b",
            skill_version_id="sv.b.1",
            version="1.0.0",
            package_digest="sha256:" + "b" * 64,
            now=now,
            load_probe_succeeded=True,
        ),
    )
    assert {entry.revision for entry in entries} == {1, 2}
    assert await first.get_catalog_revision() == 2


@pytest.mark.asyncio
async def test_operation_claim_token_fences_reused_worker_after_takeover(postgres_session_factory) -> None:
    first = SqlReceiverOperationStore(postgres_session_factory)
    second = SqlReceiverOperationStore(postgres_session_factory)
    command_payload = {
        "receiverOperationId": "11111111-1111-4111-8111-111111111111",
        "receiverBindingId": "df-primary-prod",
        "skillVersionId": "sv.a.1",
        "runtimeSkillName": "skill-a",
        "packageDigest": "sha256:" + "a" * 64,
        "packageSizeBytes": 1,
        "packageMediaType": "application/zip",
        "policyRevision": "policy-1",
        "expectedObservedState": "ABSENT",
        "actorAudit": {"principalId": "11111111-1111-4111-8111-111111111111", "action": "skill:install_for_user"},
        "target": {"scope": "USER", "deerFlowUserId": "user-a"},
    }
    command = ReceiverRuntimeHandler.validate_command(command_payload)
    now = datetime(2026, 8, 13, 5, 0, tzinfo=UTC)
    await first.reserve(idempotency_key="postgres-operation-token", request_sha256="sha256:" + "c" * 64, command=command, now=now)
    stale = await first.try_claim(str(command.receiver_operation_id), owner="worker", now=now, expires_at=now + timedelta(seconds=1))
    current = await second.try_claim(str(command.receiver_operation_id), owner="worker", now=now + timedelta(seconds=2), expires_at=now + timedelta(minutes=1))

    assert stale is not None and current is not None and stale != current
    assert not await first.renew_claim(
        str(command.receiver_operation_id),
        owner="worker",
        token=stale,
        now=now + timedelta(seconds=2),
        expires_at=now + timedelta(minutes=2),
    )
    assert await second.renew_claim(
        str(command.receiver_operation_id),
        owner="worker",
        token=current,
        now=now + timedelta(seconds=2),
        expires_at=now + timedelta(minutes=2),
    )
    with pytest.raises(ReceiverProviderError) as fenced:
        await first.transition(
            str(command.receiver_operation_id),
            phase="validating",
            now=now + timedelta(seconds=2),
            owner="worker",
            token=stale,
        )
    assert fenced.value.code == "OBSERVED_STATE_CONFLICT"
    assert "no longer owned" in fenced.value.detail
    transitioned = await second.transition(
        str(command.receiver_operation_id),
        phase="validating",
        now=now + timedelta(seconds=2),
        owner="worker",
        token=current,
    )
    assert transitioned.operation.phase == "validating"
