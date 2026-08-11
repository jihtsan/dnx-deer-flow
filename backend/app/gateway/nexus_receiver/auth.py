"""Dedicated machine-authentication Port for the Nexus receiver boundary."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from fastapi import Request

ReceiverAuthenticationProfile = Literal["oauth2_client_credentials", "mtls", "combined"]
ReceiverAuthorizationProfile = Literal[
    "unapproved",
    "oauth2_client_credentials",
    "mtls",
    "combined",
    "ssh_forced_command",
]
ReceiverServiceAction = Literal[
    "receiver:capabilities:read",
    "receiver:user-directory:read",
    "receiver:install:global",
    "receiver:install:user",
    "receiver:observe:global",
    "receiver:observe:user",
    "receiver:operations:read",
]

_APPROVED_PROFILES = frozenset({"oauth2_client_credentials", "mtls", "combined"})
_KNOWN_ACTIONS = frozenset(
    {
        "receiver:capabilities:read",
        "receiver:user-directory:read",
        "receiver:install:global",
        "receiver:install:user",
        "receiver:observe:global",
        "receiver:observe:user",
        "receiver:operations:read",
    }
)
_SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class ReceiverProviderError(Exception):
    """Base class for errors safe to expose at the receiver boundary."""

    status_code = 500
    code = "INTERNAL_ERROR"
    title = "Receiver internal error"
    detail = "The receiver could not complete the request."
    retryable = False


class ReceiverServiceAuthenticationRequired(ReceiverProviderError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    title = "Authentication required"
    detail = "A dedicated receiver service identity is required."


class ReceiverServiceActionForbidden(ReceiverProviderError):
    status_code = 403
    code = "FORBIDDEN"
    title = "Receiver action forbidden"
    detail = "The receiver service principal lacks the required action."


class ReceiverServiceAuthenticationUnavailable(ReceiverProviderError):
    status_code = 503
    code = "RECEIVER_NOT_READY"
    title = "Receiver authentication unavailable"
    detail = "Dedicated receiver service authentication is not ready."
    retryable = True


@dataclass(frozen=True, slots=True)
class ReceiverServicePrincipal:
    """Authenticated service identity and its explicitly granted actions."""

    subject: str
    profile: ReceiverAuthenticationProfile
    actions: frozenset[ReceiverServiceAction]

    def __post_init__(self) -> None:
        if _SUBJECT_PATTERN.fullmatch(self.subject) is None:
            raise ValueError("receiver service subject has an invalid shape")
        if self.profile not in _APPROVED_PROFILES:
            raise ValueError("receiver service profile is not approved")
        normalized_actions = frozenset(self.actions)
        unknown_actions = normalized_actions - _KNOWN_ACTIONS
        if unknown_actions:
            raise ValueError("receiver service principal contains unknown actions")
        object.__setattr__(self, "actions", normalized_actions)


class ReceiverServiceAuthenticator(Protocol):
    """Deployment-owned credential verification, intentionally without a built-in profile."""

    async def authenticate(self, request: Request) -> ReceiverServicePrincipal: ...


async def authenticate_receiver_service(request: Request) -> ReceiverServicePrincipal:
    """Resolve and invoke the deployment authenticator, failing closed by default."""
    authenticator: ReceiverServiceAuthenticator | None = getattr(
        request.app.state,
        "nexus_receiver_service_authenticator",
        None,
    )
    if authenticator is None:
        raise ReceiverServiceAuthenticationRequired()

    try:
        principal = await authenticator.authenticate(request)
    except ReceiverProviderError:
        raise
    except Exception as exc:
        raise ReceiverServiceAuthenticationUnavailable() from exc

    if not isinstance(principal, ReceiverServicePrincipal):
        raise ReceiverServiceAuthenticationUnavailable()
    return principal


def require_receiver_action(
    action: ReceiverServiceAction,
) -> Callable[[Request], Awaitable[ReceiverServicePrincipal]]:
    """Build a route dependency that authenticates before checking one action."""

    async def dependency(request: Request) -> ReceiverServicePrincipal:
        principal = await authenticate_receiver_service(request)
        if action not in principal.actions:
            raise ReceiverServiceActionForbidden()
        return principal

    return dependency
