from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.gateway.nexus_receiver.package_store import InMemoryReceiverPackageStore
from app.gateway.nexus_receiver.production import (
    GovernedReceiverRuntimeHandler,
    ProductionReceiverReleaseBootstrap,
    load_production_provider_factory,
)
from app.gateway.nexus_receiver.router import _correlation_id
from app.gateway.nexus_receiver.runtime import InMemoryReceiverOperationStore, ReceiverRuntimeError
from deerflow.config.nexus_receiver_config import NexusReceiverConfig


def _config(**overrides):
    values = {
        "enabled": True,
        "production": True,
        "ssh_account": "nexus-receiver",
        "recovery_signal_socket": "/run/nexus-receiver-ipc/recovery.sock",
        "host_key_secret_ref": {"name": "receiver/ssh", "key": "host-key"},
        "principal_map_secret_ref": {"name": "receiver/ssh", "key": "principal-map"},
        "cursor_signing_key_secret_ref": {"name": "receiver/runtime", "key": "cursor-key"},
        "provider_factory": "enterprise.receiver:build_providers",
        "package_stage_path": "/var/lib/deer-flow/nexus-receiver/packages",
        "global_storage_path": "/var/lib/deer-flow/global-skills",
        "directory_policy_revision": "directory-v7",
        "trust_policy_revision": "trust-v9",
        "compatibility_policy_revision": "compat-v4",
        "audit_policy_revision": "audit-v3",
        "rate_limit_policy_revision": "rate-v2",
    }
    values.update(overrides)
    return NexusReceiverConfig.model_validate(values)


@pytest.mark.parametrize(
    "missing",
    [
        "cursor_signing_key_secret_ref",
        "provider_factory",
        "package_stage_path",
        "global_storage_path",
        "audit_policy_revision",
        "rate_limit_policy_revision",
    ],
)
def test_enabled_production_config_requires_every_external_gate(missing: str) -> None:
    values = _config().model_dump()
    values.pop(missing)

    with pytest.raises(ValidationError, match=missing):
        NexusReceiverConfig.model_validate(values)


def test_production_paths_must_be_absolute_and_separate() -> None:
    with pytest.raises(ValidationError, match="package_stage_path"):
        _config(package_stage_path="relative/packages")
    with pytest.raises(ValidationError, match="must be different"):
        _config(package_stage_path="/shared", global_storage_path="/shared")


def test_provider_factory_load_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.gateway.nexus_receiver.production.import_module", lambda _name: object())

    with pytest.raises(ValueError, match="provider factory is unavailable"):
        load_production_provider_factory("enterprise.receiver:build_providers")


def test_generated_http_correlation_id_is_stable_for_the_request() -> None:
    request = SimpleNamespace(state=SimpleNamespace(), headers={})

    first = _correlation_id(request)

    assert first.startswith("receiver-")
    assert _correlation_id(request) == first


def test_sshd_preflight_requires_mounted_secrets_to_match_provider(tmp_path: Path) -> None:
    mounted = tmp_path / "mounted-secret"
    mounted.write_bytes(b"mounted")

    ProductionReceiverReleaseBootstrap._require_mounted_secret(mounted, b"mounted", "host-key")
    with pytest.raises(ValueError, match="does not match"):
        ProductionReceiverReleaseBootstrap._require_mounted_secret(mounted, b"provider", "host-key")
    with pytest.raises(ValueError, match="is unavailable"):
        ProductionReceiverReleaseBootstrap._require_mounted_secret(tmp_path / "missing", b"provider", "host-key")


class _Policy:
    async def validate(self, **kwargs) -> None:
        del kwargs


class _Audit:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.events: list[tuple[str, str, str]] = []

    async def record(self, *, action: str, principal_subject: str, correlation_id: str, outcome: str) -> None:
        del principal_subject
        if self.failure is not None:
            raise self.failure
        self.events.append((action, correlation_id, outcome))


class _RateLimiter:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def check(self, **kwargs) -> None:
        del kwargs
        if self.failure is not None:
            raise self.failure


def _governed_handler(*, audit: _Audit, limiter: _RateLimiter) -> GovernedReceiverRuntimeHandler:
    return GovernedReceiverRuntimeHandler(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=SimpleNamespace(),
        installer=SimpleNamespace(),
        package_policy=_Policy(),
        audit_sink=audit,
        rate_limiter=limiter,
    )


@pytest.mark.asyncio
async def test_rate_limit_and_audit_fail_closed_before_action() -> None:
    principal = SimpleNamespace(subject="nexus.production", actions=frozenset({"receiver:capabilities:read"}), profile="mtls")
    with pytest.raises(ReceiverRuntimeError) as rate_error:
        await _governed_handler(audit=_Audit(), limiter=_RateLimiter(RuntimeError("backend detail"))).get_capabilities(principal=principal)
    assert rate_error.value.status_code == 429
    assert rate_error.value.code == "RATE_LIMITED"
    assert "backend detail" not in rate_error.value.detail

    with pytest.raises(ReceiverRuntimeError) as audit_error:
        await _governed_handler(audit=_Audit(RuntimeError("audit detail")), limiter=_RateLimiter()).get_capabilities(principal=principal)
    assert audit_error.value.status_code == 503
    assert audit_error.value.code == "RECEIVER_NOT_READY"
    assert "audit detail" not in audit_error.value.detail


@pytest.mark.asyncio
async def test_governed_action_records_minimal_outcomes() -> None:
    audit = _Audit()
    principal = SimpleNamespace(subject="nexus.production", actions=frozenset({"receiver:capabilities:read"}), profile="mtls")

    result = await _governed_handler(audit=audit, limiter=_RateLimiter()).get_capabilities(principal=principal, correlation_id="receiver-prod-17")

    assert result.access_mode == "read_only"
    assert audit.events == [("capabilities.get", "receiver-prod-17", "attempt"), ("capabilities.get", "receiver-prod-17", "succeeded")]


def test_production_docs_do_not_present_fixture_as_acceptance() -> None:
    root = Path(__file__).resolve().parents[2]
    guide = (root / "backend" / "docs" / "NEXUS_RECEIVER_PRODUCTION.md").read_text(encoding="utf-8")

    assert "postgres:17.5" in guide
    assert "production provider bundle" in guide
    assert "fixture" in guide.casefold()
    assert "does not" in guide.casefold()
