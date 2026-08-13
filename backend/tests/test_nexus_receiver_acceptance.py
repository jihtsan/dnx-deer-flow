from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.nexus_receiver.acceptance import (
    ACCEPTANCE_PREPARATION_REVISION,
    ACCEPTANCE_PROFILE,
    AcceptanceFaultController,
    AcceptanceManifest,
    AcceptanceReceiverReleaseBootstrap,
    AcceptanceReceiverRuntimeHandler,
    AcceptanceRestartRequested,
    AcceptanceTransportDisconnect,
    AcceptanceUserInstaller,
    ControlledAcceptanceDirectory,
    _acceptance_precommit_scan,
    load_acceptance_settings,
)
from app.gateway.nexus_receiver.auth import ReceiverServiceAuthenticationRequired
from app.gateway.nexus_receiver.installer import UserScopedReceiverInstaller
from app.gateway.nexus_receiver.package_store import (
    InMemoryReceiverPackageStore,
    LocalReceiverPackageStore,
)
from app.gateway.nexus_receiver.runtime import (
    InMemoryReceiverOperationStore,
    ReceiverRuntimeError,
    ReceiverRuntimeHandler,
    ReceiverTransportPrincipal,
)
from app.gateway.nexus_receiver.store import SqlReceiverOperationStore
from deerflow.config.nexus_receiver_config import NexusReceiverConfig
from deerflow.config.paths import Paths
from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine
from deerflow.persistence.receiver_operations.model import ReceiverOperationRow
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage

ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = "e2fa130f24c12de4f74d4b3b2977ae92c3f7fbd4"
HARNESS_REVISION = "a" * 40


def _manifest(*, harness_revision: str | None = HARNESS_REVISION) -> dict:
    return {
        "schemaVersion": 1,
        "executionPolicy": {
            "purpose": "preparation_rehearsal_only",
            "productionWriteEnabled": False,
            "jointE2EExecuted": False,
            "globalEnabled": False,
            "fixtureSuccessIsAcceptance": False,
        },
        "revisions": {
            "nexusImplementationRevision": "3b8916c65961dd040d0a0e55a151e54cfcf4ff86",
            "deerFlowDeploymentRevision": BASE_REVISION,
            "deerFlowReceiverRuntimeRevision": "1acaca4e553d83fecfdae04b038f7d415edae6e2",
            "nexusAcceptanceHarnessRevision": None,
            "deerFlowAcceptanceHarnessRevision": harness_revision,
        },
        "contractPin": {
            "openapiPath": "contracts/openapi/nexus-skill-receiver-v1.yaml",
            "openapiSha256": "bf3c6a98c8686695cd6518f27817b2f1b184acab17863dc2ae5c303ae9c24615",
            "conformancePath": "contracts/openapi/nexus-skill-receiver-v1.conformance.json",
            "conformanceSha256": "300e4be74bf66749718d4bdc0d7845ed51dc4e4f0498be8c1fbe47ef871fe81c",
        },
        "topology": {
            "postgres": {
                "image": "postgres:17.5-alpine",
                "deerFlow": {
                    "database": "deerflow_acceptance",
                    "schema": "deerflow",
                    "role": "deerflow_acceptance",
                },
                "crossDatabaseAccess": False,
            },
            "nexus": {
                "mode": "junit_joint_acceptance_profile",
                "backendPort": 38080,
                "publicWriteRoutesEnabled": False,
                "receiverBindingId": "df-joint-acceptance",
                "connectTimeoutSeconds": 5,
                "commandTimeoutSeconds": 30,
            },
            "deerFlow": {
                "gatewayWorkers": 1,
                "sshdBindHost": "127.0.0.1",
                "sshdPort": 38222,
                "composeProfile": "nexus-receiver-ssh",
                "recoverySignalSocket": "/run/nexus-receiver-ipc/recovery.sock",
            },
        },
        "fixture": {
            "users": [
                {
                    "userId": "acceptance-user-a",
                    "displayName": "Acceptance User A",
                    "accountLabel": "a***",
                    "active": True,
                    "installEligible": True,
                },
                {
                    "userId": "acceptance-user-b",
                    "displayName": "Acceptance User B",
                    "accountLabel": "b***",
                    "active": True,
                    "installEligible": True,
                },
                {
                    "userId": "acceptance-user-ineligible",
                    "displayName": "Acceptance Ineligible",
                    "accountLabel": "i***",
                    "active": True,
                    "installEligible": False,
                },
            ],
            "nexusPrincipal": {
                "principalId": "11111111-1111-4111-8111-111111111111",
                "actions": ["skill:read", "skill:install_for_user"],
                "syntheticAcceptanceIdentity": True,
                "productionApproved": False,
            },
            "packages": [
                {
                    "id": "valid-v1",
                    "runtimeSkillName": "acceptance-skill",
                    "skillVersionId": "sv.acceptance-skill.1.0.0",
                    "version": "1.0.0",
                    "mediaType": "application/zip",
                    "path": "/acceptance/packages/valid-v1.zip",
                    "digest": f"sha256:{'1' * 64}",
                    "sizeBytes": 100,
                },
                {
                    "id": "name-conflict-v2",
                    "runtimeSkillName": "acceptance-skill",
                    "skillVersionId": "sv.acceptance-skill.2.0.0",
                    "version": "2.0.0",
                    "mediaType": "application/zip",
                    "path": "/acceptance/packages/name-conflict-v2.zip",
                    "digest": f"sha256:{'2' * 64}",
                    "sizeBytes": 101,
                },
            ],
            "ssh": {
                "account": "nexus-receiver",
                "keyId": "nexus-joint-acceptance-key-1",
                "clientIdentitySecretReference": "secret://joint-acceptance/ssh/client-identity",
                "hostKeySecretReference": "secret://joint-acceptance/ssh/host-key",
                "principalMapSecretReference": "secret://joint-acceptance/ssh/principal-map",
                "materialProvisioned": False,
            },
            "policies": {
                "directoryRevision": "acceptance-directory-v1",
                "trustRevision": "acceptance-trust-v1",
                "compatibilityRevision": "acceptance-compatibility-v1",
                "syntheticAcceptancePolicies": True,
                "productionApproved": False,
            },
            "faults": [
                "disconnect_after_receiver_accept",
                "restart_nexus_after_outcome_unknown",
                "restart_deer_flow_before_activation",
                "fail_activation_after_atomic_install",
                "disable_after_success",
                "observation_unavailable",
            ],
        },
    }


def _write_manifest(
    tmp_path: Path,
    *,
    harness_revision: str | None = HARNESS_REVISION,
    package: bytes | None = None,
) -> Path:
    document = _manifest(harness_revision=harness_revision)
    compatible_package = package if package is not None else _archive()
    document["fixture"]["packages"][0]["digest"] = f"sha256:{hashlib.sha256(compatible_package).hexdigest()}"
    document["fixture"]["packages"][0]["sizeBytes"] = len(compatible_package)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _environment(tmp_path: Path, manifest_path: Path) -> dict[str, str]:
    principal_map = tmp_path / "principal-map.json"
    principal_map.write_text(
        json.dumps(
            {
                "version": 1,
                "principals": [
                    {
                        "keyId": "nexus-joint-acceptance-key-1",
                        "subject": "11111111-1111-4111-8111-111111111111",
                        "actions": [
                            "receiver:capabilities:read",
                            "receiver:user-directory:read",
                            "receiver:install:user",
                            "receiver:observe:user",
                            "receiver:operations:read",
                        ],
                        "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAcceptanceFixtureKey nexus",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return {
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED": "true",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PROFILE": ACCEPTANCE_PROFILE,
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PREPARATION_REVISION": ACCEPTANCE_PREPARATION_REVISION,
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_HARNESS_REVISION": HARNESS_REVISION,
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_MANIFEST": str(manifest_path),
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PRINCIPAL_MAP_FILE": str(principal_map),
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PACKAGE_STAGE": str(tmp_path / "packages"),
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_FAULT_DIR": str(tmp_path / "faults"),
        "GATEWAY_WORKERS": "1",
    }


def _config() -> NexusReceiverConfig:
    return NexusReceiverConfig.model_validate(
        {
            "enabled": True,
            "ssh_account": "nexus-receiver",
            "recovery_signal_socket": "/run/nexus-receiver-ipc/recovery.sock",
            "host_key_secret_ref": {"name": "joint-acceptance/ssh", "key": "host-key"},
            "principal_map_secret_ref": {"name": "joint-acceptance/ssh", "key": "principal-map"},
            "directory_policy_revision": "acceptance-directory-v1",
            "trust_policy_revision": "acceptance-trust-v1",
            "compatibility_policy_revision": "acceptance-compatibility-v1",
        }
    )


def _archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "acceptance-skill/SKILL.md",
            "---\nname: acceptance-skill\nversion: 1.0.0\ndescription: Acceptance fixture\n---\n",
        )
    return output.getvalue()


def _command(package: bytes) -> dict:
    return {
        "receiverOperationId": str(uuid4()),
        "receiverBindingId": "df-joint-acceptance",
        "skillVersionId": "sv.acceptance-skill.1.0.0",
        "runtimeSkillName": "acceptance-skill",
        "packageDigest": f"sha256:{hashlib.sha256(package).hexdigest()}",
        "packageSizeBytes": len(package),
        "packageMediaType": "application/zip",
        "policyRevision": "acceptance-trust-v1",
        "expectedObservedState": "ABSENT",
        "actorAudit": {
            "principalId": "11111111-1111-4111-8111-111111111111",
            "action": "skill:install_for_user",
        },
        "target": {"scope": "USER", "deerFlowUserId": "acceptance-user-a"},
    }


def test_acceptance_settings_fail_closed_without_profile_revision_or_manifest(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    environment = _environment(tmp_path, manifest_path)

    for missing in (
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PROFILE",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PREPARATION_REVISION",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_HARNESS_REVISION",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_MANIFEST",
        "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PRINCIPAL_MAP_FILE",
    ):
        incomplete = dict(environment)
        incomplete.pop(missing)
        with pytest.raises(ValueError, match="acceptance"):
            load_acceptance_settings(incomplete)


def test_acceptance_manifest_requires_exact_frozen_revisions_and_default_deny_flags(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    settings = load_acceptance_settings(_environment(tmp_path, manifest_path))

    manifest = AcceptanceManifest.load(settings)

    assert manifest.deer_flow_deployment_revision == BASE_REVISION
    assert manifest.deer_flow_acceptance_harness_revision == HARNESS_REVISION
    assert [user.user_id for user in manifest.users] == [
        "acceptance-user-a",
        "acceptance-user-b",
        "acceptance-user-ineligible",
    ]

    changed = _manifest()
    changed["executionPolicy"]["productionWriteEnabled"] = True
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="default-deny"):
        AcceptanceManifest.load(settings)

    changed = _manifest()
    changed["topology"]["nexus"]["publicWriteRoutesEnabled"] = True
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="Nexus topology"):
        AcceptanceManifest.load(settings)

    changed = _manifest()
    changed["fixture"]["packages"][0]["digest"] = "sha256:invalid"
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="package fixture"):
        AcceptanceManifest.load(settings)

    changed = _manifest()
    del changed["fixture"]["ssh"]["clientIdentitySecretReference"]
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest is invalid"):
        AcceptanceManifest.load(settings)


@pytest.mark.asyncio
async def test_controlled_directory_exposes_only_fixture_users_and_revalidates_targets(tmp_path: Path) -> None:
    package = _archive()
    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path, package=package)))
    directory = ControlledAcceptanceDirectory(AcceptanceManifest.load(settings))
    principal = ReceiverTransportPrincipal(
        subject="11111111-1111-4111-8111-111111111111",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:user-directory:read"}),
    )

    page = await directory.search(principal=principal, query="user", cursor=None, limit=2)
    second_page = await directory.search(principal=principal, query="user", cursor=page.page.next_cursor, limit=2)

    assert [user.user_id for user in page.items] == ["acceptance-user-a", "acceptance-user-b"]
    assert [user.user_id for user in second_page.items] == ["acceptance-user-ineligible"]
    assert (await directory.resolve_install_target("acceptance-user-a")).install_eligible is True
    assert (await directory.resolve_install_target("acceptance-user-ineligible")).install_eligible is False
    assert await directory.resolve_install_target("unknown-user") is None


@pytest.mark.asyncio
async def test_fault_controller_consumes_each_armed_fault_once(tmp_path: Path) -> None:
    controller = AcceptanceFaultController(tmp_path / "faults")

    await controller.arm("fail_activation_after_atomic_install")

    assert await controller.consume("fail_activation_after_atomic_install") is True
    assert await controller.consume("fail_activation_after_atomic_install") is False


@pytest.mark.asyncio
async def test_acceptance_installer_uses_native_user_storage_and_faults_are_one_shot(tmp_path: Path, monkeypatch) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "home"))

    def storage_factory(user_id: str) -> UserScopedSkillStorage:
        return UserScopedSkillStorage(user_id, host_path=str(tmp_path / "skills"))

    controller = AcceptanceFaultController(tmp_path / "faults")
    native = UserScopedReceiverInstaller(
        storage_factory=storage_factory,
        precommit_scan=_acceptance_precommit_scan,
    )

    def terminate(_code: int):
        raise AcceptanceRestartRequested

    installer = AcceptanceUserInstaller(
        controller,
        delegate=native,
        storage_factory=storage_factory,
        terminator=terminate,
    )
    package = _archive()
    command = ReceiverRuntimeHandler.validate_command(_command(package))
    await installer.install_user_skill(
        user_id="acceptance-user-a",
        command=command,
        manifest_version="1.0.0",
        package=package,
    )
    installed = await installer.observe_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")
    assert installed is not None and installed["enabled"] is False

    await controller.arm("restart_deer_flow_before_activation")
    with pytest.raises(AcceptanceRestartRequested):
        await installer.activate_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")
    await controller.arm("fail_activation_after_atomic_install")
    with pytest.raises(ReceiverRuntimeError) as activation_failed:
        await installer.activate_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")
    assert activation_failed.value.code == "ACTIVATION_FAILED"
    await installer.activate_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")

    await controller.arm("disable_after_success")
    disabled = await installer.observe_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")
    assert disabled is not None and disabled["enabled"] is False and disabled["loadState"] == "disabled"

    await controller.arm("observation_unavailable")
    with pytest.raises(ReceiverRuntimeError) as unavailable:
        await installer.observe_user_skill(user_id="acceptance-user-a", runtime_skill_name="acceptance-skill")
    assert unavailable.value.code == "RECEIVER_NOT_READY"
    assert unavailable.value.detail == "The acceptance fixture made observation unavailable."


@pytest.mark.asyncio
async def test_acceptance_sql_composition_closes_native_user_install_and_survives_store_reopen(tmp_path: Path, monkeypatch) -> None:
    import deerflow.config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "home"))

    def storage_factory(user_id: str) -> UserScopedSkillStorage:
        return UserScopedSkillStorage(user_id, host_path=str(tmp_path / "skills"))

    package = _archive()
    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path, package=package)))
    manifest = AcceptanceManifest.load(settings)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'receiver.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ReceiverOperationRow.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    installer = AcceptanceUserInstaller(
        AcceptanceFaultController(tmp_path / "faults"),
        delegate=UserScopedReceiverInstaller(
            storage_factory=storage_factory,
            precommit_scan=_acceptance_precommit_scan,
        ),
        storage_factory=storage_factory,
    )
    handler = AcceptanceReceiverRuntimeHandler(
        manifest=manifest,
        faults=AcceptanceFaultController(tmp_path / "faults"),
        store=SqlReceiverOperationStore(session_factory),
        package_store=LocalReceiverPackageStore(tmp_path / "packages"),
        directory=ControlledAcceptanceDirectory(manifest),
        user_directory=ControlledAcceptanceDirectory(manifest),
        installer=installer,
        execution_owner="receiver-acceptance-test",
        user_install_ready=True,
    )
    principal = ReceiverTransportPrincipal(
        subject="11111111-1111-4111-8111-111111111111",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:install:user"}),
    )
    command = _command(package)

    try:
        accepted = await handler.submit_install(
            principal=principal,
            idempotency_key="acceptance-native-sql-0001",
            request_sha256=handler.canonical_command_digest(command),
            command_payload=command,
            package=package,
        )
        recovered = await handler.recover_pending()
        reopened = await SqlReceiverOperationStore(session_factory).get(command["receiverOperationId"])

        assert accepted.phase == "accepted"
        assert [operation.phase for operation in recovered] == ["succeeded"], recovered
        assert reopened is not None and reopened.operation.phase == "succeeded"
        assert reopened.operation.observed is not None
        assert reopened.operation.observed.enabled is True
        assert reopened.operation.observed.load_state == "loaded"
        observed = await installer.observe_user_skill(
            user_id="acceptance-user-a",
            runtime_skill_name="acceptance-skill",
        )
        assert observed is not None and observed["enabled"] is True
        assert observed["packageDigest"] == command["packageDigest"]
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not os.getenv("DEERFLOW_NEXUS_RECEIVER_ACCEPTANCE_POSTGRES_URL"),
    reason="set DEERFLOW_NEXUS_RECEIVER_ACCEPTANCE_POSTGRES_URL to run the acceptance PostgreSQL container test",
)
@pytest.mark.asyncio
async def test_acceptance_composition_uses_isolated_postgres_and_native_user_installer(tmp_path: Path, monkeypatch) -> None:
    import app.gateway.nexus_receiver.acceptance as acceptance_module
    import deerflow.config.paths as paths_module
    import deerflow.skills.storage as storage_module

    monkeypatch.setattr(paths_module, "_paths", Paths(tmp_path / "home"))

    def storage_factory(user_id: str) -> UserScopedSkillStorage:
        return UserScopedSkillStorage(user_id, host_path=str(tmp_path / "skills"))

    monkeypatch.setattr(storage_module, "get_or_new_user_skill_storage", storage_factory)
    postgres_url = os.environ["DEERFLOW_NEXUS_RECEIVER_ACCEPTANCE_POSTGRES_URL"]
    if postgres_url.startswith("postgresql://"):
        postgres_url = postgres_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    await init_engine("postgres", url=postgres_url, postgres_schema="deerflow")
    engine = get_engine()
    session_factory = get_session_factory()
    assert engine is not None and session_factory is not None
    async with engine.begin() as connection:
        identity = (await connection.execute(text("SELECT current_database(), current_user, current_schema()"))).one()
    assert identity == ("deerflow_acceptance", "deerflow_acceptance", "deerflow")

    package = _archive()
    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path, package=package)))
    monkeypatch.setattr(
        acceptance_module,
        "get_app_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                backend="postgres",
                postgres_url=postgres_url,
                postgres_schema="deerflow",
            )
        ),
    )
    components = await AcceptanceReceiverReleaseBootstrap(settings).build(_config())
    handler = components.runtime_handler
    installer = handler.installer
    command = _command(package)
    principal = ReceiverTransportPrincipal(
        subject="11111111-1111-4111-8111-111111111111",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:install:user"}),
    )
    try:
        await handler.submit_install(
            principal=principal,
            idempotency_key=f"acceptance-postgres-{command['receiverOperationId']}",
            request_sha256=handler.canonical_command_digest(command),
            command_payload=command,
            package=package,
        )
        recovered = await handler.recover_pending()
        reopened = await SqlReceiverOperationStore(session_factory).get(command["receiverOperationId"])

        assert isinstance(handler.store, SqlReceiverOperationStore)
        assert isinstance(installer.delegate, UserScopedReceiverInstaller)
        assert [operation.phase for operation in recovered] == ["succeeded"], recovered
        assert reopened is not None and reopened.operation.phase == "succeeded"
    finally:
        await close_engine()


@pytest.mark.asyncio
async def test_disconnect_fault_occurs_only_after_durable_receiver_acceptance(tmp_path: Path) -> None:
    package = _archive()
    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path, package=package)))
    manifest = AcceptanceManifest.load(settings)
    faults = AcceptanceFaultController(tmp_path / "faults")
    store = InMemoryReceiverOperationStore()

    class _Installer:
        async def install_user_skill(self, **kwargs):
            del kwargs

        async def activate_user_skill(self, **kwargs):
            del kwargs

        async def observe_user_skill(self, **kwargs):
            del kwargs
            return None

    handler = AcceptanceReceiverRuntimeHandler(
        manifest=manifest,
        faults=faults,
        store=store,
        package_store=InMemoryReceiverPackageStore(),
        directory=ControlledAcceptanceDirectory(manifest),
        user_directory=ControlledAcceptanceDirectory(manifest),
        installer=_Installer(),
        user_install_ready=True,
    )
    command = _command(package)
    principal = ReceiverTransportPrincipal(
        subject="11111111-1111-4111-8111-111111111111",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:install:user"}),
    )
    await faults.arm("disconnect_after_receiver_accept")

    with pytest.raises(AcceptanceTransportDisconnect):
        await handler.submit_install(
            principal=principal,
            idempotency_key="acceptance-disconnect-0001",
            request_sha256=handler.canonical_command_digest(command),
            command_payload=command,
            package=package,
        )

    entry = await store.get(command["receiverOperationId"])
    assert entry is not None and entry.operation.phase == "accepted"


@pytest.mark.asyncio
async def test_acceptance_rejects_package_outside_frozen_compatibility_decision(tmp_path: Path) -> None:
    package = _archive()
    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path, package=package)))
    manifest = AcceptanceManifest.load(settings)
    handler = AcceptanceReceiverRuntimeHandler(
        manifest=manifest,
        faults=AcceptanceFaultController(tmp_path / "faults"),
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=ControlledAcceptanceDirectory(manifest),
        user_directory=ControlledAcceptanceDirectory(manifest),
        installer=object(),
        user_install_ready=True,
    )
    command = _command(package)
    command["skillVersionId"] = "sv.unapproved.1.0.0"
    principal = ReceiverTransportPrincipal(
        subject="11111111-1111-4111-8111-111111111111",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:install:user"}),
    )

    with pytest.raises(ReceiverRuntimeError) as incompatible:
        await handler.submit_install(
            principal=principal,
            idempotency_key="acceptance-incompatible-0001",
            request_sha256=handler.canonical_command_digest(command),
            command_payload=command,
            package=package,
        )

    assert incompatible.value.code == "SKILL_INCOMPATIBLE"
    assert incompatible.value.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_uses_real_sql_store_native_installer_and_write_ready_user_capabilities(tmp_path: Path, monkeypatch) -> None:
    import app.gateway.nexus_receiver.acceptance as acceptance_module

    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path)))
    session_factory = object()
    monkeypatch.setattr(acceptance_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        acceptance_module,
        "get_app_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(
                backend="postgres",
                postgres_url="postgresql://deerflow_acceptance:secret@postgres/deerflow_acceptance",
                postgres_schema="deerflow",
            )
        ),
    )
    bootstrap = AcceptanceReceiverReleaseBootstrap(settings)

    components = await bootstrap.build(_config())
    principal = await components.principal_mapper.map_principal("nexus-joint-acceptance-key-1")
    capability = await components.runtime_handler.get_capabilities(principal=principal)

    assert isinstance(components.runtime_handler, ReceiverRuntimeHandler)
    assert isinstance(components.runtime_handler.store, SqlReceiverOperationStore)
    assert components.runtime_handler.store._session_factory is session_factory
    assert isinstance(components.runtime_handler.package_store, LocalReceiverPackageStore)
    assert isinstance(components.runtime_handler.installer.delegate, UserScopedReceiverInstaller)
    assert principal.subject == "11111111-1111-4111-8111-111111111111"
    assert capability.access_mode == "read_write"
    assert capability.capabilities.user_install == "supported"
    assert capability.capabilities.global_install == "unsupported"
    assert capability.capabilities.observation == "user_only"
    assert capability.blocked_by == []
    with pytest.raises(ReceiverServiceAuthenticationRequired):
        await components.service_authenticator.authenticate(object())

    principal_map = settings.principal_map_path
    changed = json.loads(principal_map.read_text(encoding="utf-8"))
    changed["principals"][0]["subject"] = "wrong.acceptance.subject"
    principal_map.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="principal binding"):
        await components.principal_mapper.map_principal("nexus-joint-acceptance-key-1")


@pytest.mark.asyncio
async def test_bootstrap_rejects_non_postgres_or_wrong_isolation_identity(tmp_path: Path, monkeypatch) -> None:
    import app.gateway.nexus_receiver.acceptance as acceptance_module

    settings = load_acceptance_settings(_environment(tmp_path, _write_manifest(tmp_path)))
    monkeypatch.setattr(acceptance_module, "get_session_factory", lambda: object())
    bootstrap = AcceptanceReceiverReleaseBootstrap(settings)

    for database in (
        SimpleNamespace(backend="sqlite", postgres_url="", postgres_schema=""),
        SimpleNamespace(
            backend="postgres",
            postgres_url="postgresql://wrong:secret@postgres/wrong",
            postgres_schema="deerflow",
        ),
    ):
        monkeypatch.setattr(acceptance_module, "get_app_config", lambda database=database: SimpleNamespace(database=database))
        with pytest.raises(ValueError, match="acceptance bootstrap"):
            await bootstrap.build(_config())


def test_production_gateway_factory_does_not_inject_acceptance_bootstrap() -> None:
    from app.gateway.app import create_app

    production_app = create_app()

    assert not hasattr(production_app.state, "nexus_receiver_release_bootstrap")


def test_acceptance_compose_overlay_is_explicit_and_keeps_production_entrypoints_unchanged() -> None:
    overlay = yaml.safe_load((ROOT / "docker" / "docker-compose.nexus-receiver-acceptance.yaml").read_text(encoding="utf-8"))
    gateway = overlay["services"]["gateway"]
    sshd = overlay["services"]["nexus-receiver-sshd"]
    production_app = (ROOT / "backend" / "app" / "gateway" / "app.py").read_text(encoding="utf-8")
    forced_command = (ROOT / "backend" / "app" / "gateway" / "nexus_receiver" / "forced_command.py").read_text(encoding="utf-8")

    assert "acceptance_app:app" in gateway["command"]
    assert "--workers 1" in gateway["command"]
    assert gateway["environment"]["DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED"] == "${DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED:-false}"
    assert sshd["environment"]["NEXUS_RECEIVER_FORCED_COMMAND_PROFILE"] == "acceptance"
    assert "acceptance_app" not in production_app
    assert "acceptance" not in forced_command
