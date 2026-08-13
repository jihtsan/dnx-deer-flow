"""Default-deny HTTP routes for the canonical Nexus receiver contract."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from app.gateway.nexus_receiver.auth import (
    ReceiverProviderError,
    ReceiverServiceActionForbidden,
    ReceiverServicePrincipal,
    authenticate_receiver_service,
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
    ReceiverObservedSkill,
    ReceiverOperation,
    ReceiverProblem,
    ReceiverSkillPage,
    ReceiverUserPage,
)
from app.gateway.nexus_receiver.runtime import ReceiverRuntimeError, ReceiverRuntimeHandler, get_receiver_runtime_handler

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
    cached = getattr(request.state, "nexus_receiver_correlation_id", None)
    if isinstance(cached, str):
        return cached
    supplied = request.headers.get("X-Correlation-ID", "")
    if _CORRELATION_PATTERN.fullmatch(supplied):
        correlation_id = supplied
    else:
        correlation_id = f"receiver-{uuid4().hex}"
    request.state.nexus_receiver_correlation_id = correlation_id
    return correlation_id


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
    runtime_handler = getattr(request.app.state, "nexus_receiver_runtime_handler", None)
    if isinstance(runtime_handler, ReceiverRuntimeHandler):
        return await runtime_handler.get_capabilities(principal=principal, correlation_id=_correlation_id(request))
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
    runtime_handler = getattr(request.app.state, "nexus_receiver_runtime_handler", None)
    if isinstance(runtime_handler, ReceiverRuntimeHandler):
        page = await runtime_handler.list_users(
            principal=principal,
            query=query,
            cursor=cursor,
            limit=limit,
            correlation_id=_correlation_id(request),
        )
        response.headers["X-Correlation-ID"] = _correlation_id(request)
        return page
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


@router.post(
    "/skills/query",
    response_model=ReceiverSkillPage,
    include_in_schema=False,
)
async def list_receiver_skills(
    request: Request,
    response: Response,
    body: dict,
    principal: Annotated[
        ReceiverServicePrincipal,
        Depends(require_receiver_action("receiver:skills:list:user")),
    ],
) -> ReceiverSkillPage:
    result = await get_receiver_runtime_handler(request.app.state).list_skills(
        principal=principal,
        request_payload=body,
        correlation_id=_correlation_id(request),
    )
    response.headers["X-Correlation-ID"] = _correlation_id(request)
    return result


@router.post(
    "/operations",
    response_model=ReceiverOperation,
    status_code=202,
    include_in_schema=False,
)
async def create_receiver_operation(
    request: Request,
    response: Response,
    principal: Annotated[ReceiverServicePrincipal, Depends(authenticate_receiver_service)],
    command: Annotated[str, Form(min_length=2, max_length=32_768)],
    package: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")],
    request_sha256: Annotated[str, Header(alias="X-Request-SHA256", pattern=r"^sha256:[a-f0-9]{64}$")],
) -> ReceiverOperation:
    """Validate transport input, then delegate to the shared receiver handler."""
    if package.content_type != "application/zip":
        raise ReceiverRuntimeError(
            code="PACKAGE_MEDIA_TYPE_UNSUPPORTED",
            detail="The receiver accepts application/zip Skill packages only.",
            status_code=415,
        )
    try:
        command_payload = json.loads(command)
    except json.JSONDecodeError:
        raise ReceiverRuntimeError(
            code="INVALID_INSTALLATION_TARGET",
            detail="The receiver install command is not valid JSON.",
            status_code=422,
        ) from None
    if not isinstance(command_payload, dict):
        raise ReceiverRuntimeError(
            code="INVALID_INSTALLATION_TARGET",
            detail="The receiver install command must be a JSON object.",
            status_code=422,
        )
    package_bytes = await package.read(64 * 1024 * 1024 + 1)
    handler = get_receiver_runtime_handler(request.app.state)
    result = await handler.submit_install(
        principal=principal,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        command_payload=command_payload,
        package=package_bytes,
        correlation_id=_correlation_id(request),
    )
    correlation_id = _correlation_id(request)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["Location"] = f"{_PREFIX}/operations/{result.operation_id}"
    return result


@router.get(
    "/operations/{operation_id}",
    response_model=ReceiverOperation,
    include_in_schema=False,
)
async def get_receiver_operation(
    request: Request,
    response: Response,
    operation_id: Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")],
    principal: Annotated[ReceiverServicePrincipal, Depends(authenticate_receiver_service)],
) -> ReceiverOperation:
    result = await get_receiver_runtime_handler(request.app.state).get_operation(
        principal=principal,
        operation_id=operation_id,
        correlation_id=_correlation_id(request),
    )
    response.headers["X-Correlation-ID"] = _correlation_id(request)
    return result


@router.post(
    "/observations/query",
    response_model=ReceiverObservedSkill,
    include_in_schema=False,
)
async def query_receiver_observation(
    request: Request,
    response: Response,
    query: dict,
    principal: Annotated[ReceiverServicePrincipal, Depends(authenticate_receiver_service)],
) -> ReceiverObservedSkill:
    result = await get_receiver_runtime_handler(request.app.state).query_observation(
        principal=principal,
        query_payload=query,
        correlation_id=_correlation_id(request),
    )
    response.headers["X-Correlation-ID"] = _correlation_id(request)
    return result
