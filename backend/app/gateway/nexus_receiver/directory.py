"""Controlled user-directory Port for the Nexus receiver boundary."""

from __future__ import annotations

from typing import Protocol

from app.gateway.nexus_receiver.auth import ReceiverProviderError, ReceiverServicePrincipal
from app.gateway.nexus_receiver.models import ReceiverUserPage


class ReceiverDirectoryCursorRejected(Exception):
    """The opaque cursor is invalid for the current principal/search context."""


class ReceiverUserDirectoryUnsupported(ReceiverProviderError):
    status_code = 409
    code = "USER_DIRECTORY_UNSUPPORTED"
    title = "User directory unsupported"
    detail = "A reviewed receiver user-directory provider is not configured."


class ReceiverUserDirectoryUnavailable(ReceiverProviderError):
    status_code = 503
    code = "USER_DIRECTORY_UNAVAILABLE"
    title = "User directory unavailable"
    detail = "The controlled receiver user directory is temporarily unavailable."
    retryable = True


class ReceiverUserDirectory(Protocol):
    """Minimal directory projection; implementations own opaque cursor binding."""

    async def search(
        self,
        *,
        principal: ReceiverServicePrincipal,
        query: str | None,
        cursor: str | None,
        limit: int,
    ) -> ReceiverUserPage: ...


def get_receiver_user_directory(app_state: object) -> ReceiverUserDirectory:
    directory: ReceiverUserDirectory | None = getattr(
        app_state,
        "nexus_receiver_user_directory",
        None,
    )
    if directory is None:
        raise ReceiverUserDirectoryUnsupported()
    return directory
