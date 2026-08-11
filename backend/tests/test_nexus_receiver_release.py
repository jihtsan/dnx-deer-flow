from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.gateway.nexus_receiver.release import (
    MappedReceiverForcedCommandContextProvider,
    ReceiverRecoveryService,
    ReceiverReleaseComponents,
    SecretBackedReceiverPrincipalMapper,
    UnixDatagramReceiverRecoveryNotifier,
    build_receiver_forced_command_context_provider,
    start_receiver_release_wiring,
)
from app.gateway.nexus_receiver.runtime import ReceiverTransportPrincipal
from deerflow.config.nexus_receiver_config import NexusReceiverConfig, ReceiverSecretReference

_COMPLETE_CONFIG = {
    "enabled": True,
    "ssh_account": "nexus-receiver",
    "recovery_signal_socket": "/run/nexus-receiver-ipc/recovery.sock",
    "host_key_secret_ref": {"name": "deerflow-receiver-host-key", "key": "ssh_host_ed25519_key"},
    "principal_map_secret_ref": {"name": "deerflow-receiver-principals", "key": "principals.json"},
    "directory_policy_revision": "directory-policy-v1",
    "trust_policy_revision": "trust-policy-v1",
    "compatibility_policy_revision": "compatibility-policy-v1",
}


def test_receiver_config_is_disabled_by_default() -> None:
    config = NexusReceiverConfig()

    assert config.enabled is False
    assert config.host_key_secret_ref is None
    assert config.principal_map_secret_ref is None


@pytest.mark.parametrize(
    "missing",
    [
        "ssh_account",
        "recovery_signal_socket",
        "host_key_secret_ref",
        "principal_map_secret_ref",
        "directory_policy_revision",
        "trust_policy_revision",
        "compatibility_policy_revision",
    ],
)
def test_receiver_config_rejects_enabled_with_an_open_release_gate(missing: str) -> None:
    values = dict(_COMPLETE_CONFIG)
    values.pop(missing)

    with pytest.raises(ValidationError, match=missing):
        NexusReceiverConfig.model_validate(values)


def test_receiver_config_contains_references_not_secret_values() -> None:
    config = NexusReceiverConfig.model_validate(_COMPLETE_CONFIG)

    assert config.host_key_secret_ref == ReceiverSecretReference(name="deerflow-receiver-host-key", key="ssh_host_ed25519_key")
    assert "secret_value" not in config.model_dump_json()


def test_receiver_config_account_must_match_the_dedicated_image_account() -> None:
    values = dict(_COMPLETE_CONFIG)
    values["ssh_account"] = "another-account"

    with pytest.raises(ValidationError, match="nexus-receiver"):
        NexusReceiverConfig.model_validate(values)


class _SecretResolver:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def resolve(self, reference: ReceiverSecretReference) -> bytes:
        del reference
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload).encode()


def _principal_map(*, principals: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "principals": principals
        or [
            {
                "keyId": "nexus-release-key-1",
                "subject": "nexus.release.prod",
                "actions": ["receiver:capabilities:read", "receiver:install:user"],
                "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC7g6Ygddummyreleasekey nexus",
            }
        ],
    }


@pytest.mark.asyncio
async def test_secret_backed_principal_mapper_maps_only_exact_key_ids_and_actions() -> None:
    mapper = SecretBackedReceiverPrincipalMapper(
        resolver=_SecretResolver(_principal_map()),
        reference=ReceiverSecretReference(name="principals", key="map"),
    )

    principal = await mapper.map_principal("nexus-release-key-1")

    assert principal == ReceiverTransportPrincipal(
        subject="nexus.release.prod",
        profile="ssh_forced_command",
        actions=frozenset({"receiver:capabilities:read", "receiver:install:user"}),
    )
    with pytest.raises(ValueError, match="not authorized"):
        await mapper.map_principal("unknown-key")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principals",
    [
        [
            {
                "keyId": "same-key",
                "subject": "nexus.release.one",
                "actions": ["receiver:capabilities:read"],
                "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOne",
            },
            {
                "keyId": "same-key",
                "subject": "nexus.release.two",
                "actions": ["receiver:capabilities:read"],
                "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITwo",
            },
        ],
        [
            {
                "keyId": "unsafe\nkey",
                "subject": "nexus.release.one",
                "actions": ["receiver:capabilities:read"],
                "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOne",
            }
        ],
        [
            {
                "keyId": "safe-key",
                "subject": "nexus.release.one",
                "actions": ["receiver:unknown"],
                "publicKey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOne",
            }
        ],
    ],
)
async def test_secret_backed_principal_mapper_rejects_malformed_or_ambiguous_maps(principals: list[dict]) -> None:
    mapper = SecretBackedReceiverPrincipalMapper(
        resolver=_SecretResolver(_principal_map(principals=principals)),
        reference=ReceiverSecretReference(name="principals", key="map"),
    )

    with pytest.raises(ValueError, match="principal map"):
        await mapper.map_principal("safe-key")


@pytest.mark.asyncio
async def test_secret_resolver_failure_does_not_expose_provider_detail() -> None:
    mapper = SecretBackedReceiverPrincipalMapper(
        resolver=_SecretResolver(ValueError("raw-secret-provider-detail")),
        reference=ReceiverSecretReference(name="principals", key="map"),
    )

    with pytest.raises(ValueError, match="principal map is unavailable") as caught:
        await mapper.map_principal("safe-key")

    assert "raw-secret-provider-detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_forced_command_context_maps_the_transport_principal() -> None:
    handler = object()
    mapper = SecretBackedReceiverPrincipalMapper(
        resolver=_SecretResolver(_principal_map()),
        reference=ReceiverSecretReference(name="principals", key="map"),
    )
    provider = MappedReceiverForcedCommandContextProvider(handler=handler, principal_mapper=mapper)

    context = await provider.get_context("nexus-release-key-1")

    assert context.handler is handler
    assert context.principal.subject == "nexus.release.prod"


@pytest.mark.asyncio
async def test_forced_command_release_factory_wires_secret_mapping_and_cross_process_wakeup() -> None:
    callbacks: list[object] = []

    class _Runtime:
        def set_pending_recovery_notifier(self, callback) -> None:
            callbacks.append(callback)

    runtime = _Runtime()
    provider = build_receiver_forced_command_context_provider(
        config=NexusReceiverConfig.model_validate(_COMPLETE_CONFIG),
        handler=runtime,
        secret_resolver=_SecretResolver(_principal_map()),
    )

    context = await provider.get_context("nexus-release-key-1")

    assert context.handler is runtime
    assert context.principal.subject == "nexus.release.prod"
    assert len(callbacks) == 1
    provider.close()


class _Recoverer:
    def __init__(self) -> None:
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.called = asyncio.Event()

    async def recover_pending(self) -> list:
        self.calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.concurrent, self.max_concurrent)
        self.called.set()
        await asyncio.sleep(0.01)
        self.concurrent -= 1
        return []


@pytest.mark.asyncio
async def test_recovery_service_runs_at_startup_after_submit_and_periodically() -> None:
    recoverer = _Recoverer()
    service = ReceiverRecoveryService(recoverer, poll_interval_seconds=0.03)

    await service.start()
    assert recoverer.calls == 1

    recoverer.called.clear()
    service.notify_pending()
    await asyncio.wait_for(recoverer.called.wait(), timeout=0.2)
    assert recoverer.calls >= 2

    recoverer.called.clear()
    await asyncio.wait_for(recoverer.called.wait(), timeout=0.2)
    assert recoverer.calls >= 3

    await service.stop()


@pytest.mark.asyncio
async def test_recovery_service_accepts_cross_process_submit_signal() -> None:
    recoverer = _Recoverer()
    signal_path = Path("/tmp") / f"nexus-receiver-{uuid4().hex}.sock"
    service = ReceiverRecoveryService(
        recoverer,
        poll_interval_seconds=60,
        signal_path=signal_path,
    )
    await service.start()
    recoverer.called.clear()

    notifier = UnixDatagramReceiverRecoveryNotifier(signal_path)
    notifier.notify_pending()
    notifier.close()

    await asyncio.wait_for(recoverer.called.wait(), timeout=0.2)
    assert recoverer.calls == 2
    await service.stop()
    assert not signal_path.exists()


@pytest.mark.asyncio
async def test_recovery_service_is_single_flight_and_stops_cleanly() -> None:
    recoverer = _Recoverer()
    service = ReceiverRecoveryService(recoverer, poll_interval_seconds=60)
    await service.start()

    await asyncio.gather(service.run_now(), service.run_now(), service.run_now())
    await service.stop()

    assert recoverer.max_concurrent == 1
    assert service.running is False


@pytest.mark.asyncio
async def test_recovery_service_shutdown_is_bounded(monkeypatch) -> None:
    import app.gateway.nexus_receiver.release as release_module

    entered = asyncio.Event()

    class _BlockedRecoverer:
        def __init__(self) -> None:
            self.calls = 0

        async def recover_pending(self) -> list:
            self.calls += 1
            if self.calls == 1:
                return []
            entered.set()
            await asyncio.Event().wait()
            return []

    monkeypatch.setattr(release_module, "_RECOVERY_STOP_TIMEOUT_SECONDS", 0.01)
    service = ReceiverRecoveryService(_BlockedRecoverer(), poll_interval_seconds=60)
    await service.start()
    service.notify_pending()
    await entered.wait()

    await asyncio.wait_for(service.stop(), timeout=0.1)

    assert service.running is False


class _Bootstrap:
    def __init__(self, components: ReceiverReleaseComponents) -> None:
        self.components = components

    async def build(self, config: NexusReceiverConfig) -> ReceiverReleaseComponents:
        assert config.enabled
        return self.components


@pytest.mark.asyncio
async def test_release_wiring_remains_default_deny_without_injected_bootstrap() -> None:
    state = SimpleNamespace()

    service = await start_receiver_release_wiring(state, NexusReceiverConfig.model_validate(_COMPLETE_CONFIG))

    assert service is None
    assert not hasattr(state, "nexus_receiver_runtime_handler")
    assert not hasattr(state, "nexus_receiver_service_authenticator")
    assert not hasattr(state, "nexus_receiver_principal_mapper")


@pytest.mark.asyncio
async def test_disabled_release_wiring_clears_previous_runtime_state() -> None:
    class _ExistingRecovery:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    recovery = _ExistingRecovery()
    state = SimpleNamespace(
        nexus_receiver_runtime_handler=object(),
        nexus_receiver_service_authenticator=object(),
        nexus_receiver_principal_mapper=object(),
        nexus_receiver_recovery_service=recovery,
    )

    service = await start_receiver_release_wiring(state, NexusReceiverConfig())

    assert service is None
    assert recovery.stopped is True
    assert not hasattr(state, "nexus_receiver_runtime_handler")
    assert not hasattr(state, "nexus_receiver_service_authenticator")
    assert not hasattr(state, "nexus_receiver_principal_mapper")


@pytest.mark.asyncio
async def test_release_wiring_injects_complete_components_and_recovery_together() -> None:
    recoverer = _Recoverer()
    notifier: list[object] = []

    class _Runtime:
        recover_pending = recoverer.recover_pending

        def set_pending_recovery_notifier(self, callback) -> None:
            notifier.append(callback)

    runtime = _Runtime()

    class _Authenticator:
        async def authenticate(self, request):
            del request

    class _Mapper:
        async def map_principal(self, key_id):
            del key_id

    authenticator = _Authenticator()
    mapper = _Mapper()
    state = SimpleNamespace(
        nexus_receiver_release_bootstrap=_Bootstrap(
            ReceiverReleaseComponents(
                runtime_handler=runtime,
                service_authenticator=authenticator,
                principal_mapper=mapper,
            )
        )
    )

    config_values = dict(_COMPLETE_CONFIG)
    config_values["recovery_signal_socket"] = str(Path("/tmp") / f"nexus-receiver-{uuid4().hex}.sock")
    service = await start_receiver_release_wiring(state, NexusReceiverConfig.model_validate(config_values))

    assert service is not None
    assert state.nexus_receiver_runtime_handler is runtime
    assert state.nexus_receiver_service_authenticator is authenticator
    assert state.nexus_receiver_principal_mapper is mapper
    assert notifier == [service.notify_pending]
    await service.stop()
