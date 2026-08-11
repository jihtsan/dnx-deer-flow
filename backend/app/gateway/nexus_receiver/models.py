"""Strict runtime models for the enabled read-only receiver surface."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.nexus_receiver.auth import ReceiverAuthenticationProfile, ReceiverServiceAction


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _ReceiverModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class ReceiverAuthorizationSnapshot(_ReceiverModel):
    profile: ReceiverAuthenticationProfile
    granted_actions: list[ReceiverServiceAction]


class ReceiverCapabilities(_ReceiverModel):
    user_directory: Literal["unsupported"] = "unsupported"
    global_install: Literal["unsupported"] = "unsupported"
    user_install: Literal["unsupported"] = "unsupported"
    observation: Literal["unsupported"] = "unsupported"
    activation: Literal["unsupported"] = "unsupported"
    durable_operations: Literal["unsupported"] = "unsupported"
    observed_package_digest: Literal["unsupported"] = "unsupported"


class ReceiverCapabilitySnapshot(_ReceiverModel):
    contract_version: Literal["1.0.0"] = "1.0.0"
    runtime_version: str | None = Field(min_length=1, max_length=100)
    connection: Literal["healthy"] = "healthy"
    access_mode: Literal["read_only"] = "read_only"
    freshness: Literal["current"] = "current"
    authorization: ReceiverAuthorizationSnapshot
    capabilities: ReceiverCapabilities
    blocked_by: list[str]
    observed_at: datetime


class ReceiverUser(_ReceiverModel):
    user_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    account_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^(?:[^@\s]+|[^@\s]*\*[^@\s]*@[^@\s]+)$",
    )
    active: bool
    install_eligible: bool


class ReceiverCursorPage(_ReceiverModel):
    has_more: bool
    next_cursor: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_cursor_state(self) -> ReceiverCursorPage:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("next_cursor must be present exactly when has_more is true")
        return self


class ReceiverUserPage(_ReceiverModel):
    items: list[ReceiverUser] = Field(max_length=100)
    page: ReceiverCursorPage
    observed_at: datetime


class ReceiverProblem(_ReceiverModel):
    type: str
    title: str = Field(min_length=1, max_length=200)
    status: int = Field(ge=400, le=599)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str = Field(min_length=1, max_length=2000)
    instance: str
    correlation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    timestamp: datetime
    retryable: bool
