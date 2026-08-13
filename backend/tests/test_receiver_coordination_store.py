from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.nexus_receiver.coordination import GlobalManagedSkillCommitter, SqlReceiverCoordinationStore
from deerflow.persistence.receiver_catalog.model import ReceiverCatalogStateRow, ReceiverRecoveryLeaseRow
from deerflow.skills.storage.global_managed_skill_storage import GlobalManagedSkillStorage


def _archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "research-assistant/SKILL.md",
            "---\nname: research-assistant\nversion: 1.0.0\ndescription: Managed test Skill\n---\n",
        )


async def _allow_scan(_skill_dir: Path, _skill_name: str) -> None:
    return None


@pytest.mark.asyncio
async def test_catalog_revision_and_identity_commit_are_one_transaction(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ReceiverCatalogStateRow.metadata.create_all)
    store = SqlReceiverCoordinationStore(async_sessionmaker(engine, expire_on_commit=False))
    now = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    first = await store.commit_global_skill(
        runtime_skill_name="research-assistant",
        skill_version_id="sv.research-assistant.1.0.0",
        version="1.0.0",
        package_digest="sha256:" + "a" * 64,
        now=now,
        load_probe_succeeded=True,
    )
    replay = await store.commit_global_skill(
        runtime_skill_name="research-assistant",
        skill_version_id="sv.research-assistant.1.0.0",
        version="1.0.0",
        package_digest="sha256:" + "a" * 64,
        now=now + timedelta(seconds=1),
        load_probe_succeeded=True,
    )

    assert first.revision == replay.revision == 1
    assert await store.get_catalog_revision() == 1
    observed = await store.get_global_skill("research-assistant")
    assert observed is not None and observed.activated_revision == 1

    with pytest.raises(ValueError, match="load probe"):
        await store.commit_global_skill(
            runtime_skill_name="other-skill",
            skill_version_id="sv.other.1",
            version="1.0.0",
            package_digest="sha256:" + "b" * 64,
            now=now,
            load_probe_succeeded=False,
        )
    assert await store.get_catalog_revision() == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_singleton_recovery_lease_takeover_fences_old_token(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ReceiverRecoveryLeaseRow.metadata.create_all)
    store_a = SqlReceiverCoordinationStore(async_sessionmaker(engine, expire_on_commit=False))
    store_b = SqlReceiverCoordinationStore(async_sessionmaker(engine, expire_on_commit=False))
    now = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    token_a = await store_a.try_acquire_recovery_lease(owner="gateway-a", now=now, lease_duration=timedelta(seconds=5))
    assert token_a is not None
    assert await store_b.try_acquire_recovery_lease(owner="gateway-b", now=now, lease_duration=timedelta(seconds=5)) is None

    token_b = await store_b.try_acquire_recovery_lease(owner="gateway-b", now=now + timedelta(seconds=6), lease_duration=timedelta(seconds=5))
    assert token_b is not None and token_b != token_a
    assert not await store_a.renew_recovery_lease(owner="gateway-a", token=token_a, now=now + timedelta(seconds=6), lease_duration=timedelta(seconds=5))
    assert await store_b.renew_recovery_lease(owner="gateway-b", token=token_b, now=now + timedelta(seconds=7), lease_duration=timedelta(seconds=5))
    assert not await store_a.release_recovery_lease(owner="gateway-a", token=token_a)
    assert await store_b.release_recovery_lease(owner="gateway-b", token=token_b)
    await engine.dispose()


@pytest.mark.asyncio
async def test_global_commit_recovers_crash_after_filesystem_rename_before_catalog(tmp_path: Path) -> None:
    archive = tmp_path / "research.skill"
    _archive(archive)
    storage = GlobalManagedSkillStorage(base_dir=tmp_path / "state")
    metadata = {
        "contractVersion": "1.1.0",
        "skillVersionId": "sv.research-assistant.1.0.0",
        "version": "1.0.0",
        "runtimeSkillName": "research-assistant",
        "packageDigest": "sha256:" + "a" * 64,
    }
    await storage.install_from_archive(archive, receiver_metadata=metadata, precommit_scan=_allow_scan)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ReceiverCatalogStateRow.metadata.create_all)
    catalog = SqlReceiverCoordinationStore(async_sessionmaker(engine, expire_on_commit=False))
    committer = GlobalManagedSkillCommitter(storage=storage, catalog=catalog)

    committed = await committer.commit(
        archive,
        receiver_metadata=metadata,
        precommit_scan=_allow_scan,
        now=datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
    )

    assert committed.revision == await catalog.get_catalog_revision() == 1
    assert await catalog.get_global_skill("research-assistant") == committed
    await engine.dispose()
