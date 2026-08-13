from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.nexus_receiver import install_nexus_receiver_provider
from app.gateway.nexus_receiver.auth import ReceiverProviderError, ReceiverServicePrincipal
from app.gateway.nexus_receiver.installer import GlobalScopedReceiverInstaller
from app.gateway.nexus_receiver.models import ReceiverSkillListItem
from app.gateway.nexus_receiver.package_store import InMemoryReceiverPackageStore
from app.gateway.nexus_receiver.runtime import (
    InMemoryReceiverOperationStore,
    ReceiverCapabilityReadiness,
    ReceiverRuntimeHandler,
    ReceiverTargetUser,
    ReceiverTransportPrincipal,
)
from app.gateway.nexus_receiver.skill_inventory import HmacReceiverSkillCursorCodec, ReceiverSkillCursor, UserScopedReceiverSkillInventory
from app.gateway.nexus_receiver.ssh import dispatch_forced_command

NOW = datetime(2026, 8, 13, 4, 1, tzinfo=UTC)


class _Directory:
    def __init__(self, users: set[str]) -> None:
        self.users = users
        self.calls: list[str] = []

    async def resolve_install_target(self, user_id: str) -> ReceiverTargetUser | None:
        self.calls.append(user_id)
        if user_id not in self.users:
            return None
        return ReceiverTargetUser(user_id=user_id, active=True, install_eligible=True)


class _InventoryStorage:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def list_receiver_inventory(self) -> list[dict]:
        return list(self.rows)


class _NoopInstaller:
    async def observe_user_skill(self, **kwargs):
        return None


class _Entitlements:
    def __init__(self, allowed: set[tuple[str, str]]) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    async def authorize_user_target(self, *, principal, user_id: str) -> bool:
        key = (principal.subject, user_id)
        self.calls.append(key)
        return key in self.allowed


def _principal(*actions: str) -> ReceiverServicePrincipal:
    return ReceiverServicePrincipal(
        subject="nexus-runtime-test",
        profile="oauth2_client_credentials",
        actions=frozenset(actions),  # type: ignore[arg-type]
    )


def _row(name: str, marker: str, *, enabled: bool = True, freshness: str = "current") -> dict:
    return {
        "runtimeSkillName": name,
        "skillVersionId": f"sv.{name}.1.0.0",
        "version": "1.0.0",
        "packageDigest": "sha256:" + marker * 64,
        "enabled": enabled,
        "loadState": "unknown" if freshness == "unavailable" else ("loaded" if enabled else "disabled"),
        "freshness": freshness,
        "observedAt": NOW,
    }


def _handler(*, directory, inventory, entitlements, revision) -> ReceiverRuntimeHandler:
    return ReceiverRuntimeHandler(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=directory,
        installer=_NoopInstaller(),
        skill_inventory=inventory,
        target_authorizer=entitlements,
        catalog_revision_provider=revision,
        skill_cursor_codec=HmacReceiverSkillCursorCodec(
            b"receiver-stage-three-test-key-material",
            clock=lambda: NOW,
            ttl=timedelta(minutes=10),
        ),
        clock=lambda: NOW,
    )


def _global_readiness() -> ReceiverCapabilityReadiness:
    return ReceiverCapabilityReadiness(
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
    )


@pytest.mark.asyncio
async def test_skills_list_is_user_isolated_and_uses_stable_revision_cursor() -> None:
    directory = _Directory({"user-a", "user-b"})
    storages = {
        "user-a": _InventoryStorage([_row("beta", "b", enabled=False), _row("alpha", "a")]),
        "user-b": _InventoryStorage([_row("gamma", "c")]),
    }
    inventory = UserScopedReceiverSkillInventory(lambda user_id: storages[user_id])
    entitlements = _Entitlements({("nexus-runtime-test", "user-a"), ("nexus-runtime-test", "user-b")})
    revision = {"value": 17}
    handler = _handler(directory=directory, inventory=inventory, entitlements=entitlements, revision=lambda: revision["value"])
    principal = _principal("receiver:skills:list:user")

    first = await handler.list_skills(
        principal=principal,
        request_payload={
            "contractVersion": "1.1.0",
            "correlationId": "skills-u-a-1",
            "target": {"scope": "USER", "deerFlowUserId": "user-a"},
            "limit": 1,
        },
    )
    second = await handler.list_skills(
        principal=principal,
        request_payload={
            "contractVersion": "1.1.0",
            "correlationId": "skills-u-a-2",
            "target": {"scope": "USER", "deerFlowUserId": "user-a"},
            "limit": 1,
            "cursor": first.page.next_cursor,
        },
    )
    isolated = await handler.list_skills(
        principal=principal,
        request_payload={
            "contractVersion": "1.1.0",
            "correlationId": "skills-u-b-1",
            "target": {"scope": "USER", "deerFlowUserId": "user-b"},
            "limit": 25,
        },
    )

    assert [item.runtime_skill_name for item in first.items] == ["alpha"]
    assert [item.runtime_skill_name for item in second.items] == ["beta"]
    assert [item.runtime_skill_name for item in isolated.items] == ["gamma"]
    assert set(first.items[0].model_dump(by_alias=True)) == {
        "runtimeSkillName",
        "skillVersionId",
        "version",
        "packageDigest",
        "enabled",
        "loadState",
        "freshness",
        "observedAt",
    }


@pytest.mark.asyncio
async def test_skills_list_forced_command_requires_1_1_and_shared_runtime() -> None:
    handler = _handler(
        directory=_Directory({"user-a"}),
        inventory=UserScopedReceiverSkillInventory(lambda _: _InventoryStorage([_row("alpha", "a")])),
        entitlements=_Entitlements({("nexus-runtime-test", "user-a")}),
        revision=lambda: 1,
    )
    frame = {
        "contractVersion": "1.1.0",
        "correlationId": "skills-ssh-1",
        "target": {"scope": "USER", "deerFlowUserId": "user-a"},
        "limit": 25,
    }
    payload = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    result = await dispatch_forced_command(
        "nexus-skill-receiver-v1 skills.list",
        payload,
        handler=handler,
        principal=ReceiverTransportPrincipal(
            subject="nexus-runtime-test",
            profile="ssh_forced_command",
            actions=frozenset({"receiver:skills:list:user"}),
        ),
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["items"][0]["runtimeSkillName"] == "alpha"


def test_skills_list_http_route_uses_service_auth_and_shared_runtime() -> None:
    handler = _handler(
        directory=_Directory({"user-a"}),
        inventory=UserScopedReceiverSkillInventory(lambda _: _InventoryStorage([_row("alpha", "a")])),
        entitlements=_Entitlements({("nexus-runtime-test", "user-a")}),
        revision=lambda: 1,
    )

    class _Authenticator:
        async def authenticate(self, request):
            del request
            return _principal("receiver:skills:list:user")

    app = FastAPI()
    app.state.nexus_receiver_runtime_handler = handler
    app.state.nexus_receiver_service_authenticator = _Authenticator()
    install_nexus_receiver_provider(app)

    response = TestClient(app).post(
        "/api/v1/nexus/skill-receiver/skills/query",
        headers={"Authorization": "Bearer service", "X-Correlation-ID": "skills-http-1"},
        json={
            "contractVersion": "1.1.0",
            "correlationId": "skills-http-1",
            "target": {"scope": "USER", "deerFlowUserId": "user-a"},
            "limit": 25,
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "skills-http-1"
    assert response.json()["items"][0]["runtimeSkillName"] == "alpha"


@pytest.mark.asyncio
async def test_skills_list_authorizes_target_before_directory_resolution_and_is_non_enumerating() -> None:
    directory = _Directory({"existing"})
    entitlement = _Entitlements(set())
    handler = _handler(
        directory=directory,
        inventory=UserScopedReceiverSkillInventory(lambda _: _InventoryStorage([])),
        entitlements=entitlement,
        revision=lambda: 1,
    )
    principal = _principal("receiver:skills:list:user")

    for user_id in ("existing", "missing"):
        with pytest.raises(ReceiverProviderError) as raised:
            await handler.list_skills(
                principal=principal,
                request_payload={
                    "contractVersion": "1.1.0",
                    "correlationId": f"skills-{user_id}",
                    "target": {"scope": "USER", "deerFlowUserId": user_id},
                    "limit": 25,
                },
            )
        assert raised.value.code == "FORBIDDEN"
    assert directory.calls == []


@pytest.mark.asyncio
async def test_skills_list_cursor_rejects_tamper_cross_target_query_and_revision_drift() -> None:
    directory = _Directory({"user-a", "user-b"})
    inventory = UserScopedReceiverSkillInventory(lambda _: _InventoryStorage([_row("alpha", "a"), _row("beta", "b")]))
    entitlements = _Entitlements({("nexus-runtime-test", "user-a"), ("nexus-runtime-test", "user-b")})
    revision = {"value": 4}
    handler = _handler(directory=directory, inventory=inventory, entitlements=entitlements, revision=lambda: revision["value"])
    principal = _principal("receiver:skills:list:user")
    request = {
        "contractVersion": "1.1.0",
        "correlationId": "skills-page-1",
        "target": {"scope": "USER", "deerFlowUserId": "user-a"},
        "limit": 1,
    }
    page = await handler.list_skills(principal=principal, request_payload=request)
    assert page.page.next_cursor is not None

    cases = [
        ({**request, "cursor": page.page.next_cursor[:-1] + "x"}, "CURSOR_INVALID"),
        ({**request, "target": {"scope": "USER", "deerFlowUserId": "user-b"}, "cursor": page.page.next_cursor}, "CURSOR_INVALID"),
        ({**request, "query": "alpha", "cursor": page.page.next_cursor}, "CURSOR_INVALID"),
    ]
    for payload, code in cases:
        with pytest.raises(ReceiverProviderError) as raised:
            await handler.list_skills(principal=principal, request_payload=payload)
        assert raised.value.code == code

    revision["value"] = 5
    with pytest.raises(ReceiverProviderError) as raised:
        await handler.list_skills(principal=principal, request_payload={**request, "cursor": page.page.next_cursor})
    assert raised.value.code == "CURSOR_EXPIRED"


def test_skills_list_cursor_stays_within_contract_bound_at_maximum_inputs() -> None:
    codec = HmacReceiverSkillCursorCodec(
        b"receiver-stage-three-test-key-material",
        clock=lambda: NOW,
    )
    cursor = codec.encode(
        ReceiverSkillCursor(
            principal_subject="p" * 200,
            user_id="u" * 200,
            normalized_query="q" * 100,
            catalog_revision="r" * 256,
            last_sort_key="s" * 128,
        )
    )

    assert len(cursor) <= 500
    assert codec.decode(cursor).last_sort_key == "s" * 128


@pytest.mark.asyncio
async def test_skills_list_distinguishes_exact_conflict_absent_and_unavailable() -> None:
    inventory = UserScopedReceiverSkillInventory(
        lambda _: _InventoryStorage(
            [
                _row("exact", "a"),
                _row("conflict", "b"),
                _row("unknown", "c", freshness="unavailable"),
            ]
        )
    )
    handler = _handler(
        directory=_Directory({"user-a"}),
        inventory=inventory,
        entitlements=_Entitlements({("nexus-runtime-test", "user-a")}),
        revision=lambda: 9,
    )
    principal = _principal("receiver:skills:list:user")

    async def query(name: str) -> list[ReceiverSkillListItem]:
        page = await handler.list_skills(
            principal=principal,
            request_payload={
                "contractVersion": "1.1.0",
                "correlationId": f"skills-{name}",
                "target": {"scope": "USER", "deerFlowUserId": "user-a"},
                "query": name,
                "limit": 25,
            },
        )
        return page.items

    assert (await query("exact"))[0].package_digest == "sha256:" + "a" * 64
    assert (await query("conflict"))[0].package_digest == "sha256:" + "b" * 64
    assert await query("absent") == []
    unavailable = (await query("unknown"))[0]
    assert unavailable.freshness == "unavailable" and unavailable.load_state == "unknown"


class _GlobalStorage:
    def __init__(self) -> None:
        self.metadata: dict[str, dict] = {}

    def receiver_metadata(self, name: str):
        return self.metadata.get(name)

    def load_probe(self, name: str):
        return type("Probe", (), {"name": name})() if name in self.metadata else None


class _GlobalCatalog:
    def __init__(self) -> None:
        self.entries: dict[str, object] = {}

    async def get_global_skill(self, name: str):
        return self.entries.get(name)


@pytest.mark.asyncio
async def test_global_installer_observes_only_exact_catalog_and_load_probe() -> None:
    storage = _GlobalStorage()
    catalog = _GlobalCatalog()
    adapter = GlobalScopedReceiverInstaller(storage=storage, catalog=catalog, committer=object(), precommit_scan=lambda *_: None)
    name = "research-assistant"
    metadata = {
        "contractVersion": "1.1.0",
        "skillVersionId": "sv.research.1.0.0",
        "version": "1.0.0",
        "runtimeSkillName": name,
        "packageDigest": "sha256:" + "a" * 64,
    }
    entry = type(
        "Entry",
        (),
        {
            "skill_version_id": metadata["skillVersionId"],
            "version": metadata["version"],
            "package_digest": metadata["packageDigest"],
            "activated_revision": 3,
        },
    )()

    assert await adapter.observe_global_skill(runtime_skill_name=name) is None
    storage.metadata[name] = metadata
    assert (await adapter.observe_global_skill(runtime_skill_name=name))["freshness"] == "unavailable"
    catalog.entries[name] = entry
    exact = await adapter.observe_global_skill(runtime_skill_name=name)
    assert exact == {
        "skillVersionId": metadata["skillVersionId"],
        "version": "1.0.0",
        "packageDigest": metadata["packageDigest"],
        "enabled": True,
        "loadState": "loaded",
        "freshness": "current",
        "catalogRevision": 3,
    }
    storage.metadata[name] = {**metadata, "packageDigest": "sha256:" + "b" * 64}
    assert (await adapter.observe_global_skill(runtime_skill_name=name))["freshness"] == "stale"


@pytest.mark.asyncio
async def test_capabilities_never_infer_global_support_from_adapter_presence() -> None:
    base = dict(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=_Directory(set()),
        installer=_NoopInstaller(),
        global_installer=object(),
        catalog_revision_provider=lambda: 3,
        clock=lambda: NOW,
    )
    closed = ReceiverRuntimeHandler(**base)
    closed_snapshot = await closed.get_capabilities(principal=_principal("receiver:capabilities:read", "receiver:install:global"))
    assert closed_snapshot.capabilities.global_install == "unsupported"

    ready = ReceiverRuntimeHandler(
        **base,
        capability_readiness=_global_readiness(),
    )
    ready_snapshot = await ready.get_capabilities(principal=_principal("receiver:capabilities:read", "receiver:install:global"))
    assert ready_snapshot.capabilities.global_install == "supported"
    assert ready_snapshot.capabilities.observation == "global_only"

    forbidden_snapshot = await ready.get_capabilities(principal=_principal("receiver:capabilities:read"))
    assert forbidden_snapshot.access_mode == "read_only"
    assert forbidden_snapshot.blocked_by == ["FORBIDDEN"]


@pytest.mark.asyncio
async def test_ready_global_operation_recovers_to_exact_loaded_success() -> None:
    package_stream = io.BytesIO()
    with zipfile.ZipFile(package_stream, "w") as archive:
        archive.writestr(
            "research-assistant/SKILL.md",
            "---\nname: research-assistant\nversion: 1.0.0\ndescription: test\n---\n",
        )
    package = package_stream.getvalue()
    command = {
        "receiverOperationId": str(uuid4()),
        "receiverBindingId": "df-global-test",
        "skillVersionId": "sv.research.1.0.0",
        "runtimeSkillName": "research-assistant",
        "packageDigest": f"sha256:{hashlib.sha256(package).hexdigest()}",
        "packageSizeBytes": len(package),
        "packageMediaType": "application/zip",
        "policyRevision": "trust-policy-test",
        "expectedObservedState": "ABSENT",
        "actorAudit": {
            "principalId": "11111111-1111-4111-8111-111111111111",
            "action": "skill:install_global",
        },
        "target": {"scope": "GLOBAL"},
    }

    class _GlobalInstaller:
        def __init__(self) -> None:
            self.observed = None
            self.install_calls = 0
            self.activation_calls = 0

        async def observe_global_skill(self, *, runtime_skill_name):
            assert runtime_skill_name == "research-assistant"
            return self.observed

        async def install_global_skill(self, *, command, manifest_version, package):
            del package
            self.install_calls += 1
            self.observed = {
                "skillVersionId": command.skill_version_id,
                "version": manifest_version,
                "packageDigest": command.package_digest,
                "enabled": True,
                "loadState": "loaded",
                "freshness": "current",
            }

        async def activate_global_skill(self, *, runtime_skill_name):
            assert runtime_skill_name == "research-assistant"
            self.activation_calls += 1

    global_installer = _GlobalInstaller()
    handler = ReceiverRuntimeHandler(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=_Directory(set()),
        installer=_NoopInstaller(),
        global_installer=global_installer,
        catalog_revision_provider=lambda: 3,
        capability_readiness=_global_readiness(),
        clock=lambda: NOW,
    )

    accepted = await handler.submit_install(
        principal=_principal("receiver:install:global"),
        idempotency_key="install-global-stage-three-0001",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )
    recovered = (await handler.recover_pending())[0]

    assert accepted.phase == "accepted"
    assert recovered.phase == "succeeded"
    assert recovered.observed is not None
    assert recovered.observed.load_state == "loaded"
    assert recovered.observed.freshness == "current"
    assert global_installer.install_calls == global_installer.activation_calls == 1


@pytest.mark.asyncio
async def test_operations_get_hides_scope_authorization_like_missing_operation() -> None:
    package_stream = io.BytesIO()
    with zipfile.ZipFile(package_stream, "w") as archive:
        archive.writestr(
            "research-assistant/SKILL.md",
            "---\nname: research-assistant\nversion: 1.0.0\ndescription: test\n---\n",
        )
    package = package_stream.getvalue()
    operation_id = str(uuid4())
    command = {
        "receiverOperationId": operation_id,
        "receiverBindingId": "df-user-test",
        "skillVersionId": "sv.research.1.0.0",
        "runtimeSkillName": "research-assistant",
        "packageDigest": f"sha256:{hashlib.sha256(package).hexdigest()}",
        "packageSizeBytes": len(package),
        "packageMediaType": "application/zip",
        "policyRevision": "trust-policy-test",
        "expectedObservedState": "ABSENT",
        "actorAudit": {
            "principalId": "11111111-1111-4111-8111-111111111111",
            "action": "skill:install_for_user",
        },
        "target": {"scope": "USER", "deerFlowUserId": "user-a"},
    }
    handler = ReceiverRuntimeHandler(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=_Directory({"user-a"}),
        installer=_NoopInstaller(),
        clock=lambda: NOW,
    )
    await handler.submit_install(
        principal=_principal("receiver:install:user"),
        idempotency_key="install-user-scope-rbac-0001",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )

    principal = _principal("receiver:operations:read")
    for candidate in (operation_id, str(uuid4())):
        with pytest.raises(ReceiverProviderError) as raised:
            await handler.get_operation(principal=principal, operation_id=candidate)
        assert raised.value.code == "INVALID_INSTALLATION_TARGET"
        assert raised.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_observation", "expected_code"),
    [
        (
            {
                "skillVersionId": "sv.research.1.0.0",
                "version": "2.0.0",
                "packageDigest": None,
                "enabled": False,
                "loadState": "unknown",
                "freshness": "stale",
            },
            "SKILL_NAME_CONFLICT",
        ),
        (
            {
                "skillVersionId": None,
                "version": None,
                "packageDigest": None,
                "enabled": None,
                "loadState": "unknown",
                "freshness": "unavailable",
            },
            "OBSERVATION_UNAVAILABLE",
        ),
    ],
)
async def test_global_validation_distinguishes_conflict_from_unavailable(initial_observation, expected_code) -> None:
    package_stream = io.BytesIO()
    with zipfile.ZipFile(package_stream, "w") as archive:
        archive.writestr(
            "research-assistant/SKILL.md",
            "---\nname: research-assistant\nversion: 1.0.0\ndescription: test\n---\n",
        )
    package = package_stream.getvalue()
    command = {
        "receiverOperationId": str(uuid4()),
        "receiverBindingId": "df-global-test",
        "skillVersionId": "sv.research.1.0.0",
        "runtimeSkillName": "research-assistant",
        "packageDigest": f"sha256:{hashlib.sha256(package).hexdigest()}",
        "packageSizeBytes": len(package),
        "packageMediaType": "application/zip",
        "policyRevision": "trust-policy-test",
        "expectedObservedState": "ABSENT",
        "actorAudit": {
            "principalId": "11111111-1111-4111-8111-111111111111",
            "action": "skill:install_global",
        },
        "target": {"scope": "GLOBAL"},
    }

    class _ObservedGlobalInstaller:
        async def observe_global_skill(self, **kwargs):
            del kwargs
            return initial_observation

    handler = ReceiverRuntimeHandler(
        store=InMemoryReceiverOperationStore(),
        package_store=InMemoryReceiverPackageStore(),
        directory=_Directory(set()),
        installer=_NoopInstaller(),
        global_installer=_ObservedGlobalInstaller(),
        catalog_revision_provider=lambda: 3,
        capability_readiness=_global_readiness(),
        clock=lambda: NOW,
    )
    await handler.submit_install(
        principal=_principal("receiver:install:global"),
        idempotency_key=f"install-global-{expected_code.lower()}-0001",
        request_sha256=handler.canonical_command_digest(command),
        command_payload=command,
        package=package,
    )

    recovered = (await handler.recover_pending())[0]

    assert recovered.phase == "rejected"
    assert recovered.error is not None
    assert recovered.error.code == expected_code
