from __future__ import annotations

import asyncio
import hashlib
import io
import json
import stat
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.nexus_receiver import install_nexus_receiver_provider
from app.gateway.nexus_receiver.auth import ReceiverProviderError, ReceiverServicePrincipal
from app.gateway.nexus_receiver.forced_command import _read_bounded
from app.gateway.nexus_receiver.installer import UserScopedReceiverInstaller
from app.gateway.nexus_receiver.models import ReceiverCursorPage, ReceiverUser, ReceiverUserPage
from app.gateway.nexus_receiver.package_store import InMemoryReceiverPackageStore, LocalReceiverPackageStore
from app.gateway.nexus_receiver.runtime import (
    InMemoryReceiverOperationStore,
    ReceiverRuntimeHandler,
    ReceiverTargetUser,
    ReceiverTransportPrincipal,
)
from app.gateway.nexus_receiver.ssh import dispatch_forced_command, forced_command_input_limit
from app.gateway.nexus_receiver.store import SqlReceiverOperationStore
from deerflow.config.paths import Paths
from deerflow.persistence.receiver_operations.model import ReceiverOperationRow
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage


def _archive(name: str = "research-assistant", *, unsafe_name: str | None = None, version: str = "1.0.0") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            unsafe_name or f"{name}/SKILL.md",
            f"---\nname: {name}\nversion: {version}\ndescription: Receiver test skill\n---\n",
        )
    return output.getvalue()


def _symlink_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("research-assistant/SKILL.md", "---\nname: research-assistant\ndescription: test\n---\n")
        link = zipfile.ZipInfo("research-assistant/scripts/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../private")
    return output.getvalue()


def _declared_bomb_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("research-assistant/SKILL.md", "---\nname: research-assistant\ndescription: test\n---\n")
        info = zipfile.ZipInfo("research-assistant/assets/large.bin")
        info.file_size = 65 * 1024 * 1024
        archive.writestr(info, b"small")
    data = bytearray(output.getvalue())
    # ZIP writers replace file_size with the real size, so patch both central
    # and local uncompressed-size fields for an early declared-size bound.
    target = (65 * 1024 * 1024).to_bytes(4, "little")
    name = b"research-assistant/assets/large.bin"
    local = data.find(name)
    central = data.find(name, local + len(name))
    data[local - 8 : local - 4] = target
    data[central - 22 : central - 18] = target
    return bytes(data)


def _command(package: bytes, *, operation_id: str | None = None, name: str = "research-assistant") -> dict:
    return {
        "receiverOperationId": operation_id or str(uuid4()),
        "receiverBindingId": "df-primary-prod",
        "skillVersionId": "sv.research-assistant.1.0.0",
        "runtimeSkillName": name,
        "packageDigest": f"sha256:{hashlib.sha256(package).hexdigest()}",
        "packageSizeBytes": len(package),
        "packageMediaType": "application/zip",
        "policyRevision": "trust-policy-test",
        "expectedObservedState": "ABSENT",
        "actorAudit": {
            "principalId": "11111111-1111-4111-8111-111111111111",
            "action": "skill:install_for_user",
        },
        "target": {"scope": "USER", "deerFlowUserId": "df-user-42"},
    }


class _Directory:
    def __init__(self, eligible: bool = True, *, resolved_user_id: str = "df-user-42") -> None:
        self.eligible = eligible
        self.resolved_user_id = resolved_user_id
        self.calls = 0

    async def resolve_install_target(self, user_id: str) -> ReceiverTargetUser | None:
        self.calls += 1
        if user_id != "df-user-42":
            return None
        return ReceiverTargetUser(user_id=self.resolved_user_id, active=True, install_eligible=self.eligible)


class _Installer:
    def __init__(self) -> None:
        self.installed: dict[tuple[str, str], dict] = {}
        self.calls = 0
        self.activation_calls = 0

    async def install_user_skill(self, *, user_id: str, command, manifest_version: str, package: bytes) -> None:
        self.calls += 1
        self.installed[(user_id, command.runtime_skill_name)] = {
            "skillVersionId": command.skill_version_id,
            "version": manifest_version,
            "packageDigest": command.package_digest,
            "enabled": False,
            "loadState": "disabled",
        }

    async def activate_user_skill(self, *, user_id: str, runtime_skill_name: str) -> None:
        self.activation_calls += 1
        installed = self.installed[(user_id, runtime_skill_name)]
        installed["enabled"] = True
        installed["loadState"] = "loaded"

    async def observe_user_skill(self, *, user_id: str, runtime_skill_name: str):
        return self.installed.get((user_id, runtime_skill_name))


class _BrokenInstaller(_Installer):
    async def install_user_skill(self, *, user_id: str, command, manifest_version: str, package: bytes) -> None:
        raise RuntimeError("credential=top-secret package=" + package.hex())


class _BlockingInstaller(_Installer):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def install_user_skill(self, *, user_id: str, command, manifest_version: str, package: bytes) -> None:
        self.started.set()
        await self.release.wait()
        await super().install_user_skill(user_id=user_id, command=command, manifest_version=manifest_version, package=package)


class _SearchDirectory:
    async def search(self, *, principal, query, cursor, limit) -> ReceiverUserPage:
        assert principal.subject in {"nexus-receiver-test", "nexus-ssh-test"}
        assert (query, cursor, limit) == ("chen", None, 25)
        return ReceiverUserPage(
            items=[
                ReceiverUser(
                    user_id="df-user-42",
                    display_name="Chen Wei",
                    account_label="c***@example.com",
                    active=True,
                    install_eligible=True,
                )
            ],
            page=ReceiverCursorPage(has_more=False, next_cursor=None),
            observed_at=datetime(2026, 8, 11, 6, 0, tzinfo=UTC),
        )


def _principal(*actions: str) -> ReceiverServicePrincipal:
    return ReceiverServicePrincipal(
        subject="nexus-receiver-test",
        profile="oauth2_client_credentials",
        actions=frozenset(actions),  # type: ignore[arg-type]
    )


def _ssh_principal(*actions: str) -> ReceiverTransportPrincipal:
    return ReceiverTransportPrincipal(
        subject="nexus-ssh-test",
        profile="ssh_forced_command",
        actions=frozenset(actions),
    )


def _handler(*, store=None, package_store=None, directory=None, installer=None, user_directory=None, execution_owner=None, pending_recovery_notifier=None) -> ReceiverRuntimeHandler:
    return ReceiverRuntimeHandler(
        store=store or InMemoryReceiverOperationStore(),
        package_store=package_store or InMemoryReceiverPackageStore(),
        directory=directory or _Directory(),
        installer=installer or _Installer(),
        user_directory=user_directory,
        runtime_version="0.9.0-test",
        clock=lambda: datetime(2026, 8, 11, 6, 0, tzinfo=UTC),
        execution_owner=execution_owner,
        pending_recovery_notifier=pending_recovery_notifier,
    )


@pytest.mark.asyncio
async def test_user_install_closes_durable_operation_with_exact_observed_state() -> None:
    package = _archive()
    command = _command(package)
    handler = _handler()

    accepted = await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0001",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )
    operation = (await handler.recover_pending())[0]

    assert accepted.phase == "accepted"
    assert operation.phase == "succeeded"
    assert operation.observed is not None
    assert operation.observed.model_dump(by_alias=True, mode="json") == {
        "target": command["target"],
        "presence": "installed",
        "skillVersionId": command["skillVersionId"],
        "version": "1.0.0",
        "packageDigest": command["packageDigest"],
        "runtimeSkillName": command["runtimeSkillName"],
        "enabled": True,
        "loadState": "loaded",
        "freshness": "current",
        "observedAt": "2026-08-11T06:00:00Z",
    }


@pytest.mark.asyncio
async def test_submit_notifies_recovery_only_after_durable_nonterminal_acceptance() -> None:
    package = _archive()
    command = _command(package)
    store = InMemoryReceiverOperationStore()
    notifications: list[str] = []
    handler = _handler(store=store, pending_recovery_notifier=lambda: notifications.append("pending"))

    accepted = await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-submit-wakeup",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )

    assert accepted.phase == "accepted"
    assert notifications == ["pending"]
    assert (await store.get(str(accepted.operation_id))) is not None


@pytest.mark.asyncio
async def test_replay_returns_same_terminal_operation_without_reinstalling() -> None:
    package = _archive()
    command = _command(package)
    store = InMemoryReceiverOperationStore()
    installer = _Installer()
    first_handler = _handler(store=store, installer=installer)
    digest = first_handler.canonical_command_digest(command)

    accepted = await first_handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0002",
        request_sha256=digest,
        command_payload=command,
        package=package,
    )
    first = (await first_handler.recover_pending())[0]
    restarted_handler = _handler(store=store, installer=installer)
    replay = await restarted_handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0002",
        request_sha256=digest,
        command_payload=command,
        package=package,
    )

    assert replay == first
    assert accepted.phase == "accepted"
    assert installer.calls == 1


@pytest.mark.asyncio
async def test_concurrent_same_operation_replay_does_not_execute_or_terminalize_twice() -> None:
    package = _archive()
    command = _command(package)
    store = InMemoryReceiverOperationStore()
    package_store = InMemoryReceiverPackageStore()
    installer = _BlockingInstaller()
    handler = _handler(store=store, package_store=package_store, installer=installer, execution_owner="worker")
    digest = handler.canonical_command_digest(command)

    accepted = await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-concurrent-replay",
        request_sha256=digest,
        command_payload=command,
        package=package,
    )
    replay = await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-concurrent-replay",
        request_sha256=digest,
        command_payload=command,
        package=package,
    )
    first_task = asyncio.create_task(handler.recover_pending())
    await installer.started.wait()
    concurrent_recovery = await handler.recover_pending()
    installer.release.set()
    completed = (await first_task)[0]

    assert accepted.phase == replay.phase == "accepted"
    assert concurrent_recovery[0].phase == "installing"
    assert completed.phase == "succeeded"
    assert installer.calls == 1
    assert installer.activation_calls == 1


@pytest.mark.asyncio
async def test_expired_execution_owner_is_fenced_after_takeover() -> None:
    package = _archive()
    command_payload = _command(package)
    handler = _handler()
    command = handler.validate_command(command_payload)
    store = InMemoryReceiverOperationStore()
    now = datetime(2026, 8, 11, 5, 55, tzinfo=UTC)
    await store.reserve(
        idempotency_key="install-user-owner-fencing",
        request_sha256=handler.canonical_command_digest(command_payload),
        command=command,
        now=now,
    )
    assert await store.try_claim(str(command.receiver_operation_id), owner="worker-a", now=now, expires_at=now + timedelta(seconds=1))
    assert await store.try_claim(
        str(command.receiver_operation_id),
        owner="worker-b",
        now=now + timedelta(seconds=2),
        expires_at=now + timedelta(minutes=5),
    )

    with pytest.raises(ReceiverProviderError) as fenced:
        await store.transition(str(command.receiver_operation_id), phase="validating", now=now, owner="worker-a")
    claimed = await store.transition(str(command.receiver_operation_id), phase="validating", now=now, owner="worker-b")

    assert fenced.value.code == "OBSERVED_STATE_CONFLICT"
    assert claimed.operation.phase == "validating"


@pytest.mark.asyncio
async def test_nonterminal_operation_resumes_after_handler_restart() -> None:
    package = _archive()
    command = _command(package)
    store = InMemoryReceiverOperationStore()
    package_store = InMemoryReceiverPackageStore()
    handler = _handler(store=store, package_store=package_store)
    await package_store.stage(command["receiverOperationId"], command["packageDigest"], package)
    await store.reserve(
        idempotency_key="install-user-test-0003",
        request_sha256=handler.canonical_command_digest(command),
        command=handler.validate_command(command),
        now=datetime(2026, 8, 11, 5, 59, tzinfo=UTC),
    )

    recovered = await _handler(store=store, package_store=package_store).recover_pending()

    assert [operation.phase for operation in recovered] == ["succeeded"]
    assert await package_store.read(command["receiverOperationId"], command["packageDigest"]) is None


@pytest.mark.asyncio
async def test_restart_after_filesystem_commit_observes_exact_install_without_second_write() -> None:
    package = _archive()
    command_payload = _command(package)
    handler = _handler()
    command = handler.validate_command(command_payload)
    digest = handler.canonical_command_digest(command_payload)
    store = InMemoryReceiverOperationStore()
    entry, _ = await store.reserve(
        idempotency_key="install-user-restart-window",
        request_sha256=digest,
        command=command,
        now=datetime(2026, 8, 11, 5, 58, tzinfo=UTC),
    )
    await store.transition(str(command.receiver_operation_id), phase="validating", now=datetime(2026, 8, 11, 5, 58, tzinfo=UTC))
    await store.transition(str(command.receiver_operation_id), phase="installing", now=datetime(2026, 8, 11, 5, 59, tzinfo=UTC))
    installer = _Installer()
    package_store = InMemoryReceiverPackageStore()
    await package_store.stage(str(command.receiver_operation_id), command.package_digest, package)
    installer.installed[("df-user-42", "research-assistant")] = {
        "skillVersionId": command.skill_version_id,
        "version": "1.0.0",
        "packageDigest": command.package_digest,
        "enabled": True,
        "loadState": "loaded",
    }

    recovered = (await _handler(store=store, package_store=package_store, installer=installer).recover_pending())[0]

    assert recovered.phase == "succeeded"
    assert installer.calls == 0
    assert installer.activation_calls == 1


@pytest.mark.asyncio
async def test_recovery_replays_activation_idempotently_after_enable_commit() -> None:
    package = _archive()
    command_payload = _command(package)
    store = InMemoryReceiverOperationStore()
    package_store = InMemoryReceiverPackageStore()
    handler = _handler(store=store, package_store=package_store)
    command = handler.validate_command(command_payload)
    await package_store.stage(str(command.receiver_operation_id), command.package_digest, package)
    await store.reserve(
        idempotency_key="install-user-activation-replay",
        request_sha256=handler.canonical_command_digest(command_payload),
        command=command,
        now=datetime(2026, 8, 11, 5, 57, tzinfo=UTC),
    )
    for phase in ("validating", "installing", "activating"):
        await store.transition(str(command.receiver_operation_id), phase=phase, now=datetime(2026, 8, 11, 5, 58, tzinfo=UTC))
    installer = _Installer()
    installer.installed[("df-user-42", "research-assistant")] = {
        "skillVersionId": command.skill_version_id,
        "version": "1.0.0",
        "packageDigest": command.package_digest,
        "enabled": True,
        "loadState": "loaded",
    }

    recovered = await _handler(store=store, package_store=package_store, installer=installer).recover_pending()

    assert [operation.phase for operation in recovered] == ["succeeded"]
    assert installer.calls == 0
    assert installer.activation_calls == 1


@pytest.mark.asyncio
async def test_sql_operation_store_survives_process_repository_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'receiver.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ReceiverOperationRow.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    package = _archive()
    command = _command(package)
    first_store = SqlReceiverOperationStore(session_factory)
    package_store = LocalReceiverPackageStore(tmp_path / "receiver-packages")
    handler = _handler(store=first_store, package_store=package_store)
    digest = handler.canonical_command_digest(command)
    await package_store.stage(command["receiverOperationId"], command["packageDigest"], package)
    await first_store.reserve(
        idempotency_key="install-user-sql-0001",
        request_sha256=digest,
        command=handler.validate_command(command),
        now=datetime(2026, 8, 11, 5, 59, tzinfo=UTC),
    )

    recovered = await _handler(store=SqlReceiverOperationStore(session_factory), package_store=package_store).recover_pending()

    assert [operation.phase for operation in recovered] == ["succeeded"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_redacts_and_terminalizes_corrupt_staged_package(tmp_path: Path) -> None:
    package = _archive()
    command_payload = _command(package)
    store = InMemoryReceiverOperationStore()
    package_store = LocalReceiverPackageStore(tmp_path / "receiver-packages")
    handler = _handler(store=store, package_store=package_store)
    command = handler.validate_command(command_payload)
    await package_store.stage(str(command.receiver_operation_id), command.package_digest, package)
    await store.reserve(
        idempotency_key="install-user-corrupt-staging",
        request_sha256=handler.canonical_command_digest(command_payload),
        command=command,
        now=datetime(2026, 8, 11, 5, 55, tzinfo=UTC),
    )
    next((tmp_path / "receiver-packages").glob("*/*.zip")).write_bytes(b"credential=private")

    recovered = await handler.recover_pending()

    assert [operation.phase for operation in recovered] == ["rejected"]
    serialized = recovered[0].model_dump_json(by_alias=True)
    assert "credential" not in serialized
    assert "private" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda c: c.update(packageSizeBytes=c["packageSizeBytes"] + 1), "PACKAGE_INVALID"),
        (lambda c: c.update(packageDigest="sha256:" + "0" * 64), "DIGEST_MISMATCH"),
        (lambda c: c.update(runtimeSkillName="another-name"), "PACKAGE_MANIFEST_MISMATCH"),
        (
            lambda c: (
                c.update(target={"scope": "GLOBAL"}),
                c.update(actorAudit={**c["actorAudit"], "action": "skill:install_global"}),
            ),
            "GLOBAL_INSTALL_UNSUPPORTED",
        ),
    ],
)
async def test_install_rejects_invalid_package_or_scope_without_writing(mutate, code: str) -> None:
    package = _archive()
    command = _command(package)
    mutate(command)
    installer = _Installer()

    with pytest.raises(ReceiverProviderError) as raised:
        await _handler(installer=installer).submit_install(
            principal=_principal("receiver:install:user", "receiver:install:global"),
            idempotency_key="install-user-test-0004",
            request_sha256=_handler().canonical_command_digest(command),
            command_payload=command,
            package=package,
        )

    assert raised.value.code == code
    assert installer.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("package", "code"),
    [
        (_archive(unsafe_name="../escape/SKILL.md"), "PACKAGE_INVALID"),
        (_symlink_archive(), "PACKAGE_INVALID"),
        (_declared_bomb_archive(), "PACKAGE_TOO_LARGE"),
    ],
)
async def test_package_security_rejects_traversal_symlink_and_declared_zip_bomb(package: bytes, code: str) -> None:
    command = _command(package)
    with pytest.raises(ReceiverProviderError) as raised:
        await _handler().submit_install(
            principal=_principal("receiver:install:user"),
            idempotency_key="install-user-package-security",
            request_sha256=_handler().canonical_command_digest(command),
            command_payload=command,
            package=package,
        )
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_reused_idempotency_key_or_operation_id_with_changed_request_is_rejected() -> None:
    first_package = _archive()
    first_command = _command(first_package)
    store = InMemoryReceiverOperationStore()
    package_store = InMemoryReceiverPackageStore()
    handler = _handler(store=store, package_store=package_store)
    await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0005",
        request_sha256=handler.canonical_command_digest(first_command),
        command_payload=first_command,
        package=first_package,
    )

    changed = dict(first_command, skillVersionId="sv.research-assistant.2.0.0")
    with pytest.raises(ReceiverProviderError) as reused_key:
        await handler.submit_install(
            principal=_principal("receiver:install:user"),
            idempotency_key="install-user-test-0005",
            request_sha256=handler.canonical_command_digest(changed),
            command_payload=changed,
            package=first_package,
        )
    assert reused_key.value.code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(ReceiverProviderError) as reused_operation:
        await handler.submit_install(
            principal=_principal("receiver:install:user"),
            idempotency_key="install-user-test-0006",
            request_sha256=handler.canonical_command_digest(changed),
            command_payload=changed,
            package=first_package,
        )
    assert reused_operation.value.code == "IDEMPOTENCY_KEY_REUSED"

    changed_package = _archive(version="2.0.0")
    changed_digest_command = {
        **first_command,
        "skillVersionId": "sv.research-assistant.2.0.0",
        "packageDigest": f"sha256:{hashlib.sha256(changed_package).hexdigest()}",
        "packageSizeBytes": len(changed_package),
    }
    with pytest.raises(ReceiverProviderError):
        await handler.submit_install(
            principal=_principal("receiver:install:user"),
            idempotency_key="install-user-test-0006",
            request_sha256=handler.canonical_command_digest(changed_digest_command),
            command_payload=changed_digest_command,
            package=changed_package,
        )
    assert await package_store.read(first_command["receiverOperationId"], changed_digest_command["packageDigest"]) is None


@pytest.mark.asyncio
async def test_target_is_revalidated_and_name_conflict_is_stable() -> None:
    package = _archive()
    command = _command(package)
    directory = _Directory(eligible=False)
    ineligible_handler = _handler(directory=directory)
    await ineligible_handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0007",
        request_sha256=ineligible_handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )
    ineligible = (await ineligible_handler.recover_pending())[0]
    assert ineligible.phase == "rejected"
    assert ineligible.error is not None and ineligible.error.code == "TARGET_USER_NOT_ELIGIBLE"
    assert directory.calls == 1

    installer = _Installer()
    installer.installed[("df-user-42", "research-assistant")] = {
        "skillVersionId": "different-version",
        "version": "0.1.0",
        "packageDigest": "sha256:" + "f" * 64,
        "enabled": True,
        "loadState": "loaded",
    }
    conflict_handler = _handler(installer=installer)
    await conflict_handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-test-0008",
        request_sha256=conflict_handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )
    conflict = (await conflict_handler.recover_pending())[0]
    assert conflict.phase == "rejected"
    assert conflict.error is not None and conflict.error.code == "SKILL_NAME_CONFLICT"


@pytest.mark.asyncio
async def test_directory_identity_is_exact_and_eligibility_is_rechecked_during_recovery() -> None:
    package = _archive()
    command_payload = _command(package)
    mismatched = _handler(directory=_Directory(resolved_user_id="canonical-other-user"))
    await mismatched.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-directory-identity",
        request_sha256=mismatched.canonical_command_digest(command_payload),
        command_payload=command_payload,
        package=package,
    )

    mismatch = (await mismatched.recover_pending())[0]

    assert mismatch.phase == "rejected"
    assert mismatch.error is not None and mismatch.error.code == "TARGET_USER_NOT_FOUND"

    command = mismatched.validate_command(command_payload)
    store = InMemoryReceiverOperationStore()
    package_store = InMemoryReceiverPackageStore()
    now = datetime(2026, 8, 11, 5, 55, tzinfo=UTC)
    await store.reserve(
        idempotency_key="install-user-recovery-eligibility",
        request_sha256=mismatched.canonical_command_digest(command_payload),
        command=command,
        now=now,
    )
    await store.transition(str(command.receiver_operation_id), phase="validating", now=now)
    await store.transition(str(command.receiver_operation_id), phase="installing", now=now)
    await package_store.stage(str(command.receiver_operation_id), command.package_digest, package)
    installer = _Installer()

    recovered = (
        await _handler(
            store=store,
            package_store=package_store,
            directory=_Directory(eligible=False),
            installer=installer,
        ).recover_pending()
    )[0]

    assert recovered.phase == "failed"
    assert recovered.error is not None and recovered.error.code == "TARGET_USER_NOT_ELIGIBLE"
    assert installer.calls == installer.activation_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original_command",
    [
        "nexus-skill-receiver-v1 install.submit; id",
        "nexus-skill-receiver-v1 install.submit extra",
        "nexus-skill-receiver-v1 install.submit $(id)",
        "nexus-skill-receiver-v1  install.submit",
    ],
)
async def test_forced_command_rejects_injection_and_extra_arguments(original_command: str) -> None:
    result = await dispatch_forced_command(original_command, b"{}\n", handler=None)
    assert result.exit_code == 10
    assert json.loads(result.stdout)["code"] == "PACKAGE_INVALID"
    assert original_command.encode() not in result.stdout


@pytest.mark.asyncio
async def test_forced_command_is_default_deny_and_redacts_malformed_or_oversized_frames() -> None:
    malformed = await dispatch_forced_command("nexus-skill-receiver-v1 capabilities.get", b'{"credential":"secret"}\n', handler=None)
    oversized = await dispatch_forced_command("nexus-skill-receiver-v1 capabilities.get", b"{" + b"x" * 65536 + b"}\n", handler=None)

    for result in (malformed, oversized):
        assert result.exit_code == 10
        assert b"secret" not in result.stdout
        assert b"Traceback" not in result.stdout


@pytest.mark.asyncio
async def test_forced_command_rejects_noncanonical_json_frame() -> None:
    result = await dispatch_forced_command(
        "nexus-skill-receiver-v1 capabilities.get",
        b'{"contractVersion": "1.0.0", "correlationId": "receiver-ssh-test"}\n',
        handler=None,
    )
    assert result.exit_code == 10
    assert json.loads(result.stdout)["code"] == "PACKAGE_INVALID"


@pytest.mark.asyncio
async def test_forced_command_requires_externally_authenticated_principal() -> None:
    frame = b'{"contractVersion":"1.0.0","correlationId":"receiver-ssh-auth"}\n'

    result = await dispatch_forced_command(
        "nexus-skill-receiver-v1 capabilities.get",
        frame,
        handler=_handler(),
        principal=None,
    )

    assert result.exit_code == 10
    assert json.loads(result.stdout)["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_forced_command_capabilities_and_users_use_shared_handler() -> None:
    handler = _handler(user_directory=_SearchDirectory())
    principal = _ssh_principal("receiver:capabilities:read", "receiver:user-directory:read")
    capability_frame = b'{"contractVersion":"1.0.0","correlationId":"receiver-ssh-capabilities"}\n'
    users_frame = b'{"contractVersion":"1.0.0","correlationId":"receiver-ssh-users","limit":25,"query":"chen"}\n'

    capabilities = await dispatch_forced_command(
        "nexus-skill-receiver-v1 capabilities.get",
        capability_frame,
        handler=handler,
        principal=principal,
    )
    users = await dispatch_forced_command(
        "nexus-skill-receiver-v1 users.list",
        users_frame,
        handler=handler,
        principal=principal,
    )

    assert capabilities.exit_code == users.exit_code == 0
    capability_payload = json.loads(capabilities.stdout)
    assert capability_payload["transportProfile"] == "ssh_v1"
    assert capability_payload["accessMode"] == "read_only"
    assert capability_payload["authorization"]["profile"] == "ssh_forced_command"
    assert json.loads(users.stdout)["items"][0]["userId"] == "df-user-42"


def test_forced_command_entrypoint_reads_only_the_bounded_frame_or_package() -> None:
    assert _read_bounded(io.BytesIO(b"0123456789"), 5) == b"012345"
    assert forced_command_input_limit("nexus-skill-receiver-v1 capabilities.get") == 65_537
    assert forced_command_input_limit("nexus-skill-receiver-v1 install.submit") == 65_537 + 64 * 1024 * 1024


@pytest.mark.asyncio
async def test_forced_install_validates_raw_package_bytes_before_shared_handler() -> None:
    package = _archive()
    command = _command(package)
    handler = _handler()
    frame = {
        "contractVersion": "1.0.0",
        "correlationId": "receiver-ssh-test",
        "idempotencyKey": "install-user-test-0009",
        "requestSha256": handler.canonical_command_digest(command),
        "command": command,
    }
    stdin = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n" + package[:-1]

    result = await dispatch_forced_command(
        "nexus-skill-receiver-v1 install.submit",
        stdin,
        handler=handler,
        principal=_ssh_principal("receiver:install:user"),
    )

    assert result.exit_code == 10
    assert json.loads(result.stdout)["code"] == "PACKAGE_INVALID"


@pytest.mark.asyncio
async def test_forced_install_success_uses_shared_handler_and_one_canonical_document() -> None:
    package = _archive()
    command = _command(package)
    installer = _Installer()
    handler = _handler(installer=installer)
    frame = {
        "contractVersion": "1.0.0",
        "correlationId": "receiver-ssh-success",
        "idempotencyKey": "install-user-ssh-success",
        "requestSha256": handler.canonical_command_digest(command),
        "command": command,
    }
    stdin = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n" + package

    result = await dispatch_forced_command(
        "nexus-skill-receiver-v1 install.submit",
        stdin,
        handler=handler,
        principal=_ssh_principal("receiver:install:user"),
    )

    assert result.exit_code == 0
    assert result.stdout.count(b"\n") == 1
    assert json.loads(result.stdout)["phase"] == "accepted"
    assert installer.calls == 0
    assert (await handler.recover_pending())[0].phase == "succeeded"
    assert installer.calls == 1


@pytest.mark.asyncio
async def test_internal_installer_diagnostics_and_package_bytes_are_redacted_from_durable_error() -> None:
    package = _archive()
    command = _command(package)
    store = InMemoryReceiverOperationStore()
    handler = _handler(store=store, installer=_BrokenInstaller())

    await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-redaction",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )
    failed = (await handler.recover_pending())[0]

    assert failed.phase == "failed"
    assert failed.error is not None and failed.error.code == "INTERNAL_ERROR"
    entry = await store.get(command["receiverOperationId"])
    assert entry is not None and entry.operation.error is not None
    serialized = entry.operation.model_dump_json(by_alias=True)
    assert "top-secret" not in serialized
    assert package.hex() not in serialized


def test_http_operation_route_invokes_the_same_runtime_handler() -> None:
    package = _archive()
    command = _command(package)
    installer = _Installer()
    handler = _handler(installer=installer)
    app = FastAPI()

    class Authenticator:
        async def authenticate(self, request):
            return _principal("receiver:install:user")

    app.state.nexus_receiver_service_authenticator = Authenticator()
    app.state.nexus_receiver_runtime_handler = handler
    install_nexus_receiver_provider(app)

    response = TestClient(app).post(
        "/api/v1/nexus/skill-receiver/operations",
        headers={
            "X-Correlation-ID": "receiver-http-shared-handler",
            "Idempotency-Key": "install-user-http-0001",
            "X-Request-SHA256": handler.canonical_command_digest(command),
        },
        files={
            "command": (None, json.dumps(command)),
            "package": ("research-assistant.zip", package, "application/zip"),
        },
    )

    assert response.status_code == 202
    assert response.json()["phase"] == "accepted"
    assert installer.calls == 0


def test_http_capabilities_and_users_use_the_same_runtime_handler_as_ssh() -> None:
    handler = _handler(user_directory=_SearchDirectory())
    app = FastAPI()

    class Authenticator:
        async def authenticate(self, request):
            return _principal("receiver:capabilities:read", "receiver:user-directory:read")

    app.state.nexus_receiver_service_authenticator = Authenticator()
    app.state.nexus_receiver_runtime_handler = handler
    install_nexus_receiver_provider(app)
    client = TestClient(app)

    capabilities = client.get("/api/v1/nexus/skill-receiver/capabilities")
    users = client.get("/api/v1/nexus/skill-receiver/users", params={"query": "chen", "limit": 25})

    assert capabilities.status_code == users.status_code == 200
    assert capabilities.json()["transportProfile"] == "http_v1"
    assert capabilities.json()["runtimeVersion"] == "0.9.0-test"
    assert users.json()["items"][0]["userId"] == "df-user-42"


def test_http_runtime_does_not_fall_back_to_a_different_user_directory() -> None:
    handler = _handler()
    app = FastAPI()

    class Authenticator:
        async def authenticate(self, request):
            return _principal("receiver:user-directory:read")

    app.state.nexus_receiver_service_authenticator = Authenticator()
    app.state.nexus_receiver_runtime_handler = handler
    app.state.nexus_receiver_user_directory = _SearchDirectory()
    install_nexus_receiver_provider(app)

    response = TestClient(app).get("/api/v1/nexus/skill-receiver/users", params={"query": "chen", "limit": 25})

    assert response.status_code == 409
    assert response.json()["code"] == "USER_DIRECTORY_UNSUPPORTED"


class _Storage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.enabled = False

    async def ainstall_skill_from_archive_with_metadata(self, archive_path: str, *, receiver_metadata: dict) -> dict:
        self.root.mkdir(parents=True)
        (self.root / "SKILL.md").write_text("---\nname: research-assistant\ndescription: test\n---\n", encoding="utf-8")
        (self.root / ".nexus-receiver.json").write_text(json.dumps(receiver_metadata), encoding="utf-8")
        self.enabled = False
        return {"skill_name": "research-assistant"}

    def get_custom_skill_dir(self, name: str) -> Path:
        return self.root

    def set_skill_enabled_state(self, name: str, enabled: bool) -> None:
        self.enabled = enabled

    def get_skill_enabled_state(self, name: str) -> bool:
        return self.enabled

    def custom_skill_exists(self, name: str) -> bool:
        return (self.root / "SKILL.md").exists()

    def load_skills(self):
        return [type("Skill", (), {"name": "research-assistant", "enabled": self.enabled})()]


@pytest.mark.asyncio
async def test_user_scoped_installer_persists_redacted_identity_for_observation(tmp_path: Path) -> None:
    storage = _Storage(tmp_path / "research-assistant")
    adapter = UserScopedReceiverInstaller(lambda user_id: storage)
    package = _archive()
    command = _handler().validate_command(_command(package))

    await adapter.install_user_skill(user_id="df-user-42", command=command, manifest_version="1.0.0", package=package)
    installed = await adapter.observe_user_skill(user_id="df-user-42", runtime_skill_name="research-assistant")
    await adapter.activate_user_skill(user_id="df-user-42", runtime_skill_name="research-assistant")
    observed = await adapter.observe_user_skill(user_id="df-user-42", runtime_skill_name="research-assistant")

    assert installed is not None and installed["enabled"] is False and installed["loadState"] == "disabled"
    assert observed == {
        "skillVersionId": command.skill_version_id,
        "version": "1.0.0",
        "packageDigest": command.package_digest,
        "enabled": True,
        "loadState": "loaded",
    }
    metadata = (storage.root / ".nexus-receiver.json").read_text(encoding="utf-8")
    assert "principalId" not in metadata
    assert "policyRevision" not in metadata


@pytest.mark.asyncio
async def test_user_scoped_installer_offloads_blocking_storage_factory(tmp_path: Path) -> None:
    storage = _Storage(tmp_path / "research-assistant")
    factory_threads: list[int] = []

    def storage_factory(user_id: str):
        factory_threads.append(threading.get_ident())
        return storage

    adapter = UserScopedReceiverInstaller(storage_factory)
    main_thread = threading.get_ident()

    await adapter.observe_user_skill(user_id="df-user-42", runtime_skill_name="research-assistant")

    assert factory_threads and factory_threads[0] != main_thread


@pytest.mark.asyncio
async def test_user_scoped_storage_atomically_commits_receiver_metadata_disabled(tmp_path: Path, monkeypatch) -> None:
    async def no_scan(skill_dir: Path, skill_name: str, **kwargs) -> None:
        return None

    monkeypatch.setattr("deerflow.skills.installer._scan_skill_archive_contents_or_raise", no_scan)
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(base_dir=tmp_path / "runtime"))
    archive_path = tmp_path / "research-assistant.skill"
    archive_path.write_bytes(_archive())
    storage = UserScopedSkillStorage("df-user-42", host_path=str(tmp_path / "skills"))
    metadata = {
        "contractVersion": "1.0.0",
        "skillVersionId": "sv.research-assistant.1.0.0",
        "version": "1.0.0",
        "runtimeSkillName": "research-assistant",
        "packageDigest": "sha256:" + "a" * 64,
    }

    await storage.ainstall_skill_from_archive_with_metadata(archive_path, receiver_metadata=metadata)

    installed_dir = storage.get_custom_skill_dir("research-assistant")
    assert json.loads((installed_dir / ".nexus-receiver.json").read_text(encoding="utf-8")) == metadata
    assert storage.get_skill_enabled_state("research-assistant") is False


@pytest.mark.asyncio
async def test_receiver_storage_keeps_committed_target_disabled_after_permission_failure(tmp_path: Path, monkeypatch) -> None:
    async def no_scan(skill_dir: Path, skill_name: str, **kwargs) -> None:
        return None

    monkeypatch.setattr("deerflow.skills.installer._scan_skill_archive_contents_or_raise", no_scan)
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(base_dir=tmp_path / "runtime"))
    monkeypatch.setattr(
        "deerflow.skills.storage.user_scoped_skill_storage.make_skill_written_path_sandbox_readable",
        lambda *_args: (_ for _ in ()).throw(OSError("permission projection failed")),
    )
    archive_path = tmp_path / "research-assistant.skill"
    archive_path.write_bytes(_archive())
    storage = UserScopedSkillStorage("df-user-42", host_path=str(tmp_path / "skills"))

    with pytest.raises(OSError, match="permission projection failed"):
        await storage.ainstall_skill_from_archive_with_metadata(
            archive_path,
            receiver_metadata={"contractVersion": "1.0.0"},
        )

    assert storage.get_custom_skill_dir("research-assistant").exists()
    assert storage.get_skill_enabled_state("research-assistant") is False


@pytest.mark.asyncio
async def test_local_package_store_is_owner_only_atomic_and_digest_bound(tmp_path: Path) -> None:
    package = _archive()
    command = _command(package)
    store = LocalReceiverPackageStore(tmp_path / "packages")

    await store.stage(command["receiverOperationId"], command["packageDigest"], package)

    package_path = next((tmp_path / "packages").glob("*/*.zip"))
    assert stat.S_IMODE(package_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(package_path.parent.stat().st_mode) == 0o700
    assert await store.read(command["receiverOperationId"], command["packageDigest"]) == package
    await store.delete(command["receiverOperationId"], command["packageDigest"])
    assert await store.read(command["receiverOperationId"], command["packageDigest"]) is None
