"""Read-only runtime routes for the canonical Nexus receiver contract."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.gateway.nexus_receiver.auth import (
    ReceiverProviderError,
    ReceiverServiceActionForbidden,
    ReceiverServicePrincipal,
    require_receiver_action,
)
from app.gateway.nexus_receiver.directory import (
    ReceiverDirectoryCursorRejected,
    ReceiverUserDirectoryUnavailable,
    get_receiver_user_directory,
)
from app.gateway.nexus_receiver.models import (
    ReceiverAuthorizationSnapshot,
    ReceiverCapabilities,
    ReceiverCapabilitySnapshot,
    ReceiverProblem,
    ReceiverUserPage,
)

_PREFIX = "/api/v1/nexus/skill-receiver"
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_DEFAULT_DENY_BLOCKERS = [
    "CAPABILITY_READ_ONLY",
    "USER_DIRECTORY_UNSUPPORTED",
    "GLOBAL_INSTALL_UNSUPPORTED",
    "USER_INSTALL_UNSUPPORTED",
    "GLOBAL_ACTIVATION_UNSUPPORTED",
    "TRUST_POLICY_NOT_CONFIGURED",
    "COMPATIBILITY_UNKNOWN",
]

router = APIRouter(prefix=_PREFIX, tags=["Nexus Skill Receiver"])


def _correlation_id(request: Request) -> str:
    supplied = request.headers.get("X-Correlation-ID", "")
    if _CORRELATION_PATTERN.fullmatch(supplied):
        return supplied
    return f"receiver-{uuid4().hex}"


@lru_cache(maxsize=1)
def _runtime_version() -> str | None:
    try:
        return version("deer-flow")
    except PackageNotFoundError:
        return None


async def receiver_provider_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render only pre-redacted receiver errors as canonical Problem Details."""
    if not isinstance(exc, ReceiverProviderError):
        exc = ReceiverProviderError()
    correlation_id = _correlation_id(request)
    problem = ReceiverProblem(
        type=f"/problems/{exc.code.lower().replace('_', '-')}",
        title=exc.title,
        status=exc.status_code,
        code=exc.code,
        detail=exc.detail,
        instance=f"{request.url.path}/problems/{uuid4().hex}",
        correlation_id=correlation_id,
        timestamp=datetime.now(UTC),
        retryable=exc.retryable,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(mode="json", by_alias=True),
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get(
    "/capabilities",
    response_model=ReceiverCapabilitySnapshot,
    include_in_schema=False,
)
async def get_receiver_capabilities(
    request: Request,
    response: Response,
    principal: Annotated[
        ReceiverServicePrincipal,
        Depends(require_receiver_action("receiver:capabilities:read")),
    ],
) -> ReceiverCapabilitySnapshot:
    """Return independent dimensions without claiming any release-gated support."""
    response.headers["X-Correlation-ID"] = _correlation_id(request)
    return ReceiverCapabilitySnapshot(
        runtime_version=_runtime_version(),
        authorization=ReceiverAuthorizationSnapshot(
            profile=principal.profile,
            granted_actions=sorted(principal.actions),
        ),
        capabilities=ReceiverCapabilities(),
        blocked_by=_DEFAULT_DENY_BLOCKERS,
        observed_at=datetime.now(UTC),
    )


@router.get(
    "/users",
    response_model=ReceiverUserPage,
    include_in_schema=False,
)
async def list_receiver_users(
    request: Request,
    response: Response,
    principal: Annotated[
        ReceiverServicePrincipal,
        Depends(require_receiver_action("receiver:user-directory:read")),
    ],
    query: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ReceiverUserPage:
    """Delegate bounded search to an explicitly injected controlled directory."""
    directory = get_receiver_user_directory(request.app.state)
    try:
        page = await directory.search(
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

    response.headers["X-Correlation-ID"] = _correlation_id(request)
    return ReceiverUserPage.model_validate(page)
