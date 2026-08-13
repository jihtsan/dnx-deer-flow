"""Transport-neutral USER-install runtime for the Nexus Skill receiver."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from app.gateway.nexus_receiver.auth import (
    ReceiverProviderError,
    ReceiverServiceActionForbidden,
    ReceiverServicePrincipal,
)
from app.gateway.nexus_receiver.directory import (
    ReceiverDirectoryCursorRejected,
    ReceiverUserDirectoryUnavailable,
)
from app.gateway.nexus_receiver.models import (
    ReceiverAuthorizationSnapshot,
    ReceiverCapabilities,
    ReceiverCapabilitySnapshot,
    ReceiverCursorPage,
    ReceiverInstallCommand,
    ReceiverObservationQuery,
    ReceiverObservedSkill,
    ReceiverOperation,
    ReceiverOperationError,
    ReceiverOperationPhase,
    ReceiverSkillListRequest,
    ReceiverSkillPage,
    ReceiverUserPage,
    ReceiverUserTarget,
)
from app.gateway.nexus_receiver.package_store import ReceiverPackageConflict, ReceiverPackageStore
from app.gateway.nexus_receiver.skill_inventory import (
    HmacReceiverSkillCursorCodec,
    ReceiverSkillCursor,
    ReceiverSkillCursorExpired,
    ReceiverSkillCursorInvalid,
)

_TRANSITIONS: dict[str, frozenset[str]] = {
    "accepted": frozenset({"validating", "rejected"}),
    "validating": frozenset({"installing", "rejected"}),
    "installing": frozenset({"activating", "failed"}),
    "activating": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "rejected": frozenset(),
    "failed": frozenset(),
}
_TERMINAL = frozenset({"succeeded", "rejected", "failed"})
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 2048
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,160}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_DEFAULT_DENY_BLOCKERS = [
    "CAPABILITY_READ_ONLY",
    "USER_DIRECTORY_UNSUPPORTED",
    "GLOBAL_INSTALL_UNSUPPORTED",
    "USER_INSTALL_UNSUPPORTED",
    "GLOBAL_ACTIVATION_UNSUPPORTED",
    "TRUST_POLICY_NOT_CONFIGURED",
    "COMPATIBILITY_UNKNOWN",
]
_EXECUTION_LEASE = timedelta(minutes=5)
_EXECUTION_HEARTBEAT_SECONDS = _EXECUTION_LEASE.total_seconds() / 3
logger = logging.getLogger(__name__)


def _observed_identity_matches(observed: dict[str, Any] | None, command: ReceiverInstallCommand, version: str) -> bool:
    return observed is not None and observed.get("skillVersionId") == command.skill_version_id and observed.get("version") == version and observed.get("packageDigest") == command.package_digest


def _raise_if_observation_unavailable(observed: dict[str, Any] | None) -> None:
    if observed is not None and observed.get("freshness") == "unavailable":
        raise ReceiverRuntimeError(
            code="OBSERVATION_UNAVAILABLE",
            title="Observation unavailable",
            detail="The receiver cannot determine the installed Skill state.",
            status_code=503,
            retryable=True,
        )


class ReceiverRuntimeError(ReceiverProviderError):
    def __init__(
        self,
        *,
        code: str,
        detail: str,
        title: str | None = None,
        status_code: int = 409,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.detail = detail
        self.title = title or code.replace("_", " ").title()
        self.status_code = status_code
        self.retryable = retryable
        super().__init__()


def get_receiver_runtime_handler(app_state: object) -> ReceiverRuntimeHandler:
    handler = getattr(app_state, "nexus_receiver_runtime_handler", None)
    if not isinstance(handler, ReceiverRuntimeHandler):
        raise ReceiverRuntimeError(
            code="RECEIVER_NOT_READY",
            title="Receiver runtime unavailable",
            detail="The receiver write runtime is not configured.",
            status_code=503,
            retryable=True,
        )
    return handler


@dataclass(frozen=True, slots=True)
class ReceiverTargetUser:
    user_id: str
    active: bool
    install_eligible: bool


@dataclass(frozen=True, slots=True)
class ReceiverCapabilityReadiness:
    """Explicit release-gate evidence; support is never inferred from wiring."""

    scope_rbac: bool = False
    durable_operations: bool = False
    native_global_storage: bool = False
    load_probe: bool = False
    runtime_revision_consumer: bool = False
    recovery_fencing: bool = False
    service_authentication: bool = False
    directory_privacy: bool = False
    package_trust: bool = False
    compatibility: bool = False
    shared_volume_topology: bool = False

    @property
    def global_install_supported(self) -> bool:
        return all(
            (
                self.scope_rbac,
                self.durable_operations,
                self.native_global_storage,
                self.load_probe,
                self.runtime_revision_consumer,
                self.recovery_fencing,
                self.service_authentication,
                self.directory_privacy,
                self.package_trust,
                self.compatibility,
                self.shared_volume_topology,
            )
        )


@dataclass(frozen=True, slots=True)
class ReceiverTransportPrincipal:
    """Transport-authenticated authority projected into the shared handler."""

    subject: str
    profile: str
    actions: frozenset[str]

    def __post_init__(self) -> None:
        if self.profile != "ssh_forced_command":
            raise ValueError("receiver transport principal has an invalid profile")
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", self.subject) is None:
            raise ValueError("receiver transport subject has an invalid shape")
        known_actions = {
            "receiver:capabilities:read",
            "receiver:user-directory:read",
            "receiver:skills:list:user",
            "receiver:install:global",
            "receiver:install:user",
            "receiver:observe:global",
            "receiver:observe:user",
            "receiver:operations:read",
        }
        normalized = frozenset(self.actions)
        if normalized - known_actions:
            raise ValueError("receiver transport principal contains unknown actions")
        object.__setattr__(self, "actions", normalized)


class ReceiverInstallDirectory(Protocol):
    async def resolve_install_target(self, user_id: str) -> ReceiverTargetUser | None: ...


class ReceiverUserDirectory(Protocol):
    async def search(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> ReceiverUserPage: ...


class ReceiverUserSkillInstaller(Protocol):
    async def install_user_skill(
        self,
        *,
        user_id: str,
        command: ReceiverInstallCommand,
        manifest_version: str,
        package: bytes,
    ) -> None: ...

    async def activate_user_skill(self, *, user_id: str, runtime_skill_name: str) -> None: ...

    async def observe_user_skill(
        self,
        *,
        user_id: str,
        runtime_skill_name: str,
    ) -> dict[str, Any] | None: ...


class ReceiverGlobalSkillInstaller(Protocol):
    async def install_global_skill(
        self,
        *,
        command: ReceiverInstallCommand,
        manifest_version: str,
        package: bytes,
    ) -> None: ...

    async def activate_global_skill(self, *, runtime_skill_name: str) -> None: ...

    async def observe_global_skill(self, *, runtime_skill_name: str) -> dict[str, Any] | None: ...


class ReceiverSkillInventory(Protocol):
    async def list_user_skills(self, *, user_id: str) -> list: ...

    async def catalog_revision(self, *, user_id: str) -> str: ...

    async def snapshot(self, *, user_id: str) -> tuple[str, list]: ...


class ReceiverTargetAuthorizer(Protocol):
    async def authorize_user_target(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        user_id: str,
    ) -> bool: ...


class ReceiverPackagePolicy(Protocol):
    """Deployment trust and compatibility decision, repeated on recovery."""

    async def validate(
        self,
        *,
        command: ReceiverInstallCommand,
        manifest_version: str,
        package: bytes,
        principal_subject: str | None,
    ) -> None: ...


@dataclass(slots=True)
class ReceiverOperationEntry:
    idempotency_key: str
    request_sha256: str
    command: ReceiverInstallCommand
    operation: ReceiverOperation
    execution_owner: str | None = None
    execution_token: str | None = None
    execution_expires_at: datetime | None = None


class ReceiverOperationStore(Protocol):
    async def reserve(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        command: ReceiverInstallCommand,
        now: datetime,
    ) -> tuple[ReceiverOperationEntry, bool]: ...

    async def get(self, operation_id: str) -> ReceiverOperationEntry | None: ...

    async def list_nonterminal(self) -> list[ReceiverOperationEntry]: ...

    async def try_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> str | None: ...

    async def renew_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        token: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool: ...

    async def release_claim(self, operation_id: str, *, owner: str, token: str) -> None: ...

    async def transition(
        self,
        operation_id: str,
        *,
        phase: ReceiverOperationPhase,
        now: datetime,
        owner: str | None = None,
        token: str | None = None,
        observed: ReceiverObservedSkill | None = None,
        error: ReceiverOperationError | None = None,
    ) -> ReceiverOperationEntry: ...


class InMemoryReceiverOperationStore:
    """Deterministic test/local store; production must inject a durable adapter."""

    def __init__(self) -> None:
        self._by_operation: dict[str, ReceiverOperationEntry] = {}
        self._by_idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def reserve(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        command: ReceiverInstallCommand,
        now: datetime,
    ) -> tuple[ReceiverOperationEntry, bool]:
        operation_id = str(command.receiver_operation_id)
        async with self._lock:
            existing_id = self._by_idempotency.get(idempotency_key)
            operation_existing = self._by_operation.get(operation_id)
            existing = self._by_operation.get(existing_id) if existing_id else operation_existing
            if existing is not None:
                if existing.idempotency_key != idempotency_key or existing.request_sha256 != request_sha256 or existing.command != command:
                    raise ReceiverRuntimeError(
                        code="IDEMPOTENCY_KEY_REUSED",
                        detail="The idempotency key or receiver operation ID is bound to a different request.",
                    )
                return existing, False

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
            entry = ReceiverOperationEntry(
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                command=command,
                operation=operation,
            )
            self._by_operation[operation_id] = entry
            self._by_idempotency[idempotency_key] = operation_id
            return entry, True

    async def get(self, operation_id: str) -> ReceiverOperationEntry | None:
        return self._by_operation.get(operation_id)

    async def list_nonterminal(self) -> list[ReceiverOperationEntry]:
        return [entry for entry in self._by_operation.values() if entry.operation.phase not in _TERMINAL]

    async def try_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> str | None:
        async with self._lock:
            entry = self._by_operation.get(operation_id)
            if entry is None or entry.operation.phase in _TERMINAL:
                return None
            if entry.execution_owner is not None and entry.execution_expires_at is not None and entry.execution_expires_at > now:
                return None
            token = str(uuid4())
            entry.execution_owner = owner
            entry.execution_token = token
            entry.execution_expires_at = expires_at
            return token

    async def renew_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        token: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        async with self._lock:
            entry = self._by_operation.get(operation_id)
            if entry is None or entry.operation.phase in _TERMINAL or entry.execution_owner != owner or entry.execution_token != token or entry.execution_expires_at is None or entry.execution_expires_at <= now:
                return False
            entry.execution_expires_at = expires_at
            return True

    async def release_claim(self, operation_id: str, *, owner: str, token: str) -> None:
        async with self._lock:
            entry = self._by_operation.get(operation_id)
            if entry is not None and entry.execution_owner == owner and entry.execution_token == token:
                entry.execution_owner = None
                entry.execution_token = None
                entry.execution_expires_at = None

    async def transition(
        self,
        operation_id: str,
        *,
        phase: ReceiverOperationPhase,
        now: datetime,
        owner: str | None = None,
        token: str | None = None,
        observed: ReceiverObservedSkill | None = None,
        error: ReceiverOperationError | None = None,
    ) -> ReceiverOperationEntry:
        async with self._lock:
            entry = self._by_operation[operation_id]
            current = entry.operation
            if owner is not None and (token is None or entry.execution_owner != owner or entry.execution_token != token or entry.execution_expires_at is None or entry.execution_expires_at <= now):
                raise ReceiverRuntimeError(
                    code="OBSERVED_STATE_CONFLICT",
                    detail="The receiver operation execution claim is no longer owned by this worker.",
                )
            if phase not in _TRANSITIONS[current.phase]:
                raise ReceiverRuntimeError(
                    code="OBSERVED_STATE_CONFLICT",
                    detail="The durable receiver operation cannot make the requested phase transition.",
                )
            entry.operation = current.model_copy(
                update={
                    "phase": phase,
                    "updated_at": now,
                    "completed_at": now if phase in _TERMINAL else None,
                    "observed": observed,
                    "error": error,
                }
            )
            entry.operation = ReceiverOperation.model_validate(entry.operation)
            return entry


def _safe_error(exc: ReceiverProviderError) -> ReceiverOperationError:
    return ReceiverOperationError(
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        retryable=exc.retryable,
        recovery_action=None,
    )


def _canonical_json(value: object) -> bytes:
    # Contract values contain no floats; sorted, minimal UTF-8 JSON is RFC 8785
    # canonical for this closed data model.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _inspect_package(package: bytes) -> tuple[str, str]:
    from deerflow.skills.installer import is_symlink_member, is_unsafe_zip_member
    from deerflow.skills.validation import _validate_skill_frontmatter

    if len(package) > _MAX_PACKAGE_BYTES:
        raise ReceiverRuntimeError(
            code="PACKAGE_TOO_LARGE",
            detail="The Skill package exceeds the receiver size limit.",
            status_code=413,
        )
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_ENTRIES:
                raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The Skill package has an invalid archive shape.", status_code=422)
            if sum(info.file_size for info in infos) > _MAX_PACKAGE_BYTES:
                raise ReceiverRuntimeError(code="PACKAGE_TOO_LARGE", detail="The expanded Skill package exceeds the receiver size limit.", status_code=413)
            if any(is_unsafe_zip_member(info) or is_symlink_member(info) for info in infos):
                raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The Skill package contains an unsafe archive member.", status_code=422)
            with tempfile.TemporaryDirectory(prefix="nexus-receiver-validate-") as tmp:
                from deerflow.skills.installer import resolve_skill_dir_from_archive, safe_extract_skill_archive

                safe_extract_skill_archive(
                    archive,
                    Path(tmp),
                    max_total_size=_MAX_PACKAGE_BYTES,
                    max_entries=_MAX_ARCHIVE_ENTRIES,
                )
                skill_dir = resolve_skill_dir_from_archive(Path(tmp))
                valid, _message, name = _validate_skill_frontmatter(skill_dir)
                if not valid or not name:
                    raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The Skill package manifest is invalid.", status_code=422)
                from deerflow.skills.frontmatter import split_skill_markdown

                parts, error = split_skill_markdown((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
                if error or parts is None:
                    raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The Skill package manifest is invalid.", status_code=422)
                version = parts.metadata.get("version")
                if not isinstance(version, str) or not 1 <= len(version) <= 100:
                    raise ReceiverRuntimeError(code="PACKAGE_MANIFEST_MISMATCH", detail="The Skill package manifest version is missing or invalid.", status_code=422)
                return name, version
    except ReceiverProviderError:
        raise
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile):
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The Skill package is not a valid safe ZIP archive.", status_code=422) from None


class ReceiverRuntimeHandler:
    def __init__(
        self,
        *,
        store: ReceiverOperationStore,
        package_store: ReceiverPackageStore,
        directory: ReceiverInstallDirectory,
        installer: ReceiverUserSkillInstaller,
        user_directory: ReceiverUserDirectory | None = None,
        runtime_version: str | None = None,
        clock: Callable[[], datetime] | None = None,
        execution_owner: str | None = None,
        pending_recovery_notifier: Callable[[], None] | None = None,
        user_install_ready: bool = False,
        skill_inventory: ReceiverSkillInventory | None = None,
        target_authorizer: ReceiverTargetAuthorizer | None = None,
        catalog_revision_provider: Callable[[], Any] | None = None,
        skill_cursor_codec: HmacReceiverSkillCursorCodec | None = None,
        global_installer: ReceiverGlobalSkillInstaller | None = None,
        capability_readiness: ReceiverCapabilityReadiness | None = None,
        package_policy: ReceiverPackagePolicy | None = None,
    ) -> None:
        self.store = store
        self.package_store = package_store
        self.directory = directory
        self.installer = installer
        self.user_directory = user_directory
        self.runtime_version = runtime_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._execution_owner_prefix = execution_owner or f"receiver-{uuid4().hex}"
        self._pending_recovery_notifier = pending_recovery_notifier
        self._user_install_ready = user_install_ready
        self.skill_inventory = skill_inventory
        self.target_authorizer = target_authorizer
        self._catalog_revision_provider = catalog_revision_provider
        self._skill_cursor_codec = skill_cursor_codec
        self.global_installer = global_installer
        self._capability_readiness = capability_readiness or ReceiverCapabilityReadiness()
        self._package_policy = package_policy
        self._global_install_ready = self._capability_readiness.global_install_supported
        if self._global_install_ready and (global_installer is None or catalog_revision_provider is None):
            raise ValueError("GLOBAL receiver readiness requires installer, observer, and catalog revision providers")

    def set_pending_recovery_notifier(self, notifier: Callable[[], None] | None) -> None:
        """Register the supervised recovery wake-up owned by Gateway lifespan."""
        self._pending_recovery_notifier = notifier

    def _notify_pending_recovery(self) -> None:
        if self._pending_recovery_notifier is None:
            return
        try:
            self._pending_recovery_notifier()
        except Exception:
            logger.exception("Failed to notify Nexus receiver recovery after durable submit")

    async def _validate_package_policy(
        self,
        *,
        command: ReceiverInstallCommand,
        manifest_version: str,
        package: bytes,
        principal_subject: str | None,
    ) -> None:
        if self._package_policy is None:
            return
        try:
            await self._package_policy.validate(
                command=command,
                manifest_version=manifest_version,
                package=package,
                principal_subject=principal_subject,
            )
        except ReceiverProviderError:
            raise
        except Exception:
            raise ReceiverRuntimeError(
                code="TRUST_POLICY_NOT_CONFIGURED",
                title="Trust policy unavailable",
                detail="The receiver package trust or compatibility policy is unavailable.",
                status_code=503,
                retryable=True,
            ) from None

    @staticmethod
    def canonical_command_digest(command_payload: dict[str, Any]) -> str:
        return f"sha256:{hashlib.sha256(_canonical_json(command_payload)).hexdigest()}"

    @staticmethod
    def validate_command(command_payload: dict[str, Any]) -> ReceiverInstallCommand:
        try:
            return ReceiverInstallCommand.model_validate(command_payload)
        except ValidationError:
            raise ReceiverRuntimeError(
                code="INVALID_INSTALLATION_TARGET",
                detail="The receiver install command does not match the closed contract.",
                status_code=422,
            ) from None

    @staticmethod
    def _require(principal: ReceiverServicePrincipal | ReceiverTransportPrincipal, action: str) -> None:
        if action not in principal.actions:
            raise ReceiverServiceActionForbidden()

    async def get_capabilities(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        correlation_id: str | None = None,
    ) -> ReceiverCapabilitySnapshot:
        del correlation_id
        self._require(principal, "receiver:capabilities:read")
        transport_profile = "ssh_v1" if isinstance(principal, ReceiverTransportPrincipal) else "http_v1"
        can_install_user = self._user_install_ready and "receiver:install:user" in principal.actions
        can_install_global = self._global_install_ready and "receiver:install:global" in principal.actions
        can_install = can_install_user or can_install_global
        if self._user_install_ready or self._global_install_ready:
            if self._user_install_ready and self._global_install_ready:
                observation = activation = "global_and_user"
            elif self._global_install_ready:
                observation = activation = "global_only"
            else:
                observation = activation = "user_only"
            capabilities = ReceiverCapabilities(
                user_directory="supported" if self.user_directory is not None else "unsupported",
                global_install="supported" if self._global_install_ready else "unsupported",
                user_install="supported" if self._user_install_ready else "unsupported",
                observation=observation,
                activation=activation,
                durable_operations="supported",
                observed_package_digest="supported",
            )
        else:
            capabilities = ReceiverCapabilities()
        return ReceiverCapabilitySnapshot(
            transport_profile=transport_profile,
            runtime_version=self.runtime_version,
            access_mode="read_write" if can_install else "read_only",
            authorization=ReceiverAuthorizationSnapshot(
                profile=principal.profile,
                granted_actions=sorted(principal.actions),
            ),
            capabilities=capabilities,
            blocked_by=[] if can_install else (["FORBIDDEN"] if self._user_install_ready or self._global_install_ready else _DEFAULT_DENY_BLOCKERS),
            observed_at=self._clock(),
        )

    async def list_users(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        query: str | None,
        cursor: str | None,
        limit: int,
        correlation_id: str | None = None,
    ) -> ReceiverUserPage:
        del correlation_id
        self._require(principal, "receiver:user-directory:read")
        if self.user_directory is None:
            raise ReceiverRuntimeError(
                code="USER_DIRECTORY_UNSUPPORTED",
                title="User directory unsupported",
                detail="A reviewed receiver user-directory provider is not configured.",
            )
        if not 1 <= limit <= 100:
            raise ReceiverRuntimeError(code="INVALID_INSTALLATION_TARGET", detail="The user-directory limit is invalid.", status_code=422)
        try:
            page = await self.user_directory.search(
                principal=principal,
                query=query,
                cursor=cursor,
                limit=limit,
            )
        except ReceiverDirectoryCursorRejected as exc:
            raise ReceiverServiceActionForbidden() from exc
        except ReceiverProviderError:
            raise
        except Exception as exc:
            raise ReceiverUserDirectoryUnavailable() from exc
        return ReceiverUserPage.model_validate(page)

    async def list_skills(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        request_payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> ReceiverSkillPage:
        del correlation_id
        self._require(principal, "receiver:skills:list:user")
        try:
            request = ReceiverSkillListRequest.model_validate(request_payload)
        except ValidationError:
            raise ReceiverRuntimeError(code="INVALID_INSTALLATION_TARGET", detail="The Skill inventory request is invalid.", status_code=422) from None
        if self.skill_inventory is None or self.target_authorizer is None or self._skill_cursor_codec is None:
            raise ReceiverRuntimeError(code="RECEIVER_NOT_READY", detail="The receiver Skill inventory is not configured.", status_code=503, retryable=True)
        user_id = request.target.deer_flow_user_id
        try:
            entitled = await self.target_authorizer.authorize_user_target(principal=principal, user_id=user_id)
        except Exception:
            entitled = False
        if not entitled:
            raise ReceiverServiceActionForbidden()
        user = await self.directory.resolve_install_target(user_id)
        if user is None or user.user_id != user_id:
            raise ReceiverRuntimeError(code="TARGET_USER_NOT_FOUND", detail="The selected DeerFlow user does not exist.", status_code=404)

        normalized_query = (request.query or "").strip().casefold()
        try:
            provider_revision, items = await self.skill_inventory.snapshot(user_id=user_id)
            global_revision = self._catalog_revision_provider() if self._catalog_revision_provider is not None else None
            if asyncio.iscoroutine(global_revision):
                global_revision = await global_revision
            revision = f"{global_revision or '0'}:{provider_revision}"
            last_sort_key = ""
            if request.cursor is not None:
                decoded = self._skill_cursor_codec.decode(request.cursor)
                if not self._skill_cursor_codec.matches_request(
                    decoded,
                    principal_subject=principal.subject,
                    user_id=user_id,
                    normalized_query=normalized_query,
                ):
                    raise ReceiverSkillCursorInvalid()
                if not self._skill_cursor_codec.matches_revision(decoded, revision):
                    raise ReceiverSkillCursorExpired()
                last_sort_key = decoded.last_sort_key
        except ReceiverSkillCursorExpired:
            raise ReceiverRuntimeError(code="CURSOR_EXPIRED", title="Cursor expired", detail="The cursor catalog revision is no longer available; restart without a cursor.") from None
        except ReceiverSkillCursorInvalid:
            raise ReceiverRuntimeError(code="CURSOR_INVALID", title="Cursor invalid", detail="The supplied cursor is invalid for this request.", status_code=422) from None
        except ReceiverProviderError:
            raise
        except Exception:
            raise ReceiverRuntimeError(code="OBSERVATION_UNAVAILABLE", title="Observation unavailable", detail="The receiver Skill inventory is temporarily unavailable.", status_code=503, retryable=True) from None

        ordered = sorted(items, key=lambda item: item.runtime_skill_name.casefold())
        if normalized_query:
            ordered = [item for item in ordered if normalized_query in item.runtime_skill_name.casefold()]
        if last_sort_key:
            ordered = [item for item in ordered if item.runtime_skill_name.casefold() > last_sort_key]
        page_items = ordered[: request.limit]
        has_more = len(ordered) > len(page_items)
        next_cursor = None
        if has_more and page_items:
            next_cursor = self._skill_cursor_codec.encode(
                ReceiverSkillCursor(
                    principal_subject=principal.subject,
                    user_id=user_id,
                    normalized_query=normalized_query,
                    catalog_revision=revision,
                    last_sort_key=page_items[-1].runtime_skill_name.casefold(),
                )
            )
        return ReceiverSkillPage(
            items=page_items,
            page=ReceiverCursorPage(has_more=has_more, next_cursor=next_cursor),
            observed_at=self._clock(),
        )

    async def submit_install(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        idempotency_key: str,
        request_sha256: str,
        command_payload: dict[str, Any],
        package: bytes,
        correlation_id: str | None = None,
    ) -> ReceiverOperation:
        del correlation_id
        command = self.validate_command(command_payload)
        if _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key) is None:
            raise ReceiverRuntimeError(
                code="IDEMPOTENCY_KEY_REUSED",
                detail="The idempotency key does not match the receiver contract.",
                status_code=422,
            )
        if _DIGEST_PATTERN.fullmatch(request_sha256) is None:
            raise ReceiverRuntimeError(
                code="DIGEST_MISMATCH",
                detail="The canonical command digest does not match the receiver contract.",
                status_code=422,
            )
        if command.target.scope == "GLOBAL":
            self._require(principal, "receiver:install:global")
            if not self._global_install_ready or self.global_installer is None:
                raise ReceiverRuntimeError(
                    code="GLOBAL_INSTALL_UNSUPPORTED",
                    title="Global installation is unsupported",
                    detail="The receiver GLOBAL release gates are not ready.",
                )
        else:
            self._require(principal, "receiver:install:user")
        if request_sha256 != self.canonical_command_digest(command_payload):
            raise ReceiverRuntimeError(code="DIGEST_MISMATCH", detail="The canonical command digest does not match.", status_code=422)
        if len(package) != command.package_size_bytes:
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The package byte count does not match the command.", status_code=422)
        package_digest = f"sha256:{hashlib.sha256(package).hexdigest()}"
        if package_digest != command.package_digest:
            raise ReceiverRuntimeError(code="DIGEST_MISMATCH", detail="The Skill package digest does not match.", status_code=422)

        manifest_name, _manifest_version = await asyncio.to_thread(_inspect_package, package)
        if manifest_name != command.runtime_skill_name:
            raise ReceiverRuntimeError(
                code="PACKAGE_MANIFEST_MISMATCH",
                detail="The Skill package manifest name does not match runtimeSkillName.",
                status_code=422,
            )
        await self._validate_package_policy(
            command=command,
            manifest_version=_manifest_version,
            package=package,
            principal_subject=principal.subject,
        )

        operation_id = str(command.receiver_operation_id)
        try:
            await self.package_store.stage(operation_id, command.package_digest, package)
        except ReceiverPackageConflict:
            raise ReceiverRuntimeError(
                code="IDEMPOTENCY_KEY_REUSED",
                detail="The receiver operation ID is already bound to different package bytes.",
            ) from None
        except (OSError, ValueError):
            raise ReceiverRuntimeError(
                code="RECEIVER_NOT_READY",
                title="Receiver staging unavailable",
                detail="The receiver cannot durably stage the Skill package.",
                status_code=503,
                retryable=True,
            ) from None

        try:
            entry, created = await self.store.reserve(
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                command=command,
                now=self._clock(),
            )
        except ReceiverRuntimeError:
            existing = await self.store.get(operation_id)
            if existing is None or existing.command.package_digest != command.package_digest:
                await self._delete_staged_package(operation_id, command.package_digest)
            raise
        if not created and entry.operation.phase in _TERMINAL:
            await self._delete_staged_package(operation_id, command.package_digest)
            return entry.operation
        self._notify_pending_recovery()
        return entry.operation

    async def recover_pending(self) -> list[ReceiverOperation]:
        """Resume staged nonterminal operations without requiring a POST replay."""
        recovered: list[ReceiverOperation] = []
        for entry in await self.store.list_nonterminal():
            operation_id = str(entry.command.receiver_operation_id)
            try:
                package = await self.package_store.read(operation_id, entry.command.package_digest)
            except (OSError, ValueError):
                package = None
            if package is None:
                claim_owner = self._execution_owner_prefix
                claim_token = await self.store.try_claim(
                    operation_id,
                    owner=claim_owner,
                    now=self._clock(),
                    expires_at=self._clock() + _EXECUTION_LEASE,
                )
                if claim_token is None:
                    current = await self.store.get(operation_id)
                    if current is not None:
                        recovered.append(current.operation)
                    continue
                try:
                    exc = ReceiverRuntimeError(
                        code="INTERNAL_ERROR",
                        title="Receiver staging unavailable",
                        detail="The receiver cannot recover the staged Skill package.",
                        status_code=500,
                    )
                    current = await self.store.get(operation_id)
                    if current is not None and current.operation.phase not in _TERMINAL:
                        terminal: ReceiverOperationPhase = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                        current = await self.store.transition(
                            operation_id,
                            phase=terminal,
                            now=self._clock(),
                            owner=claim_owner,
                            token=claim_token,
                            error=_safe_error(exc),
                        )
                        recovered.append(current.operation)
                finally:
                    await self.store.release_claim(operation_id, owner=claim_owner, token=claim_token)
                continue
            try:
                recovered.append(await self._run_claimed(entry, package))
            except ReceiverProviderError:
                current = await self.store.get(operation_id)
                if current is not None:
                    recovered.append(current.operation)
        return recovered

    async def _run_claimed(self, entry: ReceiverOperationEntry, package: bytes) -> ReceiverOperation:
        operation_id = str(entry.command.receiver_operation_id)
        claim_owner = self._execution_owner_prefix
        claim_token = await self.store.try_claim(
            operation_id,
            owner=claim_owner,
            now=self._clock(),
            expires_at=self._clock() + _EXECUTION_LEASE,
        )
        if claim_token is None:
            current = await self.store.get(operation_id)
            if current is None:
                raise ReceiverRuntimeError(code="INTERNAL_ERROR", detail="The receiver operation disappeared.", status_code=500)
            return current.operation
        heartbeat_stop = asyncio.Event()
        claim_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._maintain_execution_claim(
                operation_id,
                owner=claim_owner,
                token=claim_token,
                stop=heartbeat_stop,
                claim_lost=claim_lost,
            ),
            name=f"nexus-receiver-operation-heartbeat-{operation_id}",
        )
        try:
            current = await self.store.get(operation_id)
            if current is None:
                raise ReceiverRuntimeError(code="INTERNAL_ERROR", detail="The receiver operation disappeared.", status_code=500)
            install = asyncio.create_task(
                self._continue_install(current, package, owner=claim_owner, token=claim_token),
                name=f"nexus-receiver-operation-{operation_id}",
            )
            lost_waiter = asyncio.create_task(claim_lost.wait())
            done, _pending = await asyncio.wait({install, lost_waiter}, return_when=asyncio.FIRST_COMPLETED)
            if lost_waiter in done and claim_lost.is_set():
                install.cancel()
                with suppress(asyncio.CancelledError):
                    await install
                raise ReceiverRuntimeError(
                    code="OBSERVED_STATE_CONFLICT",
                    detail="The receiver operation execution claim is no longer owned by this worker.",
                )
            lost_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await lost_waiter
            return await install
        finally:
            heartbeat_stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await self.store.release_claim(operation_id, owner=claim_owner, token=claim_token)
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase in _TERMINAL:
                await self._delete_staged_package(operation_id, current.command.package_digest)

    async def _delete_staged_package(self, operation_id: str, digest: str) -> None:
        try:
            await self.package_store.delete(operation_id, digest)
        except (OSError, ValueError):
            logger.warning("Failed to remove terminal receiver package staging for operation %s", operation_id)

    async def _resolve_install_target(self, user_id: str) -> ReceiverTargetUser:
        user = await self.directory.resolve_install_target(user_id)
        if user is None or user.user_id != user_id:
            raise ReceiverRuntimeError(code="TARGET_USER_NOT_FOUND", detail="The selected DeerFlow user does not exist.", status_code=404)
        if not user.active or not user.install_eligible:
            raise ReceiverRuntimeError(code="TARGET_USER_NOT_ELIGIBLE", detail="The selected DeerFlow user is not eligible for Skill installation.")
        return user

    async def _renew_execution_claim(self, operation_id: str, *, owner: str, token: str) -> None:
        now = self._clock()
        renewed = await self.store.renew_claim(
            operation_id,
            owner=owner,
            token=token,
            now=now,
            expires_at=now + _EXECUTION_LEASE,
        )
        if not renewed:
            raise ReceiverRuntimeError(
                code="OBSERVED_STATE_CONFLICT",
                detail="The receiver operation execution claim is no longer owned by this worker.",
            )

    async def _maintain_execution_claim(
        self,
        operation_id: str,
        *,
        owner: str,
        token: str,
        stop: asyncio.Event,
        claim_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=_EXECUTION_HEARTBEAT_SECONDS)
                return
            except TimeoutError:
                pass
            try:
                renewed = await self.store.renew_claim(
                    operation_id,
                    owner=owner,
                    token=token,
                    now=self._clock(),
                    expires_at=self._clock() + _EXECUTION_LEASE,
                )
            except Exception:
                logger.exception("Nexus receiver operation lease renewal failed for operation %s", operation_id)
                renewed = False
            if not renewed:
                claim_lost.set()
                return

    async def _continue_install(self, entry: ReceiverOperationEntry, package: bytes, *, owner: str, token: str) -> ReceiverOperation:
        command = entry.command
        operation_id = str(command.receiver_operation_id)
        if command.target.scope == "GLOBAL":
            return await self._continue_global_install(entry, package, owner=owner, token=token)
        assert isinstance(command.target, ReceiverUserTarget)
        try:
            manifest_name, manifest_version = await asyncio.to_thread(_inspect_package, package)
            if manifest_name != command.runtime_skill_name:
                raise ReceiverRuntimeError(
                    code="PACKAGE_MANIFEST_MISMATCH",
                    detail="The Skill package manifest name does not match runtimeSkillName.",
                    status_code=422,
                )
            await self._validate_package_policy(command=command, manifest_version=manifest_version, package=package, principal_subject=None)
            user = await self._resolve_install_target(command.target.deer_flow_user_id)
            if entry.operation.phase == "accepted":
                entry = await self.store.transition(operation_id, phase="validating", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "validating":
                existing = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                if existing is not None:
                    _raise_if_observation_unavailable(existing)
                    if _observed_identity_matches(existing, command, manifest_version):
                        raise ReceiverRuntimeError(code="SKILL_ALREADY_INSTALLED", detail="The exact Skill version is already installed.")
                    raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                entry = await self.store.transition(operation_id, phase="installing", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "installing":
                await self._renew_execution_claim(operation_id, owner=owner, token=token)
                # A crash may occur after the atomic filesystem install but
                # before the durable phase advances. Exact observation lets a
                # replay close that window without a second write.
                installed = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                _raise_if_observation_unavailable(installed)
                exact = _observed_identity_matches(installed, command, manifest_version)
                if not exact:
                    if installed is not None:
                        raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                    from deerflow.skills.installer import SkillAlreadyExistsError

                    try:
                        await self._renew_execution_claim(operation_id, owner=owner, token=token)
                        await self.installer.install_user_skill(
                            user_id=user.user_id,
                            command=command,
                            manifest_version=manifest_version,
                            package=package,
                        )
                    except SkillAlreadyExistsError:
                        installed = await self.installer.observe_user_skill(
                            user_id=user.user_id,
                            runtime_skill_name=command.runtime_skill_name,
                        )
                        _raise_if_observation_unavailable(installed)
                        exact = _observed_identity_matches(installed, command, manifest_version)
                        if not exact:
                            raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A Skill already uses runtimeSkillName.") from None
                entry = await self.store.transition(operation_id, phase="activating", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "activating":
                await self._renew_execution_claim(operation_id, owner=owner, token=token)
                installed = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                _raise_if_observation_unavailable(installed)
                exact_identity = _observed_identity_matches(installed, command, manifest_version)
                if not exact_identity:
                    raise ReceiverRuntimeError(code="ACTIVATION_FAILED", detail="The installed Skill identity could not be recovered for activation.")
                await self.installer.activate_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                observed_data = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                if observed_data is None:
                    raise ReceiverRuntimeError(code="ACTIVATION_FAILED", detail="The installed Skill could not be observed as loaded.")
                observed = ReceiverObservedSkill(
                    target=command.target,
                    presence="installed",
                    skill_version_id=observed_data.get("skillVersionId"),
                    version=observed_data.get("version"),
                    package_digest=observed_data.get("packageDigest"),
                    runtime_skill_name=command.runtime_skill_name,
                    enabled=observed_data.get("enabled"),
                    load_state=observed_data.get("loadState", "unknown"),
                    freshness="current",
                    observed_at=self._clock(),
                )
                if observed.skill_version_id != command.skill_version_id or observed.version != manifest_version or observed.package_digest != command.package_digest or observed.enabled is not True or observed.load_state != "loaded":
                    raise ReceiverRuntimeError(code="OBSERVED_STATE_CONFLICT", detail="Observed state does not exactly prove the requested installation.")
                entry = await self.store.transition(
                    operation_id,
                    phase="succeeded",
                    now=self._clock(),
                    owner=owner,
                    token=token,
                    observed=observed,
                )
            return entry.operation
        except ReceiverProviderError as exc:
            if exc.code == "TRUST_POLICY_NOT_CONFIGURED" and bool(getattr(exc, "retryable", False)):
                raise
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase not in _TERMINAL:
                terminal: ReceiverOperationPhase = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                await self.store.transition(
                    operation_id,
                    phase=terminal,
                    now=self._clock(),
                    owner=owner,
                    token=token,
                    error=_safe_error(exc),
                )
            raise
        except Exception:
            exc = ReceiverRuntimeError(
                code="INTERNAL_ERROR",
                title="Receiver internal error",
                detail="The receiver could not complete the operation.",
                status_code=500,
            )
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase not in _TERMINAL:
                terminal = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                await self.store.transition(
                    operation_id,
                    phase=terminal,
                    now=self._clock(),
                    owner=owner,
                    token=token,
                    error=_safe_error(exc),
                )
            raise exc from None

    async def _continue_global_install(
        self,
        entry: ReceiverOperationEntry,
        package: bytes,
        *,
        owner: str,
        token: str,
    ) -> ReceiverOperation:
        command = entry.command
        operation_id = str(command.receiver_operation_id)
        if not self._global_install_ready or self.global_installer is None:
            raise ReceiverRuntimeError(code="GLOBAL_INSTALL_UNSUPPORTED", detail="The receiver GLOBAL release gates are not ready.")
        try:
            manifest_name, manifest_version = await asyncio.to_thread(_inspect_package, package)
            if manifest_name != command.runtime_skill_name:
                raise ReceiverRuntimeError(code="PACKAGE_MANIFEST_MISMATCH", detail="The Skill package manifest name does not match runtimeSkillName.", status_code=422)
            await self._validate_package_policy(command=command, manifest_version=manifest_version, package=package, principal_subject=None)
            if entry.operation.phase == "accepted":
                entry = await self.store.transition(operation_id, phase="validating", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "validating":
                existing = await self.global_installer.observe_global_skill(runtime_skill_name=command.runtime_skill_name)
                if existing is not None:
                    _raise_if_observation_unavailable(existing)
                    exact = _observed_identity_matches(existing, command, manifest_version)
                    if exact:
                        raise ReceiverRuntimeError(code="SKILL_ALREADY_INSTALLED", detail="The exact Skill version is already installed.")
                    raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                entry = await self.store.transition(operation_id, phase="installing", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "installing":
                await self._renew_execution_claim(operation_id, owner=owner, token=token)
                installed = await self.global_installer.observe_global_skill(runtime_skill_name=command.runtime_skill_name)
                _raise_if_observation_unavailable(installed)
                exact = _observed_identity_matches(installed, command, manifest_version)
                if not exact:
                    if installed is not None:
                        raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                    await self.global_installer.install_global_skill(
                        command=command,
                        manifest_version=manifest_version,
                        package=package,
                    )
                entry = await self.store.transition(operation_id, phase="activating", now=self._clock(), owner=owner, token=token)
            if entry.operation.phase == "activating":
                await self._renew_execution_claim(operation_id, owner=owner, token=token)
                installed = await self.global_installer.observe_global_skill(runtime_skill_name=command.runtime_skill_name)
                _raise_if_observation_unavailable(installed)
                exact = _observed_identity_matches(installed, command, manifest_version)
                if not exact:
                    raise ReceiverRuntimeError(code="ACTIVATION_FAILED", detail="The GLOBAL Skill identity could not be recovered for activation.")
                await self.global_installer.activate_global_skill(runtime_skill_name=command.runtime_skill_name)
                observed_data = await self.global_installer.observe_global_skill(runtime_skill_name=command.runtime_skill_name)
                if observed_data is None:
                    raise ReceiverRuntimeError(code="ACTIVATION_FAILED", detail="The GLOBAL Skill could not be observed as loaded.")
                observed = ReceiverObservedSkill(
                    target=command.target,
                    presence="installed",
                    skill_version_id=observed_data.get("skillVersionId"),
                    version=observed_data.get("version"),
                    package_digest=observed_data.get("packageDigest"),
                    runtime_skill_name=command.runtime_skill_name,
                    enabled=observed_data.get("enabled"),
                    load_state=observed_data.get("loadState", "unknown"),
                    freshness=observed_data.get("freshness", "unavailable"),
                    observed_at=self._clock(),
                )
                if (
                    observed.skill_version_id != command.skill_version_id
                    or observed.version != manifest_version
                    or observed.package_digest != command.package_digest
                    or observed.enabled is not True
                    or observed.load_state != "loaded"
                    or observed.freshness != "current"
                ):
                    raise ReceiverRuntimeError(code="OBSERVED_STATE_CONFLICT", detail="Observed GLOBAL state does not exactly prove the requested installation.")
                entry = await self.store.transition(
                    operation_id,
                    phase="succeeded",
                    now=self._clock(),
                    owner=owner,
                    token=token,
                    observed=observed,
                )
            return entry.operation
        except ReceiverProviderError as exc:
            if exc.code == "TRUST_POLICY_NOT_CONFIGURED" and bool(getattr(exc, "retryable", False)):
                raise
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase not in _TERMINAL:
                terminal: ReceiverOperationPhase = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                await self.store.transition(operation_id, phase=terminal, now=self._clock(), owner=owner, token=token, error=_safe_error(exc))
            raise
        except Exception:
            exc = ReceiverRuntimeError(code="INTERNAL_ERROR", title="Receiver internal error", detail="The receiver could not complete the operation.", status_code=500)
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase not in _TERMINAL:
                terminal = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                await self.store.transition(operation_id, phase=terminal, now=self._clock(), owner=owner, token=token, error=_safe_error(exc))
            raise exc from None

    async def get_operation(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        operation_id: str,
        correlation_id: str | None = None,
    ) -> ReceiverOperation:
        del correlation_id
        self._require(principal, "receiver:operations:read")
        entry = await self.store.get(operation_id)
        if entry is None:
            raise ReceiverRuntimeError(
                code="INVALID_INSTALLATION_TARGET",
                detail="The receiver operation does not exist.",
                status_code=404,
            )
        scope_action = "receiver:observe:global" if entry.operation.target.scope == "GLOBAL" else "receiver:observe:user"
        if scope_action not in principal.actions:
            raise ReceiverRuntimeError(
                code="INVALID_INSTALLATION_TARGET",
                detail="The receiver operation does not exist.",
                status_code=404,
            )
        return entry.operation

    async def query_observation(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        query_payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> ReceiverObservedSkill:
        del correlation_id
        try:
            query = ReceiverObservationQuery.model_validate(query_payload)
        except ValidationError:
            raise ReceiverRuntimeError(code="INVALID_INSTALLATION_TARGET", detail="The observation query is invalid.", status_code=422) from None
        if query.target.scope == "GLOBAL":
            self._require(principal, "receiver:observe:global")
            if not self._global_install_ready or self.global_installer is None:
                raise ReceiverRuntimeError(code="GLOBAL_INSTALL_UNSUPPORTED", detail="Global Skill observation is unsupported.")
            try:
                observed_data = await self.global_installer.observe_global_skill(runtime_skill_name=query.runtime_skill_name)
            except ReceiverProviderError:
                raise
            except Exception:
                raise ReceiverRuntimeError(
                    code="OBSERVATION_UNAVAILABLE",
                    title="Observation unavailable",
                    detail="The receiver cannot determine the installed Skill state.",
                    status_code=503,
                    retryable=True,
                ) from None
        else:
            self._require(principal, "receiver:observe:user")
            try:
                observed_data = await self.installer.observe_user_skill(
                    user_id=query.target.deer_flow_user_id,
                    runtime_skill_name=query.runtime_skill_name,
                )
            except ReceiverProviderError:
                raise
            except Exception:
                raise ReceiverRuntimeError(
                    code="OBSERVATION_UNAVAILABLE",
                    title="Observation unavailable",
                    detail="The receiver cannot determine the installed Skill state.",
                    status_code=503,
                    retryable=True,
                ) from None
        if observed_data is None:
            return ReceiverObservedSkill(
                target=query.target,
                presence="absent",
                skill_version_id=None,
                version=None,
                package_digest=None,
                runtime_skill_name=query.runtime_skill_name,
                enabled=None,
                load_state="absent",
                freshness="current",
                observed_at=self._clock(),
            )
        return ReceiverObservedSkill(
            target=query.target,
            presence="installed",
            skill_version_id=observed_data.get("skillVersionId"),
            version=observed_data.get("version"),
            package_digest=observed_data.get("packageDigest"),
            runtime_skill_name=query.runtime_skill_name,
            enabled=observed_data.get("enabled"),
            load_state=observed_data.get("loadState", "unknown"),
            freshness=observed_data.get("freshness", "current"),
            observed_at=self._clock(),
        )
