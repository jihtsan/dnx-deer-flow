"""Opt-in composition root for the isolated Skill Hub USER acceptance harness."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import make_url

from app.gateway.nexus_receiver.auth import (
    ReceiverServiceAuthenticationRequired,
    ReceiverServicePrincipal,
)
from app.gateway.nexus_receiver.coordination import SqlReceiverCoordinationStore
from app.gateway.nexus_receiver.directory import ReceiverDirectoryCursorRejected
from app.gateway.nexus_receiver.installer import UserScopedReceiverInstaller
from app.gateway.nexus_receiver.models import ReceiverCursorPage, ReceiverInstallCommand, ReceiverUser, ReceiverUserPage
from app.gateway.nexus_receiver.package_store import LocalReceiverPackageStore
from app.gateway.nexus_receiver.release import (
    ReceiverPrincipalMapper,
    ReceiverReleaseComponents,
    SecretBackedReceiverPrincipalMapper,
)
from app.gateway.nexus_receiver.runtime import (
    ReceiverRuntimeError,
    ReceiverRuntimeHandler,
    ReceiverTargetUser,
    ReceiverTransportPrincipal,
)
from app.gateway.nexus_receiver.store import SqlReceiverOperationStore
from deerflow.config.app_config import get_app_config
from deerflow.config.nexus_receiver_config import NexusReceiverConfig, ReceiverSecretReference
from deerflow.persistence.engine import get_session_factory

ACCEPTANCE_PROFILE = "skill-hub-user-v1"
ACCEPTANCE_PREPARATION_REVISION = "ad8600e3ec4282c33fba896be2bb62f1d6674f3b"
ACCEPTANCE_DEPLOYMENT_REVISION = "e2fa130f24c12de4f74d4b3b2977ae92c3f7fbd4"
ACCEPTANCE_RUNTIME_REVISION = "1acaca4e553d83fecfdae04b038f7d415edae6e2"
ACCEPTANCE_OPENAPI_SHA256 = "bf3c6a98c8686695cd6518f27817b2f1b184acab17863dc2ae5c303ae9c24615"
ACCEPTANCE_CONFORMANCE_SHA256 = "300e4be74bf66749718d4bdc0d7845ed51dc4e4f0498be8c1fbe47ef871fe81c"

_REVISION = re.compile(r"^[0-9a-f]{40}$")
ACCEPTANCE_FAULTS = (
    "disconnect_after_receiver_accept",
    "restart_nexus_after_outcome_unknown",
    "restart_deer_flow_before_activation",
    "fail_activation_after_atomic_install",
    "disable_after_success",
    "observation_unavailable",
)
AcceptanceFault = Literal[
    "disconnect_after_receiver_accept",
    "restart_nexus_after_outcome_unknown",
    "restart_deer_flow_before_activation",
    "fail_activation_after_atomic_install",
    "disable_after_success",
    "observation_unavailable",
]


@dataclass(frozen=True, slots=True)
class AcceptanceSettings:
    manifest_path: Path
    principal_map_path: Path
    package_stage_path: Path
    fault_path: Path
    harness_revision: str


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        raise ValueError(f"acceptance environment is missing {name}")
    return value


def _absolute_path(environment: Mapping[str, str], name: str, *, must_exist: bool = False) -> Path:
    path = Path(_required_environment(environment, name))
    if not path.is_absolute():
        raise ValueError(f"acceptance path {name} must be absolute")
    if must_exist and not path.is_file():
        raise ValueError(f"acceptance file {name} is unavailable")
    return path


def load_acceptance_settings(environment: Mapping[str, str] | None = None) -> AcceptanceSettings:
    environment = os.environ if environment is None else environment
    if environment.get("DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_ENABLED") != "true":
        raise ValueError("acceptance profile is disabled")
    if environment.get("DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PROFILE") != ACCEPTANCE_PROFILE:
        raise ValueError("acceptance profile identifier is invalid")
    if environment.get("DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PREPARATION_REVISION") != ACCEPTANCE_PREPARATION_REVISION:
        raise ValueError("acceptance preparation revision is invalid")
    harness_revision = _required_environment(environment, "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_HARNESS_REVISION")
    if _REVISION.fullmatch(harness_revision) is None:
        raise ValueError("acceptance harness revision is invalid")
    if environment.get("GATEWAY_WORKERS") != "1":
        raise ValueError("acceptance profile requires exactly one Gateway worker")
    return AcceptanceSettings(
        manifest_path=_absolute_path(environment, "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_MANIFEST", must_exist=True),
        principal_map_path=_absolute_path(environment, "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PRINCIPAL_MAP_FILE", must_exist=True),
        package_stage_path=_absolute_path(environment, "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_PACKAGE_STAGE"),
        fault_path=_absolute_path(environment, "DEER_FLOW_NEXUS_RECEIVER_ACCEPTANCE_FAULT_DIR"),
        harness_revision=harness_revision,
    )


class _ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    purpose: str
    production_write_enabled: bool = Field(alias="productionWriteEnabled")
    joint_e2e_executed: bool = Field(alias="jointE2EExecuted")
    global_enabled: bool = Field(alias="globalEnabled")
    fixture_success_is_acceptance: bool = Field(alias="fixtureSuccessIsAcceptance")


class _Revisions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deer_flow_deployment_revision: str = Field(alias="deerFlowDeploymentRevision")
    deer_flow_receiver_runtime_revision: str = Field(alias="deerFlowReceiverRuntimeRevision")
    deer_flow_acceptance_harness_revision: str | None = Field(alias="deerFlowAcceptanceHarnessRevision")


class _ContractPin(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openapi_path: str = Field(alias="openapiPath")
    openapi_sha256: str = Field(alias="openapiSha256")
    conformance_path: str = Field(alias="conformancePath")
    conformance_sha256: str = Field(alias="conformanceSha256")


class _PostgresDeerFlow(BaseModel):
    database: str
    schema_name: str = Field(alias="schema")
    role: str


class _PostgresTopology(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: str
    deer_flow: _PostgresDeerFlow = Field(alias="deerFlow")
    cross_database_access: bool = Field(alias="crossDatabaseAccess")


class _DeerFlowTopology(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gateway_workers: int = Field(alias="gatewayWorkers")
    sshd_bind_host: str = Field(alias="sshdBindHost")
    sshd_port: int = Field(alias="sshdPort")
    compose_profile: str = Field(alias="composeProfile")
    recovery_signal_socket: str = Field(alias="recoverySignalSocket")


class _NexusTopology(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str
    backend_port: int = Field(alias="backendPort")
    public_write_routes_enabled: bool = Field(alias="publicWriteRoutesEnabled")
    receiver_binding_id: str = Field(alias="receiverBindingId")
    connect_timeout_seconds: int = Field(alias="connectTimeoutSeconds")
    command_timeout_seconds: int = Field(alias="commandTimeoutSeconds")


class _Topology(BaseModel):
    model_config = ConfigDict(extra="ignore")

    postgres: _PostgresTopology
    nexus: _NexusTopology
    deer_flow: _DeerFlowTopology = Field(alias="deerFlow")


class _FixtureUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(alias="userId")
    display_name: str = Field(alias="displayName")
    account_label: str = Field(alias="accountLabel")
    active: bool
    install_eligible: bool = Field(alias="installEligible")


class _FixturePrincipal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    principal_id: str = Field(alias="principalId")
    actions: list[str]
    synthetic_acceptance_identity: bool = Field(alias="syntheticAcceptanceIdentity")
    production_approved: bool = Field(alias="productionApproved")


class _FixtureSsh(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account: str
    key_id: str = Field(alias="keyId")
    client_identity_secret_reference: str = Field(alias="clientIdentitySecretReference")
    host_key_secret_reference: str = Field(alias="hostKeySecretReference")
    principal_map_secret_reference: str = Field(alias="principalMapSecretReference")
    material_provisioned: bool = Field(alias="materialProvisioned")


class _FixturePolicies(BaseModel):
    model_config = ConfigDict(extra="ignore")

    directory_revision: str = Field(alias="directoryRevision")
    trust_revision: str = Field(alias="trustRevision")
    compatibility_revision: str = Field(alias="compatibilityRevision")
    synthetic_acceptance_policies: bool = Field(alias="syntheticAcceptancePolicies")
    production_approved: bool = Field(alias="productionApproved")


class _FixturePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(alias="id")
    runtime_skill_name: str = Field(alias="runtimeSkillName")
    skill_version_id: str = Field(alias="skillVersionId")
    version: str
    media_type: str = Field(alias="mediaType")
    path: str
    digest: str
    size_bytes: int = Field(alias="sizeBytes")


class _Fixture(BaseModel):
    model_config = ConfigDict(extra="ignore")

    users: list[_FixtureUser]
    nexus_principal: _FixturePrincipal = Field(alias="nexusPrincipal")
    ssh: _FixtureSsh
    policies: _FixturePolicies
    packages: list[_FixturePackage]
    faults: list[str]


class _ManifestDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(alias="schemaVersion")
    execution_policy: _ExecutionPolicy = Field(alias="executionPolicy")
    revisions: _Revisions
    contract_pin: _ContractPin = Field(alias="contractPin")
    topology: _Topology
    fixture: _Fixture


@dataclass(frozen=True, slots=True)
class AcceptancePackageDecision:
    skill_version_id: str
    runtime_skill_name: str
    package_digest: str
    package_size_bytes: int
    package_media_type: str


@dataclass(frozen=True, slots=True)
class AcceptanceManifest:
    deer_flow_deployment_revision: str
    deer_flow_acceptance_harness_revision: str
    users: tuple[ReceiverUser, ...]
    key_id: str
    principal_id: str
    directory_revision: str
    trust_revision: str
    compatibility_revision: str
    recovery_signal_socket: str
    compatible_packages: tuple[AcceptancePackageDecision, ...]

    @classmethod
    def load(cls, settings: AcceptanceSettings) -> AcceptanceManifest:
        try:
            raw = json.loads(settings.manifest_path.read_text(encoding="utf-8"))
            document = _ManifestDocument.model_validate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
            raise ValueError("acceptance manifest is invalid") from None

        policy = document.execution_policy
        if document.schema_version != 1 or policy.purpose != "preparation_rehearsal_only" or policy.production_write_enabled or policy.joint_e2e_executed or policy.global_enabled or policy.fixture_success_is_acceptance:
            raise ValueError("acceptance manifest violates default-deny execution policy")
        revisions = document.revisions
        if (
            revisions.deer_flow_deployment_revision != ACCEPTANCE_DEPLOYMENT_REVISION
            or revisions.deer_flow_receiver_runtime_revision != ACCEPTANCE_RUNTIME_REVISION
            or revisions.deer_flow_acceptance_harness_revision != settings.harness_revision
        ):
            raise ValueError("acceptance manifest revision pins are invalid")
        contract_pin = document.contract_pin
        if (
            contract_pin.openapi_path != "contracts/openapi/nexus-skill-receiver-v1.yaml"
            or contract_pin.openapi_sha256 != ACCEPTANCE_OPENAPI_SHA256
            or contract_pin.conformance_path != "contracts/openapi/nexus-skill-receiver-v1.conformance.json"
            or contract_pin.conformance_sha256 != ACCEPTANCE_CONFORMANCE_SHA256
        ):
            raise ValueError("acceptance manifest contract pins are invalid")
        topology = document.topology
        if (
            topology.postgres.image != "postgres:17.5-alpine"
            or topology.postgres.cross_database_access
            or topology.postgres.deer_flow.database != "deerflow_acceptance"
            or topology.postgres.deer_flow.role != "deerflow_acceptance"
            or topology.postgres.deer_flow.schema_name != "deerflow"
            or topology.deer_flow.gateway_workers != 1
            or topology.deer_flow.sshd_bind_host != "127.0.0.1"
            or topology.deer_flow.sshd_port != 38222
            or topology.deer_flow.compose_profile != "nexus-receiver-ssh"
            or topology.deer_flow.recovery_signal_socket != "/run/nexus-receiver-ipc/recovery.sock"
        ):
            raise ValueError("acceptance manifest topology is invalid")
        nexus = topology.nexus
        if (
            nexus.mode != "junit_joint_acceptance_profile"
            or nexus.backend_port != 38080
            or nexus.public_write_routes_enabled
            or nexus.receiver_binding_id != "df-joint-acceptance"
            or nexus.connect_timeout_seconds != 5
            or nexus.command_timeout_seconds != 30
        ):
            raise ValueError("acceptance manifest Nexus topology is invalid")

        fixture = document.fixture
        expected_users = (
            ("acceptance-user-a", "Acceptance User A", "a***", True),
            ("acceptance-user-b", "Acceptance User B", "b***", True),
            ("acceptance-user-ineligible", "Acceptance Ineligible", "i***", False),
        )
        actual_users = tuple((user.user_id, user.display_name, user.account_label, user.active and user.install_eligible) for user in fixture.users)
        if actual_users != expected_users or any(not user.active for user in fixture.users):
            raise ValueError("acceptance controlled directory fixture is invalid")
        if (
            fixture.nexus_principal.principal_id != "11111111-1111-4111-8111-111111111111"
            or fixture.nexus_principal.actions != ["skill:read", "skill:install_for_user"]
            or not fixture.nexus_principal.synthetic_acceptance_identity
            or fixture.nexus_principal.production_approved
        ):
            raise ValueError("acceptance synthetic principal fixture is invalid")
        if (
            fixture.ssh.account != "nexus-receiver"
            or fixture.ssh.key_id != "nexus-joint-acceptance-key-1"
            or fixture.ssh.client_identity_secret_reference != "secret://joint-acceptance/ssh/client-identity"
            or fixture.ssh.host_key_secret_reference != "secret://joint-acceptance/ssh/host-key"
            or fixture.ssh.principal_map_secret_reference != "secret://joint-acceptance/ssh/principal-map"
            or fixture.ssh.material_provisioned
        ):
            raise ValueError("acceptance synthetic SSH fixture is invalid")
        policies = fixture.policies
        if (
            policies.directory_revision != "acceptance-directory-v1"
            or policies.trust_revision != "acceptance-trust-v1"
            or policies.compatibility_revision != "acceptance-compatibility-v1"
            or not policies.synthetic_acceptance_policies
            or policies.production_approved
            or tuple(fixture.faults) != ACCEPTANCE_FAULTS
        ):
            raise ValueError("acceptance synthetic policy or fault fixture is invalid")
        expected_packages = (
            ("valid-v1", "acceptance-skill", "sv.acceptance-skill.1.0.0", "1.0.0"),
            ("name-conflict-v2", "acceptance-skill", "sv.acceptance-skill.2.0.0", "2.0.0"),
        )
        actual_packages = tuple((package.package_id, package.runtime_skill_name, package.skill_version_id, package.version) for package in fixture.packages)
        if actual_packages != expected_packages or any(
            package.media_type != "application/zip" or not Path(package.path).is_absolute() or re.fullmatch(r"sha256:[0-9a-f]{64}", package.digest) is None or package.size_bytes <= 0 for package in fixture.packages
        ):
            raise ValueError("acceptance package fixture is invalid")

        users = tuple(
            ReceiverUser(
                user_id=user.user_id,
                display_name=user.display_name,
                account_label=user.account_label,
                active=user.active,
                install_eligible=user.install_eligible,
            )
            for user in fixture.users
        )
        return cls(
            deer_flow_deployment_revision=revisions.deer_flow_deployment_revision,
            deer_flow_acceptance_harness_revision=settings.harness_revision,
            users=users,
            key_id=fixture.ssh.key_id,
            principal_id=fixture.nexus_principal.principal_id,
            directory_revision=policies.directory_revision,
            trust_revision=policies.trust_revision,
            compatibility_revision=policies.compatibility_revision,
            recovery_signal_socket=topology.deer_flow.recovery_signal_socket,
            compatible_packages=tuple(
                AcceptancePackageDecision(
                    skill_version_id=package.skill_version_id,
                    runtime_skill_name=package.runtime_skill_name,
                    package_digest=package.digest,
                    package_size_bytes=package.size_bytes,
                    package_media_type=package.media_type,
                )
                for package in fixture.packages
            ),
        )


class AcceptanceFaultController:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ValueError("acceptance fault directory must be absolute")
        self.root = root

    @staticmethod
    def _validate(fault: str) -> None:
        if fault not in ACCEPTANCE_FAULTS:
            raise ValueError("acceptance fault name is invalid")

    def _arm(self, fault: str) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        path = self.root / f"{fault}.armed"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(descriptor)

    async def arm(self, fault: AcceptanceFault) -> None:
        self._validate(fault)
        await asyncio.to_thread(self._arm, fault)

    def _consume(self, fault: str) -> bool:
        armed = self.root / f"{fault}.armed"
        consumed = self.root / f"{fault}.consumed"
        try:
            os.replace(armed, consumed)
        except FileNotFoundError:
            return False
        return True

    async def consume(self, fault: AcceptanceFault) -> bool:
        self._validate(fault)
        return await asyncio.to_thread(self._consume, fault)

    def _clear(self, fault: str) -> None:
        (self.root / f"{fault}.armed").unlink(missing_ok=True)
        (self.root / f"{fault}.consumed").unlink(missing_ok=True)

    async def clear(self, fault: AcceptanceFault) -> None:
        self._validate(fault)
        await asyncio.to_thread(self._clear, fault)

    async def is_armed(self, fault: AcceptanceFault) -> bool:
        self._validate(fault)
        return await asyncio.to_thread((self.root / f"{fault}.armed").is_file)


class ControlledAcceptanceDirectory:
    _CURSOR_PREFIX = "acceptance-v1:"

    def __init__(self, manifest: AcceptanceManifest) -> None:
        self._users = manifest.users
        self._by_id = {user.user_id: user for user in manifest.users}

    async def resolve_install_target(self, user_id: str) -> ReceiverTargetUser | None:
        user = self._by_id.get(user_id)
        if user is None:
            return None
        return ReceiverTargetUser(user_id=user.user_id, active=user.active, install_eligible=user.install_eligible)

    async def search(self, *, principal: object, query: str | None, cursor: str | None, limit: int) -> ReceiverUserPage:
        del principal
        if cursor is None:
            offset = 0
        else:
            try:
                prefix, raw_offset = cursor.rsplit(":", 1)
                if f"{prefix}:" != self._CURSOR_PREFIX:
                    raise ValueError
                offset = int(raw_offset)
            except (ValueError, TypeError):
                raise ReceiverDirectoryCursorRejected() from None
        normalized = query.casefold() if query else None
        matching = [
            user
            for user in self._users
            if normalized is None or normalized in user.user_id.casefold() or (user.display_name is not None and normalized in user.display_name.casefold()) or (user.account_label is not None and normalized in user.account_label.casefold())
        ]
        items = matching[offset : offset + limit]
        next_offset = offset + len(items)
        has_more = next_offset < len(matching)
        return ReceiverUserPage(
            items=list(items),
            page=ReceiverCursorPage(
                has_more=has_more,
                next_cursor=f"{self._CURSOR_PREFIX}{next_offset}" if has_more else None,
            ),
            observed_at=datetime.now(UTC),
        )


class AcceptanceRestartRequested(BaseException):
    """Test sentinel used instead of terminating the process in unit coverage."""


class AcceptanceTransportDisconnect(BaseException):
    """Close the acceptance-only SSH submit after durable receiver acceptance."""


class AcceptanceUserInstaller:
    def __init__(
        self,
        faults: AcceptanceFaultController,
        *,
        delegate: UserScopedReceiverInstaller | None = None,
        storage_factory: Callable[[str], Any] | None = None,
        terminator: Callable[[int], NoReturn] = os._exit,
    ) -> None:
        if storage_factory is None:
            from deerflow.skills.storage import get_or_new_user_skill_storage

            storage_factory = get_or_new_user_skill_storage
        self.delegate = delegate or UserScopedReceiverInstaller(storage_factory=storage_factory)
        self._storage_factory = storage_factory
        self._faults = faults
        self._terminator = terminator

    async def install_user_skill(self, *, user_id: str, command: ReceiverInstallCommand, manifest_version: str, package: bytes) -> None:
        await self.delegate.install_user_skill(
            user_id=user_id,
            command=command,
            manifest_version=manifest_version,
            package=package,
        )

    async def activate_user_skill(self, *, user_id: str, runtime_skill_name: str) -> None:
        if await self._faults.consume("restart_deer_flow_before_activation"):
            self._terminator(75)
        if await self._faults.consume("fail_activation_after_atomic_install"):
            raise ReceiverRuntimeError(code="ACTIVATION_FAILED", detail="The acceptance fixture rejected activation deterministically.")
        await self.delegate.activate_user_skill(user_id=user_id, runtime_skill_name=runtime_skill_name)

    async def observe_user_skill(self, *, user_id: str, runtime_skill_name: str) -> dict[str, Any] | None:
        if await self._faults.consume("observation_unavailable"):
            raise ReceiverRuntimeError(
                code="RECEIVER_NOT_READY",
                title="Acceptance observation unavailable",
                detail="The acceptance fixture made observation unavailable.",
                status_code=503,
                retryable=True,
            )
        observed = await self.delegate.observe_user_skill(user_id=user_id, runtime_skill_name=runtime_skill_name)
        if observed is not None and observed.get("enabled") is True and await self._faults.consume("disable_after_success"):
            storage = await asyncio.to_thread(self._storage_factory, user_id)
            await asyncio.to_thread(storage.set_skill_enabled_state, runtime_skill_name, False)
            observed = await self.delegate.observe_user_skill(user_id=user_id, runtime_skill_name=runtime_skill_name)
        return observed


async def _acceptance_precommit_scan(skill_dir: Path, skill_name: str) -> None:
    """Run native deterministic SkillScan under the frozen synthetic trust gate."""
    from deerflow.skills.installer import _scan_static_skill_archive_or_raise

    await _scan_static_skill_archive_or_raise(skill_dir, skill_name)


class AcceptanceReceiverRuntimeHandler(ReceiverRuntimeHandler):
    def __init__(self, *, manifest: AcceptanceManifest, faults: AcceptanceFaultController, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._manifest = manifest
        self._faults = faults

    async def submit_install(self, *, command_payload: dict[str, Any], **kwargs: Any):
        command = self.validate_command(command_payload)
        principal = kwargs.get("principal")
        if not isinstance(principal, (ReceiverServicePrincipal, ReceiverTransportPrincipal)) or principal.subject != self._manifest.principal_id or str(command.actor_audit.principal_id) != self._manifest.principal_id:
            raise ReceiverRuntimeError(code="FORBIDDEN", detail="The acceptance principal binding does not match.", status_code=403)
        if command.receiver_binding_id != "df-joint-acceptance":
            raise ReceiverRuntimeError(code="INVALID_INSTALLATION_TARGET", detail="The acceptance receiver binding does not match.", status_code=422)
        if command.policy_revision != self._manifest.trust_revision:
            raise ReceiverRuntimeError(code="TRUST_POLICY_NOT_CONFIGURED", detail="The acceptance trust revision does not match.")
        package_decision = AcceptancePackageDecision(
            skill_version_id=command.skill_version_id,
            runtime_skill_name=command.runtime_skill_name,
            package_digest=command.package_digest,
            package_size_bytes=command.package_size_bytes,
            package_media_type=command.package_media_type,
        )
        if package_decision not in self._manifest.compatible_packages:
            raise ReceiverRuntimeError(
                code="SKILL_INCOMPATIBLE",
                detail="The package is not allowed by the frozen synthetic compatibility policy.",
                status_code=422,
            )
        operation = await super().submit_install(command_payload=command_payload, **kwargs)
        if await self._faults.consume("disconnect_after_receiver_accept"):
            raise AcceptanceTransportDisconnect
        return operation


class _AcceptanceSecretResolver:
    def __init__(self, path: Path, reference: ReceiverSecretReference) -> None:
        self._path = path
        self._reference = reference

    async def resolve(self, reference: ReceiverSecretReference) -> bytes:
        if reference != self._reference:
            raise ValueError("acceptance Secret reference is not authorized")
        try:
            return await asyncio.to_thread(self._path.read_bytes)
        except OSError:
            raise ValueError("acceptance Secret is unavailable") from None


class _DenyAcceptanceHttpAuthenticator:
    async def authenticate(self, request: object):
        del request
        raise ReceiverServiceAuthenticationRequired()


class _AcceptancePrincipalMapper:
    def __init__(self, delegate: ReceiverPrincipalMapper, manifest: AcceptanceManifest) -> None:
        self._delegate = delegate
        self._manifest = manifest

    async def map_principal(self, key_id: str) -> ReceiverTransportPrincipal:
        if key_id != self._manifest.key_id:
            raise ValueError("acceptance principal key ID is not authorized")
        principal = await self._delegate.map_principal(key_id)
        expected_actions = frozenset(
            {
                "receiver:capabilities:read",
                "receiver:user-directory:read",
                "receiver:install:user",
                "receiver:observe:user",
                "receiver:operations:read",
            }
        )
        if principal.subject != self._manifest.principal_id or principal.actions != expected_actions:
            raise ValueError("acceptance principal binding is invalid")
        return principal


class AcceptanceReceiverReleaseBootstrap:
    def __init__(self, settings: AcceptanceSettings) -> None:
        self._settings = settings

    @staticmethod
    def _validate_database() -> None:
        database = get_app_config().database
        if database.backend != "postgres" or database.postgres_schema != "deerflow":
            raise ValueError("acceptance bootstrap requires PostgreSQL schema deerflow")
        raw_url = getattr(database, "app_sqlalchemy_url", None) or database.postgres_url
        url = make_url(raw_url)
        if url.database != "deerflow_acceptance" or url.username != "deerflow_acceptance":
            raise ValueError("acceptance bootstrap requires the isolated Deer Flow database and role")

    async def build(self, config: NexusReceiverConfig) -> ReceiverReleaseComponents:
        manifest = await asyncio.to_thread(AcceptanceManifest.load, self._settings)
        expected_host_key = ReceiverSecretReference(name="joint-acceptance/ssh", key="host-key")
        expected_principal_map = ReceiverSecretReference(name="joint-acceptance/ssh", key="principal-map")
        if (
            not config.enabled
            or config.ssh_account != "nexus-receiver"
            or config.host_key_secret_ref != expected_host_key
            or config.principal_map_secret_ref != expected_principal_map
            or config.directory_policy_revision != manifest.directory_revision
            or config.trust_policy_revision != manifest.trust_revision
            or config.compatibility_policy_revision != manifest.compatibility_revision
            or config.recovery_signal_socket != manifest.recovery_signal_socket
        ):
            raise ValueError("acceptance bootstrap release gates do not match the frozen fixture")
        self._validate_database()
        session_factory = get_session_factory()
        if session_factory is None:
            raise ValueError("acceptance bootstrap requires the real SQL session factory")
        faults = AcceptanceFaultController(self._settings.fault_path)
        installer = AcceptanceUserInstaller(
            faults,
            delegate=UserScopedReceiverInstaller(precommit_scan=_acceptance_precommit_scan),
        )
        handler = AcceptanceReceiverRuntimeHandler(
            manifest=manifest,
            faults=faults,
            store=SqlReceiverOperationStore(session_factory),
            package_store=LocalReceiverPackageStore(self._settings.package_stage_path),
            directory=ControlledAcceptanceDirectory(manifest),
            user_directory=ControlledAcceptanceDirectory(manifest),
            installer=installer,
            runtime_version="0.9.0-acceptance",
            execution_owner="receiver-acceptance",
            user_install_ready=True,
        )
        resolver = _AcceptanceSecretResolver(self._settings.principal_map_path, expected_principal_map)
        principal_mapper = SecretBackedReceiverPrincipalMapper(
            resolver=resolver,
            reference=expected_principal_map,
        )
        return ReceiverReleaseComponents(
            runtime_handler=handler,
            service_authenticator=_DenyAcceptanceHttpAuthenticator(),
            principal_mapper=_AcceptancePrincipalMapper(principal_mapper, manifest),
            recovery_coordinator=SqlReceiverCoordinationStore(session_factory),
        )


def build_acceptance_bootstrap_from_environment(environment: Mapping[str, str] | None = None) -> AcceptanceReceiverReleaseBootstrap:
    return AcceptanceReceiverReleaseBootstrap(load_acceptance_settings(environment))
