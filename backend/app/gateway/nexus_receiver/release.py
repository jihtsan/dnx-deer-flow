"""Fail-closed release wiring for receiver transports and recovery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.gateway.nexus_receiver.auth import ReceiverServiceAuthenticator
from app.gateway.nexus_receiver.runtime import ReceiverRuntimeHandler, ReceiverTransportPrincipal
from deerflow.config.nexus_receiver_config import NexusReceiverConfig, ReceiverSecretReference

logger = logging.getLogger(__name__)

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLIC_KEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh\.com) [A-Za-z0-9+/=]{8,8192}(?: [^\r\n]{1,200})?$")
_SSH_ACTIONS = frozenset(
    {
        "receiver:capabilities:read",
        "receiver:user-directory:read",
        "receiver:install:user",
        "receiver:observe:user",
        "receiver:operations:read",
    }
)
_RECOVERY_STOP_TIMEOUT_SECONDS = 10.0


class ReceiverSecretResolver(Protocol):
    """Deployment-owned Secret resolver; implementations must not log values."""

    async def resolve(self, reference: ReceiverSecretReference) -> bytes: ...


class ReceiverPrincipalMapper(Protocol):
    async def map_principal(self, key_id: str) -> ReceiverTransportPrincipal: ...


class _PrincipalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key_id: str = Field(alias="keyId")
    subject: str
    actions: list[str]
    public_key: str = Field(alias="publicKey")

    @model_validator(mode="after")
    def validate_entry(self):
        if _KEY_ID.fullmatch(self.key_id) is None:
            raise ValueError("principal key ID is invalid")
        if not self.actions or len(set(self.actions)) != len(self.actions) or set(self.actions) - _SSH_ACTIONS:
            raise ValueError("principal actions are invalid")
        if _PUBLIC_KEY.fullmatch(self.public_key) is None:
            raise ValueError("principal public key is invalid")
        ReceiverTransportPrincipal(
            subject=self.subject,
            profile="ssh_forced_command",
            actions=frozenset(self.actions),
        )
        return self


class ReceiverPrincipalMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    principals: list[_PrincipalEntry]

    @model_validator(mode="after")
    def validate_map(self):
        if not self.principals:
            raise ValueError("principal map version or entries are invalid")
        key_ids = [entry.key_id for entry in self.principals]
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("principal map contains duplicate key IDs")
        public_keys = [entry.public_key.split(" ", 2)[:2] for entry in self.principals]
        normalized_keys = [" ".join(parts) for parts in public_keys]
        if len(normalized_keys) != len(set(normalized_keys)):
            raise ValueError("principal map contains duplicate public keys")
        return self


def parse_receiver_principal_map(payload: bytes) -> ReceiverPrincipalMap:
    try:
        raw = json.loads(payload)
        return ReceiverPrincipalMap.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise ValueError("receiver principal map is invalid") from None


class SecretBackedReceiverPrincipalMapper:
    def __init__(self, *, resolver: ReceiverSecretResolver, reference: ReceiverSecretReference) -> None:
        self._resolver = resolver
        self._reference = reference

    async def map_principal(self, key_id: str) -> ReceiverTransportPrincipal:
        if _KEY_ID.fullmatch(key_id) is None:
            raise ValueError("receiver principal key ID is not authorized")
        try:
            payload = await self._resolver.resolve(self._reference)
        except Exception:
            raise ValueError("receiver principal map is unavailable") from None
        principal_map = parse_receiver_principal_map(payload)
        entry = next((candidate for candidate in principal_map.principals if candidate.key_id == key_id), None)
        if entry is None:
            raise ValueError("receiver principal key ID is not authorized")
        return ReceiverTransportPrincipal(
            subject=entry.subject,
            profile="ssh_forced_command",
            actions=frozenset(entry.actions),
        )


@dataclass(frozen=True, slots=True)
class ReceiverForcedCommandContext:
    handler: ReceiverRuntimeHandler
    principal: ReceiverTransportPrincipal


class ReceiverForcedCommandContextProvider(Protocol):
    async def get_context(self, key_id: str) -> ReceiverForcedCommandContext: ...


class MappedReceiverForcedCommandContextProvider:
    def __init__(self, *, handler: ReceiverRuntimeHandler, principal_mapper: ReceiverPrincipalMapper) -> None:
        self._handler = handler
        self._principal_mapper = principal_mapper

    async def get_context(self, key_id: str) -> ReceiverForcedCommandContext:
        return ReceiverForcedCommandContext(
            handler=self._handler,
            principal=await self._principal_mapper.map_principal(key_id),
        )


class _Recoverer(Protocol):
    async def recover_pending(self) -> list: ...


class ReceiverRecoveryService:
    """Own startup, submit-triggered and periodic durable recovery passes."""

    def __init__(self, recoverer: _Recoverer, *, poll_interval_seconds: float) -> None:
        self._recoverer = recoverer
        self._poll_interval_seconds = poll_interval_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        await self.run_now()
        self._task = asyncio.create_task(self._run(), name="nexus-receiver-recovery")

    def notify_pending(self) -> None:
        self._wake.set()

    async def run_now(self) -> None:
        async with self._run_lock:
            try:
                await self._recoverer.recover_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Nexus receiver recovery pass failed; the next scheduled pass will retry")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                pass
            self._wake.clear()
            if not self._stop.is_set():
                await self.run_now()

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        self._wake.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_RECOVERY_STOP_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Nexus receiver recovery shutdown exceeded %.1fs; cancelling it", _RECOVERY_STOP_TIMEOUT_SECONDS)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None


@dataclass(frozen=True, slots=True)
class ReceiverReleaseComponents:
    runtime_handler: ReceiverRuntimeHandler
    service_authenticator: ReceiverServiceAuthenticator
    principal_mapper: ReceiverPrincipalMapper


class ReceiverReleaseBootstrap(Protocol):
    """Deployment-owned composition root for approved receiver dependencies."""

    async def build(self, config: NexusReceiverConfig) -> ReceiverReleaseComponents: ...


async def start_receiver_release_wiring(app_state: object, config: NexusReceiverConfig) -> ReceiverRecoveryService | None:
    """Install the complete receiver bundle atomically, or leave every route denied."""
    existing_recovery = getattr(app_state, "nexus_receiver_recovery_service", None)
    if existing_recovery is not None:
        try:
            await existing_recovery.stop()
        except Exception:
            logger.exception("Failed to stop the previous Nexus receiver recovery service")
    for attribute in (
        "nexus_receiver_runtime_handler",
        "nexus_receiver_service_authenticator",
        "nexus_receiver_principal_mapper",
        "nexus_receiver_recovery_service",
    ):
        try:
            delattr(app_state, attribute)
        except AttributeError:
            pass
    if not config.enabled:
        return None
    bootstrap: ReceiverReleaseBootstrap | None = getattr(app_state, "nexus_receiver_release_bootstrap", None)
    if bootstrap is None:
        logger.warning("Nexus receiver is configured but no approved release bootstrap is injected; remaining default-deny")
        return None
    try:
        components = await bootstrap.build(config)
        if not callable(getattr(components.runtime_handler, "recover_pending", None)):
            raise TypeError("receiver runtime handler is incomplete")
        if not callable(getattr(components.service_authenticator, "authenticate", None)):
            raise TypeError("receiver service authenticator is incomplete")
        if not callable(getattr(components.principal_mapper, "map_principal", None)):
            raise TypeError("receiver principal mapper is incomplete")
        recovery = ReceiverRecoveryService(
            components.runtime_handler,
            poll_interval_seconds=config.recovery_poll_interval_seconds,
        )
        components.runtime_handler.set_pending_recovery_notifier(recovery.notify_pending)
        await recovery.start()
    except Exception:
        logger.exception("Nexus receiver release bootstrap is incomplete; remaining default-deny")
        return None
    setattr(app_state, "nexus_receiver_runtime_handler", components.runtime_handler)
    setattr(app_state, "nexus_receiver_service_authenticator", components.service_authenticator)
    setattr(app_state, "nexus_receiver_principal_mapper", components.principal_mapper)
    setattr(app_state, "nexus_receiver_recovery_service", recovery)
    return recovery
