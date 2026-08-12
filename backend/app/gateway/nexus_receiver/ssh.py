"""Strict forced-command framing for the canonical ssh_v1 receiver binding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.gateway.nexus_receiver.auth import ReceiverProviderError, ReceiverServiceAuthenticationRequired
from app.gateway.nexus_receiver.runtime import (
    ReceiverRuntimeError,
    ReceiverRuntimeHandler,
    ReceiverTransportPrincipal,
)

_COMMANDS = {
    "nexus-skill-receiver-v1 capabilities.get": "capabilities.get",
    "nexus-skill-receiver-v1 users.list": "users.list",
    "nexus-skill-receiver-v1 install.submit": "install.submit",
    "nexus-skill-receiver-v1 operations.get": "operations.get",
    "nexus-skill-receiver-v1 observations.query": "observations.query",
}
_MAX_FRAME_BYTES = 65_536
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_FRAME_FIELDS = {
    "capabilities.get": (frozenset({"contractVersion", "correlationId"}), frozenset()),
    "users.list": (frozenset({"contractVersion", "correlationId", "limit"}), frozenset({"query", "cursor"})),
    "install.submit": (
        frozenset({"contractVersion", "correlationId", "idempotencyKey", "requestSha256", "command"}),
        frozenset(),
    ),
    "operations.get": (frozenset({"contractVersion", "correlationId", "operationId"}), frozenset()),
    "observations.query": (frozenset({"contractVersion", "correlationId", "query"}), frozenset()),
}


@dataclass(frozen=True, slots=True)
class ForcedCommandResult:
    exit_code: int
    stdout: bytes


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _problem(exc: ReceiverProviderError, correlation_id: str = "receiver-ssh") -> dict:
    return {
        "type": f"/problems/{exc.code.lower().replace('_', '-')}",
        "title": exc.title,
        "status": exc.status_code,
        "code": exc.code,
        "detail": exc.detail,
        "instance": f"/api/v1/nexus/skill-receiver/problems/{uuid4().hex}",
        "correlationId": correlation_id,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "retryable": exc.retryable,
    }


def _parse_stdin(action: str, stdin: bytes) -> tuple[dict, bytes]:
    delimiter = stdin.find(b"\n")
    if delimiter < 0 or delimiter > _MAX_FRAME_BYTES:
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 JSON frame is missing or too large.", status_code=422)
    frame_bytes = stdin[:delimiter]
    package = stdin[delimiter + 1 :]
    if len(package) > _MAX_PACKAGE_BYTES:
        raise ReceiverRuntimeError(code="PACKAGE_TOO_LARGE", detail="The ssh_v1 package exceeds the receiver size limit.", status_code=413)
    if not frame_bytes or (action != "install.submit" and package):
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 frame has invalid trailing bytes.", status_code=422)
    try:
        frame = json.loads(frame_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 JSON frame is invalid.", status_code=422) from None
    if not isinstance(frame, dict) or frame.get("contractVersion") != "1.0.0":
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 JSON frame does not match contract version 1.0.0.", status_code=422)
    required, optional = _FRAME_FIELDS[action]
    fields = frozenset(frame)
    if not required.issubset(fields) or fields - required - optional:
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 JSON frame has invalid fields.", status_code=422)
    correlation_id = frame.get("correlationId")
    if not isinstance(correlation_id, str) or _CORRELATION_PATTERN.fullmatch(correlation_id) is None:
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 correlation ID is invalid.", status_code=422)
    if frame_bytes != json.dumps(frame, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"):
        raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 JSON frame is not canonical.", status_code=422)
    if action == "users.list":
        limit = frame.get("limit")
        query = frame.get("query")
        cursor = frame.get("cursor")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 user-directory limit is invalid.", status_code=422)
        if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 100):
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 user-directory query is invalid.", status_code=422)
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 500):
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 user-directory cursor is invalid.", status_code=422)
    if action == "operations.get":
        try:
            UUID(frame.get("operationId", ""))
        except (TypeError, ValueError):
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The ssh_v1 receiver operation ID is invalid.", status_code=422) from None
    return frame, package


def forced_command_input_limit(original_command: str) -> int:
    """Return the maximum bytes the forced-command entry point may read."""
    if original_command == "nexus-skill-receiver-v1 install.submit":
        return _MAX_FRAME_BYTES + 1 + _MAX_PACKAGE_BYTES
    return _MAX_FRAME_BYTES + 1


async def dispatch_forced_command(
    original_command: str,
    stdin: bytes,
    *,
    handler: ReceiverRuntimeHandler | None,
    principal: ReceiverTransportPrincipal | None = None,
) -> ForcedCommandResult:
    """Dispatch an exact forced command without shell parsing or execution."""
    correlation_id = "receiver-ssh"
    try:
        action = _COMMANDS.get(original_command)
        if action is None:
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The forced command is not an approved ssh_v1 action.", status_code=422)
        frame, package = _parse_stdin(action, stdin)
        correlation_id = frame.get("correlationId", correlation_id)
        if not isinstance(correlation_id, str) or not correlation_id:
            correlation_id = "receiver-ssh"
        if handler is None:
            raise ReceiverRuntimeError(
                code="RECEIVER_NOT_READY",
                title="Receiver runtime unavailable",
                detail="The forced-command receiver runtime is not configured.",
                status_code=503,
                retryable=True,
            )
        if principal is None:
            raise ReceiverServiceAuthenticationRequired()
        if action == "install.submit":
            result = await handler.submit_install(
                principal=principal,
                idempotency_key=frame.get("idempotencyKey", ""),
                request_sha256=frame.get("requestSha256", ""),
                command_payload=frame.get("command", {}),
                package=package,
            )
        elif action == "operations.get":
            result = await handler.get_operation(principal=principal, operation_id=frame.get("operationId", ""))
        elif action == "observations.query":
            result = await handler.query_observation(principal=principal, query_payload=frame.get("query", {}))
        elif action == "capabilities.get":
            result = await handler.get_capabilities(principal=principal)
        elif action == "users.list":
            result = await handler.list_users(
                principal=principal,
                query=frame.get("query"),
                cursor=frame.get("cursor"),
                limit=frame.get("limit", 0),
            )
        else:
            raise ReceiverRuntimeError(code="PACKAGE_INVALID", detail="The forced command action is invalid.", status_code=422)
        return ForcedCommandResult(0, _json(result.model_dump(mode="json", by_alias=True)))
    except ReceiverProviderError as exc:
        return ForcedCommandResult(10, _json(_problem(exc, correlation_id)))
    except Exception:
        exc = ReceiverRuntimeError(code="INTERNAL_ERROR", detail="The receiver could not process the forced command.", status_code=500)
        return ForcedCommandResult(10, _json(_problem(exc, correlation_id)))
