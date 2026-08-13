"""Runtime conformance for the default-deny Nexus receiver provider boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.nexus_receiver import install_nexus_receiver_provider
from app.gateway.nexus_receiver.auth import ReceiverServiceAuthenticationRequired, ReceiverServicePrincipal
from app.gateway.nexus_receiver.directory import ReceiverDirectoryCursorRejected
from app.gateway.nexus_receiver.models import ReceiverCursorPage, ReceiverUser, ReceiverUserPage

CORRELATION_ID = "receiver-provider-conformance-01"
CAPABILITIES_PATH = "/api/v1/nexus/skill-receiver/capabilities"
USERS_PATH = "/api/v1/nexus/skill-receiver/users"


class _TestAuthenticator:
    def __init__(self) -> None:
        self.principals = {
            "reader-a": ReceiverServicePrincipal(
                subject="nexus-reader-a",
                profile="oauth2_client_credentials",
                actions=frozenset({"receiver:capabilities:read", "receiver:user-directory:read"}),
            ),
            "reader-b": ReceiverServicePrincipal(
                subject="nexus-reader-b",
                profile="mtls",
                actions=frozenset({"receiver:user-directory:read"}),
            ),
            "no-actions": ReceiverServicePrincipal(
                subject="nexus-no-actions",
                profile="mtls",
                actions=frozenset(),
            ),
        }

    async def authenticate(self, request: Request) -> ReceiverServicePrincipal:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        principal = self.principals.get(token) if scheme == "Bearer" else None
        if principal is None:
            raise ReceiverServiceAuthenticationRequired()
        return principal


class _ControlledDirectory:
    def __init__(self) -> None:
        self.calls = 0
        self._next_cursor = 0
        self._cursors: dict[str, tuple[str, str, int]] = {}
        self._users = [
            ReceiverUser(
                user_id="df-user-42",
                display_name="Chen Wei",
                account_label="c***@example.com",
                active=True,
                install_eligible=True,
            ),
            ReceiverUser(
                user_id="df-user-43",
                display_name="Chen Yu",
                account_label="y***@example.com",
                active=True,
                install_eligible=False,
            ),
            ReceiverUser(
                user_id="df-user-44",
                display_name="Li Ming",
                account_label="l***@example.com",
                active=False,
                install_eligible=False,
            ),
        ]

    async def search(
        self,
        *,
        principal: ReceiverServicePrincipal,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> ReceiverUserPage:
        self.calls += 1
        normalized_query = (query or "").strip().casefold()
        offset = 0
        if cursor is not None:
            cursor_context = self._cursors.get(cursor)
            if cursor_context is None or cursor_context[:2] != (principal.subject, normalized_query):
                raise ReceiverDirectoryCursorRejected()
            offset = cursor_context[2]

        matches = [user for user in self._users if not normalized_query or normalized_query in (user.display_name or "").casefold() or normalized_query in (user.account_label or "").casefold()]
        items = matches[offset : offset + limit]
        next_offset = offset + len(items)
        has_more = next_offset < len(matches)
        next_cursor = None
        if has_more:
            self._next_cursor += 1
            next_cursor = f"opaque-cursor-{self._next_cursor}"
            self._cursors[next_cursor] = (principal.subject, normalized_query, next_offset)
        return ReceiverUserPage(
            items=items,
            page=ReceiverCursorPage(has_more=has_more, next_cursor=next_cursor),
            observed_at=datetime(2026, 8, 11, 5, 0, tzinfo=UTC),
        )


class _BrokenAuthenticator:
    async def authenticate(self, request: Request) -> ReceiverServicePrincipal:
        raise RuntimeError("credential verifier diagnostics must remain private")


class _BrokenDirectory:
    async def search(self, **kwargs) -> ReceiverUserPage:
        raise RuntimeError("directory backend diagnostics must remain private")


def _app(*, authenticator=None, directory=None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    if authenticator is not None:
        app.state.nexus_receiver_service_authenticator = authenticator
    if directory is not None:
        app.state.nexus_receiver_user_directory = directory
    install_nexus_receiver_provider(app)
    return app


def _headers(token: str | None = None, correlation_id: str = CORRELATION_ID) -> dict[str, str]:
    headers = {"X-Correlation-ID": correlation_id}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_default_runtime_rejects_service_auth_and_browser_session_cannot_bypass() -> None:
    client = TestClient(_app())

    for cookies in ({}, {"access_token": "browser-session-token"}):
        response = client.get(CAPABILITIES_PATH, headers=_headers(), cookies=cookies)
        assert response.status_code == 401
        assert response.headers["X-Correlation-ID"] == CORRELATION_ID
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_missing_service_action_is_forbidden_before_directory_provider() -> None:
    directory = _ControlledDirectory()
    response = TestClient(_app(authenticator=_TestAuthenticator(), directory=directory)).get(USERS_PATH, headers=_headers("no-actions"))

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert directory.calls == 0


def test_capabilities_are_dimensioned_and_strictly_default_deny() -> None:
    response = TestClient(_app(authenticator=_TestAuthenticator())).get(CAPABILITIES_PATH, headers=_headers("reader-a"))

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    payload = response.json()
    assert payload["contractVersion"] == "1.1.0"
    assert payload["transportProfile"] == "http_v1"
    assert payload["connection"] == "healthy"
    assert payload["accessMode"] == "read_only"
    assert payload["freshness"] == "current"
    assert payload["authorization"] == {
        "profile": "oauth2_client_credentials",
        "grantedActions": ["receiver:capabilities:read", "receiver:user-directory:read"],
    }
    assert payload["capabilities"] == {
        "userDirectory": "unsupported",
        "globalInstall": "unsupported",
        "userInstall": "unsupported",
        "observation": "unsupported",
        "activation": "unsupported",
        "durableOperations": "unsupported",
        "observedPackageDigest": "unsupported",
    }
    assert {
        "CAPABILITY_READ_ONLY",
        "USER_DIRECTORY_UNSUPPORTED",
        "GLOBAL_INSTALL_UNSUPPORTED",
        "USER_INSTALL_UNSUPPORTED",
        "GLOBAL_ACTIVATION_UNSUPPORTED",
        "TRUST_POLICY_NOT_CONFIGURED",
        "COMPATIBILITY_UNKNOWN",
    } <= set(payload["blockedBy"])


@pytest.mark.parametrize("profile", ["unapproved", "ssh_forced_command", "unknown"])
def test_http_service_principal_rejects_non_http_authentication_profiles(profile: str) -> None:
    with pytest.raises(ValueError, match="profile is not approved"):
        ReceiverServicePrincipal(
            subject="nexus-reader-a",
            profile=profile,  # type: ignore[arg-type]
            actions=frozenset({"receiver:capabilities:read"}),
        )


def test_directory_is_unsupported_until_a_reviewed_provider_is_injected() -> None:
    response = TestClient(_app(authenticator=_TestAuthenticator())).get(USERS_PATH, headers=_headers("reader-a"))

    assert response.status_code == 409
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    assert response.json()["code"] == "USER_DIRECTORY_UNSUPPORTED"


@pytest.mark.parametrize(
    ("app", "path", "token", "code"),
    [
        (_app(authenticator=_BrokenAuthenticator()), CAPABILITIES_PATH, "opaque", "RECEIVER_NOT_READY"),
        (
            _app(authenticator=_TestAuthenticator(), directory=_BrokenDirectory()),
            USERS_PATH,
            "reader-a",
            "USER_DIRECTORY_UNAVAILABLE",
        ),
    ],
)
def test_provider_failures_are_stable_redacted_problems(app: FastAPI, path: str, token: str, code: str) -> None:
    response = TestClient(app).get(path, headers=_headers(token))

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == code
    assert "diagnostics" not in response.text


def test_controlled_directory_supports_bounded_search_and_opaque_pagination() -> None:
    directory = _ControlledDirectory()
    client = TestClient(_app(authenticator=_TestAuthenticator(), directory=directory))

    first = client.get(USERS_PATH, headers=_headers("reader-a"), params={"query": " chen ", "limit": 1})
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["items"] == [
        {
            "userId": "df-user-42",
            "displayName": "Chen Wei",
            "accountLabel": "c***@example.com",
            "active": True,
            "installEligible": True,
        }
    ]
    assert first_payload["page"]["hasMore"] is True
    cursor = first_payload["page"]["nextCursor"]
    assert cursor.startswith("opaque-cursor-")

    second = client.get(USERS_PATH, headers=_headers("reader-a"), params={"query": "CHEN", "cursor": cursor, "limit": 1})
    assert second.status_code == 200
    assert second.json()["items"][0]["userId"] == "df-user-43"
    assert second.json()["page"] == {"hasMore": False, "nextCursor": None}
    assert "email" not in second.text.casefold()


@pytest.mark.parametrize(
    ("token", "query", "cursor"),
    [
        ("reader-a", "li", "from_first_page"),
        ("reader-b", "chen", "from_first_page"),
        ("reader-a", "chen", "tampered-cursor"),
    ],
)
def test_directory_cursor_is_bound_to_principal_and_normalized_query(token: str, query: str, cursor: str) -> None:
    directory = _ControlledDirectory()
    client = TestClient(_app(authenticator=_TestAuthenticator(), directory=directory))
    first = client.get(USERS_PATH, headers=_headers("reader-a"), params={"query": "chen", "limit": 1})
    first_cursor = first.json()["page"]["nextCursor"]

    response = client.get(
        USERS_PATH,
        headers=_headers(token),
        params={"query": query, "cursor": first_cursor if cursor == "from_first_page" else cursor, "limit": 1},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_directory_limits_and_masked_account_projection_are_schema_enforced() -> None:
    client = TestClient(_app(authenticator=_TestAuthenticator(), directory=_ControlledDirectory()))

    assert client.get(USERS_PATH, headers=_headers("reader-a"), params={"limit": 0}).status_code == 422
    assert client.get(USERS_PATH, headers=_headers("reader-a"), params={"limit": 101}).status_code == 422
    with pytest.raises(ValidationError):
        ReceiverUser(
            user_id="df-user-unsafe",
            display_name=None,
            account_label="complete.address@example.com",
            active=True,
            install_eligible=True,
        )


def test_production_gateway_mounts_runtime_routes_but_keeps_writes_default_deny() -> None:
    from app.gateway.app import create_app

    gateway = create_app()
    routes = {(method, route.path) for route in gateway.routes for method in getattr(route, "methods", set())}

    assert ("GET", CAPABILITIES_PATH) in routes
    assert ("GET", USERS_PATH) in routes
    receiver_root = USERS_PATH.removesuffix("/users")
    assert ("POST", f"{receiver_root}/operations") in routes
    assert ("GET", f"{receiver_root}/operations/{{operation_id}}") in routes
    assert ("POST", f"{receiver_root}/observations/query") in routes
    assert CAPABILITIES_PATH not in gateway.openapi()["paths"]
    assert USERS_PATH not in gateway.openapi()["paths"]
    assert f"{receiver_root}/operations" not in gateway.openapi()["paths"]
    assert f"{receiver_root}/observations/query" not in gateway.openapi()["paths"]

    response = TestClient(gateway).get(CAPABILITIES_PATH, headers=_headers())
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"

    response = TestClient(gateway).post(
        f"{receiver_root}/operations",
        headers={**_headers(), "Idempotency-Key": "default-deny-test", "X-Request-SHA256": "sha256:" + "0" * 64},
        files={
            "command": (None, "{}"),
            "package": ("skill.zip", b"not-a-package", "application/zip"),
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"
