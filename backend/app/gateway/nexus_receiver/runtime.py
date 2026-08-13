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
    ReceiverInstallCommand,
    ReceiverObservationQuery,
    ReceiverObservedSkill,
    ReceiverOperation,
    ReceiverOperationError,
    ReceiverOperationPhase,
    ReceiverUserPage,
    ReceiverUserTarget,
)
from app.gateway.nexus_receiver.package_store import ReceiverPackageConflict, ReceiverPackageStore

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
logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class ReceiverOperationEntry:
    idempotency_key: str
    request_sha256: str
    command: ReceiverInstallCommand
    operation: ReceiverOperation
    execution_owner: str | None = None
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
    ) -> bool: ...

    async def release_claim(self, operation_id: str, *, owner: str) -> None: ...

    async def transition(
        self,
        operation_id: str,
        *,
        phase: ReceiverOperationPhase,
        now: datetime,
        owner: str | None = None,
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
    ) -> bool:
        async with self._lock:
            entry = self._by_operation.get(operation_id)
            if entry is None or entry.operation.phase in _TERMINAL:
                return False
            if entry.execution_owner is not None and entry.execution_expires_at is not None and entry.execution_expires_at > now:
                return False
            entry.execution_owner = owner
            entry.execution_expires_at = expires_at
            return True

    async def release_claim(self, operation_id: str, *, owner: str) -> None:
        async with self._lock:
            entry = self._by_operation.get(operation_id)
            if entry is not None and entry.execution_owner == owner:
                entry.execution_owner = None
                entry.execution_expires_at = None

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
        async with self._lock:
            entry = self._by_operation[operation_id]
            current = entry.operation
            if owner is not None and (entry.execution_owner != owner or entry.execution_expires_at is None or entry.execution_expires_at <= now):
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
    ) -> ReceiverCapabilitySnapshot:
        self._require(principal, "receiver:capabilities:read")
        transport_profile = "ssh_v1" if isinstance(principal, ReceiverTransportPrincipal) else "http_v1"
        can_install = self._user_install_ready and "receiver:install:user" in principal.actions
        return ReceiverCapabilitySnapshot(
            transport_profile=transport_profile,
            runtime_version=self.runtime_version,
            access_mode="read_write" if can_install else "read_only",
            authorization=ReceiverAuthorizationSnapshot(
                profile=principal.profile,
                granted_actions=sorted(principal.actions),
            ),
            capabilities=(
                ReceiverCapabilities(
                    user_directory="supported",
                    global_install="unsupported",
                    user_install="supported",
                    observation="user_only",
                    activation="user_only",
                    durable_operations="supported",
                    observed_package_digest="supported",
                )
                if self._user_install_ready
                else ReceiverCapabilities()
            ),
            blocked_by=[] if can_install else (["FORBIDDEN"] if self._user_install_ready else _DEFAULT_DENY_BLOCKERS),
            observed_at=self._clock(),
        )

    async def list_users(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> ReceiverUserPage:
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

    async def submit_install(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        idempotency_key: str,
        request_sha256: str,
        command_payload: dict[str, Any],
        package: bytes,
    ) -> ReceiverOperation:
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
            raise ReceiverRuntimeError(
                code="GLOBAL_INSTALL_UNSUPPORTED",
                title="Global installation is unsupported",
                detail="The receiver runtime supports USER installation only.",
            )
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
                claim_owner = f"{self._execution_owner_prefix}:{uuid4().hex}"
                claimed = await self.store.try_claim(
                    operation_id,
                    owner=claim_owner,
                    now=self._clock(),
                    expires_at=self._clock() + _EXECUTION_LEASE,
                )
                if not claimed:
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
                            error=_safe_error(exc),
                        )
                        recovered.append(current.operation)
                finally:
                    await self.store.release_claim(operation_id, owner=claim_owner)
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
        claim_owner = f"{self._execution_owner_prefix}:{uuid4().hex}"
        claimed = await self.store.try_claim(
            operation_id,
            owner=claim_owner,
            now=self._clock(),
            expires_at=self._clock() + _EXECUTION_LEASE,
        )
        if not claimed:
            current = await self.store.get(operation_id)
            if current is None:
                raise ReceiverRuntimeError(code="INTERNAL_ERROR", detail="The receiver operation disappeared.", status_code=500)
            return current.operation
        try:
            current = await self.store.get(operation_id)
            if current is None:
                raise ReceiverRuntimeError(code="INTERNAL_ERROR", detail="The receiver operation disappeared.", status_code=500)
            return await self._continue_install(current, package, owner=claim_owner)
        finally:
            await self.store.release_claim(operation_id, owner=claim_owner)
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

    async def _continue_install(self, entry: ReceiverOperationEntry, package: bytes, *, owner: str) -> ReceiverOperation:
        command = entry.command
        operation_id = str(command.receiver_operation_id)
        assert isinstance(command.target, ReceiverUserTarget)
        try:
            manifest_name, manifest_version = await asyncio.to_thread(_inspect_package, package)
            if manifest_name != command.runtime_skill_name:
                raise ReceiverRuntimeError(
                    code="PACKAGE_MANIFEST_MISMATCH",
                    detail="The Skill package manifest name does not match runtimeSkillName.",
                    status_code=422,
                )
            user = await self._resolve_install_target(command.target.deer_flow_user_id)
            if entry.operation.phase == "accepted":
                entry = await self.store.transition(operation_id, phase="validating", now=self._clock(), owner=owner)
            if entry.operation.phase == "validating":
                existing = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                if existing is not None:
                    if existing.get("skillVersionId") == command.skill_version_id and existing.get("packageDigest") == command.package_digest:
                        raise ReceiverRuntimeError(code="SKILL_ALREADY_INSTALLED", detail="The exact Skill version is already installed.")
                    raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                entry = await self.store.transition(operation_id, phase="installing", now=self._clock(), owner=owner)
            if entry.operation.phase == "installing":
                # A crash may occur after the atomic filesystem install but
                # before the durable phase advances. Exact observation lets a
                # replay close that window without a second write.
                installed = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                exact = installed is not None and installed.get("skillVersionId") == command.skill_version_id and installed.get("packageDigest") == command.package_digest
                if not exact:
                    if installed is not None:
                        raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A different Skill already uses runtimeSkillName.")
                    from deerflow.skills.installer import SkillAlreadyExistsError

                    try:
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
                        exact = installed is not None and installed.get("skillVersionId") == command.skill_version_id and installed.get("packageDigest") == command.package_digest
                        if not exact:
                            raise ReceiverRuntimeError(code="SKILL_NAME_CONFLICT", detail="A Skill already uses runtimeSkillName.") from None
                entry = await self.store.transition(operation_id, phase="activating", now=self._clock(), owner=owner)
            if entry.operation.phase == "activating":
                installed = await self.installer.observe_user_skill(
                    user_id=user.user_id,
                    runtime_skill_name=command.runtime_skill_name,
                )
                exact_identity = installed is not None and installed.get("skillVersionId") == command.skill_version_id and installed.get("packageDigest") == command.package_digest
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
                    observed=observed,
                )
            return entry.operation
        except ReceiverProviderError as exc:
            current = await self.store.get(operation_id)
            if current is not None and current.operation.phase not in _TERMINAL:
                terminal: ReceiverOperationPhase = "rejected" if current.operation.phase in {"accepted", "validating"} else "failed"
                await self.store.transition(
                    operation_id,
                    phase=terminal,
                    now=self._clock(),
                    owner=owner,
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
                    error=_safe_error(exc),
                )
            raise exc from None

    async def get_operation(
        self,
        *,
        principal: ReceiverServicePrincipal | ReceiverTransportPrincipal,
        operation_id: str,
    ) -> ReceiverOperation:
        self._require(principal, "receiver:operations:read")
        entry = await self.store.get(operation_id)
        if entry is None:
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
    ) -> ReceiverObservedSkill:
        try:
            query = ReceiverObservationQuery.model_validate(query_payload)
        except ValidationError:
            raise ReceiverRuntimeError(code="INVALID_INSTALLATION_TARGET", detail="The observation query is invalid.", status_code=422) from None
        if query.target.scope == "GLOBAL":
            self._require(principal, "receiver:observe:global")
            raise ReceiverRuntimeError(code="GLOBAL_INSTALL_UNSUPPORTED", detail="Global Skill observation is unsupported.")
        self._require(principal, "receiver:observe:user")
        observed_data = await self.installer.observe_user_skill(
            user_id=query.target.deer_flow_user_id,
            runtime_skill_name=query.runtime_skill_name,
        )
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
            freshness="current",
            observed_at=self._clock(),
        )
