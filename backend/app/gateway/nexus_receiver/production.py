"""Fail-closed production composition for the Nexus Skill receiver.

The repository owns the composition and native storage boundaries. Operators
must supply the enterprise identity, directory, policy, audit, rate-limit,
Secret, and shared-volume implementations as one reviewed provider bundle.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from app.gateway.nexus_receiver.auth import ReceiverServiceAuthenticator
from app.gateway.nexus_receiver.coordination import GlobalManagedSkillCommitter, SqlReceiverCoordinationStore
from app.gateway.nexus_receiver.installer import GlobalScopedReceiverInstaller, UserScopedReceiverInstaller
from app.gateway.nexus_receiver.package_store import LocalReceiverPackageStore
from app.gateway.nexus_receiver.release import (
    ReceiverPrincipalMapper,
    ReceiverReleaseComponents,
    ReceiverSecretResolver,
    SecretBackedReceiverPrincipalMapper,
    parse_receiver_principal_map,
)
from app.gateway.nexus_receiver.runtime import (
    ReceiverCapabilityReadiness,
    ReceiverInstallDirectory,
    ReceiverPackagePolicy,
    ReceiverRuntimeHandler,
    ReceiverTargetAuthorizer,
    ReceiverUserDirectory,
)
from app.gateway.nexus_receiver.skill_inventory import HmacReceiverSkillCursorCodec, UserScopedReceiverSkillInventory
from app.gateway.nexus_receiver.store import SqlReceiverOperationStore
from deerflow.config.app_config import get_app_config
from deerflow.config.nexus_receiver_config import NexusReceiverConfig


class ReceiverAuditSink(Protocol):
    async def record(self, *, action: str, principal_subject: str, correlation_id: str, outcome: str) -> None: ...


class ReceiverRateLimiter(Protocol):
    async def check(self, *, action: str, principal_subject: str) -> None: ...


class ReceiverSharedVolumeProbe(Protocol):
    async def verify(
        self,
        *,
        global_storage_path: Path,
        package_stage_path: Path,
        global_writable: bool,
    ) -> None: ...


class ReceiverProductionReadinessProbe(Protocol):
    async def verify(self, *, config: NexusReceiverConfig) -> None: ...


@dataclass(frozen=True, slots=True)
class ProductionReceiverProviderBundle:
    secret_resolver: ReceiverSecretResolver
    service_authenticator: ReceiverServiceAuthenticator
    directory: ReceiverInstallDirectory | ReceiverUserDirectory
    target_authorizer: ReceiverTargetAuthorizer
    package_policy: ReceiverPackagePolicy
    audit_sink: ReceiverAuditSink
    rate_limiter: ReceiverRateLimiter
    shared_volume_probe: ReceiverSharedVolumeProbe
    readiness_probe: ReceiverProductionReadinessProbe


def load_production_provider_factory(path: str):
    try:
        module_name, attribute = path.split(":", 1)
        factory = getattr(import_module(module_name), attribute)
    except (ImportError, AttributeError, ValueError):
        raise ValueError("receiver production provider factory is unavailable") from None
    if not callable(factory):
        raise ValueError("receiver production provider factory is unavailable")
    return factory


def _require_method(value: object, name: str) -> None:
    if not callable(getattr(value, name, None)):
        raise ValueError(f"receiver production provider is missing {name}")


class GovernedReceiverRuntimeHandler(ReceiverRuntimeHandler):
    """Apply one shared audit/rate boundary to HTTP and forced-command calls."""

    def __init__(self, *, audit_sink: ReceiverAuditSink, rate_limiter: ReceiverRateLimiter, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._audit_sink = audit_sink
        self._rate_limiter = rate_limiter

    async def _govern(self, action: str, principal, correlation_id: str | None, operation):
        audit_correlation_id = correlation_id or "receiver-unknown"
        try:
            await self._rate_limiter.check(action=action, principal_subject=principal.subject)
        except Exception:
            from app.gateway.nexus_receiver.runtime import ReceiverRuntimeError

            raise ReceiverRuntimeError(code="RATE_LIMITED", title="Rate limited", detail="The receiver request rate limit was exceeded.", status_code=429, retryable=True) from None
        try:
            await self._audit_sink.record(action=action, principal_subject=principal.subject, correlation_id=audit_correlation_id, outcome="attempt")
        except Exception:
            from app.gateway.nexus_receiver.runtime import ReceiverRuntimeError

            raise ReceiverRuntimeError(code="RECEIVER_NOT_READY", detail="The receiver audit service is unavailable.", status_code=503, retryable=True) from None
        try:
            result = await operation()
        except Exception:
            try:
                await self._audit_sink.record(action=action, principal_subject=principal.subject, correlation_id=audit_correlation_id, outcome="denied")
            except Exception:
                from app.gateway.nexus_receiver.runtime import ReceiverRuntimeError

                raise ReceiverRuntimeError(code="RECEIVER_NOT_READY", detail="The receiver audit service is unavailable.", status_code=503, retryable=True) from None
            raise
        try:
            await self._audit_sink.record(action=action, principal_subject=principal.subject, correlation_id=audit_correlation_id, outcome="succeeded")
        except Exception:
            from app.gateway.nexus_receiver.runtime import ReceiverRuntimeError

            raise ReceiverRuntimeError(code="RECEIVER_NOT_READY", detail="The receiver audit service is unavailable.", status_code=503, retryable=True) from None
        return result

    async def get_capabilities(self, *, principal, correlation_id=None):
        return await self._govern("capabilities.get", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).get_capabilities(principal=principal))

    async def list_users(self, *, principal, query, cursor, limit, correlation_id=None):
        return await self._govern("users.list", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).list_users(principal=principal, query=query, cursor=cursor, limit=limit))

    async def list_skills(self, *, principal, request_payload, correlation_id=None):
        return await self._govern("skills.list", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).list_skills(principal=principal, request_payload=request_payload))

    async def submit_install(self, *, principal, correlation_id=None, **kwargs):
        return await self._govern("install.submit", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).submit_install(principal=principal, **kwargs))

    async def get_operation(self, *, principal, operation_id, correlation_id=None):
        return await self._govern("operations.get", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).get_operation(principal=principal, operation_id=operation_id))

    async def query_observation(self, *, principal, query_payload, correlation_id=None):
        return await self._govern("observations.query", principal, correlation_id, lambda: super(GovernedReceiverRuntimeHandler, self).query_observation(principal=principal, query_payload=query_payload))

    async def recover_pending(self):
        principal_subject = "deerflow.receiver.recovery"
        try:
            await self._audit_sink.record(action="operations.recover", principal_subject=principal_subject, correlation_id="receiver-recovery", outcome="attempt")
            result = await super().recover_pending()
            await self._audit_sink.record(action="operations.recover", principal_subject=principal_subject, correlation_id="receiver-recovery", outcome="succeeded")
            return result
        except Exception:
            try:
                await self._audit_sink.record(action="operations.recover", principal_subject=principal_subject, correlation_id="receiver-recovery", outcome="denied")
            except Exception:
                pass
            raise


class ProductionReceiverReleaseBootstrap:
    def __init__(
        self,
        *,
        mounted_host_key_path: Path | None = None,
        mounted_principal_map_path: Path | None = None,
    ) -> None:
        self._mounted_host_key_path = mounted_host_key_path
        self._mounted_principal_map_path = mounted_principal_map_path

    @staticmethod
    def _require_mounted_secret(path: Path, expected: bytes, label: str) -> None:
        try:
            mounted = path.read_bytes()
        except OSError:
            raise ValueError(f"receiver mounted {label} Secret is unavailable") from None
        if mounted != expected:
            raise ValueError(f"receiver mounted {label} Secret does not match the provider")

    async def build(self, config: NexusReceiverConfig) -> ReceiverReleaseComponents:
        app_config = get_app_config()
        if app_config.database.backend != "postgres":
            raise ValueError("receiver production bootstrap requires PostgreSQL")
        from deerflow.persistence.engine import get_session_factory

        session_factory = get_session_factory()
        if session_factory is None:
            raise ValueError("receiver production bootstrap requires the SQL session factory")
        factory = load_production_provider_factory(config.provider_factory or "")
        bundle = factory(config)
        if inspect.isawaitable(bundle):
            bundle = await bundle
        if not isinstance(bundle, ProductionReceiverProviderBundle):
            raise ValueError("receiver production provider bundle is invalid")
        for provider, method in (
            (bundle.secret_resolver, "resolve"),
            (bundle.service_authenticator, "authenticate"),
            (bundle.directory, "resolve_install_target"),
            (bundle.directory, "search"),
            (bundle.target_authorizer, "authorize_user_target"),
            (bundle.package_policy, "validate"),
            (bundle.audit_sink, "record"),
            (bundle.rate_limiter, "check"),
            (bundle.shared_volume_probe, "verify"),
            (bundle.readiness_probe, "verify"),
        ):
            _require_method(provider, method)

        assert config.package_stage_path and config.global_storage_path and config.cursor_signing_key_secret_ref and config.principal_map_secret_ref
        package_stage_path = Path(config.package_stage_path)
        global_storage_path = Path(config.global_storage_path)
        ssh_profile = os.environ.get("NEXUS_RECEIVER_FORCED_COMMAND_PROFILE") == "production"
        await bundle.shared_volume_probe.verify(
            global_storage_path=global_storage_path,
            package_stage_path=package_stage_path,
            global_writable=not ssh_profile,
        )
        await bundle.readiness_probe.verify(config=config)
        cursor_key = await bundle.secret_resolver.resolve(config.cursor_signing_key_secret_ref)
        if len(cursor_key) < 32:
            raise ValueError("receiver cursor signing Secret is invalid")
        principal_map = await bundle.secret_resolver.resolve(config.principal_map_secret_ref)
        parse_receiver_principal_map(principal_map)
        if self._mounted_principal_map_path is not None:
            self._require_mounted_secret(self._mounted_principal_map_path, principal_map, "principal-map")
        assert config.host_key_secret_ref is not None
        host_key = await bundle.secret_resolver.resolve(config.host_key_secret_ref)
        if not host_key.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----") or len(host_key) < 64:
            raise ValueError("receiver SSH host-key Secret is invalid")
        if self._mounted_host_key_path is not None:
            self._require_mounted_secret(self._mounted_host_key_path, host_key, "host-key")

        from deerflow.config.paths import get_paths, make_safe_user_id
        from deerflow.skills.installer import _scan_static_skill_archive_or_raise
        from deerflow.skills.storage.global_managed_skill_storage import GlobalManagedSkillStorage
        from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage

        if global_storage_path.name != "skills" or global_storage_path.parent.name != "integrations":
            raise ValueError("global_storage_path must end in integrations/skills")
        if global_storage_path != get_paths().integration_skills_dir():
            raise ValueError("global_storage_path must match the runtime integration Skill path")
        base_dir = global_storage_path.parent.parent
        global_storage = GlobalManagedSkillStorage(base_dir=base_dir, app_config=app_config)
        if global_storage.provider_root != global_storage_path / "nexus":
            raise ValueError("receiver GLOBAL storage path does not match the native layout")
        catalog = SqlReceiverCoordinationStore(session_factory)
        global_installer = GlobalScopedReceiverInstaller(
            storage=global_storage,
            catalog=catalog,
            committer=GlobalManagedSkillCommitter(storage=global_storage, catalog=catalog),
            precommit_scan=_scan_static_skill_archive_or_raise,
        )

        def storage_factory(user_id: str) -> UserScopedSkillStorage:
            return UserScopedSkillStorage(make_safe_user_id(user_id), app_config=app_config)

        handler = GovernedReceiverRuntimeHandler(
            store=SqlReceiverOperationStore(session_factory),
            package_store=LocalReceiverPackageStore(package_stage_path),
            directory=bundle.directory,
            user_directory=bundle.directory,
            installer=UserScopedReceiverInstaller(precommit_scan=_scan_static_skill_archive_or_raise),
            skill_inventory=UserScopedReceiverSkillInventory(storage_factory),
            target_authorizer=bundle.target_authorizer,
            catalog_revision_provider=catalog.get_catalog_revision,
            skill_cursor_codec=HmacReceiverSkillCursorCodec(cursor_key),
            global_installer=global_installer,
            package_policy=bundle.package_policy,
            user_install_ready=True,
            capability_readiness=ReceiverCapabilityReadiness(
                scope_rbac=True,
                durable_operations=True,
                native_global_storage=True,
                load_probe=True,
                runtime_revision_consumer=True,
                recovery_fencing=True,
                service_authentication=True,
                directory_privacy=True,
                package_trust=True,
                compatibility=True,
                shared_volume_topology=True,
            ),
            audit_sink=bundle.audit_sink,
            rate_limiter=bundle.rate_limiter,
        )
        principal_mapper: ReceiverPrincipalMapper = SecretBackedReceiverPrincipalMapper(
            resolver=bundle.secret_resolver,
            reference=config.principal_map_secret_ref,
        )
        return ReceiverReleaseComponents(
            runtime_handler=handler,
            service_authenticator=bundle.service_authenticator,
            principal_mapper=principal_mapper,
            recovery_coordinator=catalog,
        )
