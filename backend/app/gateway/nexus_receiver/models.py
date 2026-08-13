"""Strict runtime models for the enabled read-only receiver surface."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.gateway.nexus_receiver.auth import ReceiverAuthorizationProfile, ReceiverServiceAction


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
    profile: ReceiverAuthorizationProfile
    granted_actions: list[ReceiverServiceAction]


class ReceiverCapabilities(_ReceiverModel):
    user_directory: Literal["supported", "unsupported"] = "unsupported"
    global_install: Literal["supported", "unsupported"] = "unsupported"
    user_install: Literal["supported", "unsupported"] = "unsupported"
    observation: Literal["global_and_user", "global_only", "user_only", "unsupported"] = "unsupported"
    activation: Literal["global_and_user", "global_only", "user_only", "unsupported"] = "unsupported"
    durable_operations: Literal["supported", "unsupported"] = "unsupported"
    observed_package_digest: Literal["supported", "unsupported"] = "unsupported"


class ReceiverCapabilitySnapshot(_ReceiverModel):
    contract_version: Literal["1.1.0"] = "1.1.0"
    transport_profile: Literal["http_v1", "ssh_v1"] = "http_v1"
    runtime_version: str | None = Field(min_length=1, max_length=100)
    connection: Literal["healthy"] = "healthy"
    access_mode: Literal["read_write", "read_only", "unsupported"] = "read_only"
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


class ReceiverGlobalTarget(_ReceiverModel):
    scope: Literal["GLOBAL"]


class ReceiverUserTarget(_ReceiverModel):
    scope: Literal["USER"]
    deer_flow_user_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


ReceiverInstallationTarget = Annotated[ReceiverGlobalTarget | ReceiverUserTarget, Field(discriminator="scope")]


class ReceiverSkillListRequest(_ReceiverModel):
    contract_version: Literal["1.1.0"]
    correlation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    target: ReceiverUserTarget
    limit: int = Field(ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=500)
    query: str | None = Field(default=None, min_length=1, max_length=100)


class ReceiverSkillListItem(_ReceiverModel):
    runtime_skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$",
    )
    skill_version_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    package_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    enabled: bool
    load_state: Literal["loaded", "load_failed", "disabled", "unknown"]
    freshness: Literal["current", "stale", "unavailable"]
    observed_at: datetime


class ReceiverSkillPage(_ReceiverModel):
    items: list[ReceiverSkillListItem] = Field(max_length=100)
    page: ReceiverCursorPage
    observed_at: datetime


class ReceiverActorAudit(_ReceiverModel):
    principal_id: UUID
    action: Literal["skill:install_global", "skill:install_for_user"]


class ReceiverInstallCommand(_ReceiverModel):
    receiver_operation_id: UUID
    receiver_binding_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    skill_version_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    runtime_skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$",
    )
    package_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    package_size_bytes: int = Field(ge=1)
    package_media_type: Literal["application/zip"]
    policy_revision: str = Field(min_length=1, max_length=200)
    expected_observed_state: Literal["ABSENT"]
    actor_audit: ReceiverActorAudit
    target: ReceiverInstallationTarget

    @model_validator(mode="after")
    def validate_actor_target_binding(self) -> ReceiverInstallCommand:
        expected_action = "skill:install_for_user" if self.target.scope == "USER" else "skill:install_global"
        if self.actor_audit.action != expected_action:
            raise ValueError("actorAudit action does not match target scope")
        return self


class ReceiverObservationQuery(_ReceiverModel):
    target: ReceiverInstallationTarget
    runtime_skill_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$",
    )


class ReceiverObservedSkill(_ReceiverModel):
    target: ReceiverInstallationTarget
    presence: Literal["installed", "absent"]
    skill_version_id: str | None
    version: str | None
    package_digest: str | None
    runtime_skill_name: str
    enabled: bool | None
    load_state: Literal["loaded", "load_failed", "disabled", "unknown", "absent"]
    freshness: Literal["current", "stale", "unavailable"]
    observed_at: datetime

    @model_validator(mode="after")
    def validate_presence_shape(self) -> ReceiverObservedSkill:
        identity = (self.skill_version_id, self.version, self.package_digest, self.enabled)
        if self.presence == "absent":
            if any(value is not None for value in identity) or self.load_state != "absent":
                raise ValueError("absent observation cannot carry installed identity")
        elif any(value is None for value in identity) or self.load_state == "absent":
            raise ValueError("installed observation requires identity and a non-absent load state")
        return self


class ReceiverOperationError(_ReceiverModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=2000)
    retryable: bool
    recovery_action: str | None = Field(default=None, min_length=1, max_length=500)


ReceiverOperationPhase = Literal[
    "accepted",
    "validating",
    "installing",
    "activating",
    "succeeded",
    "rejected",
    "failed",
]


class ReceiverOperation(_ReceiverModel):
    operation_id: UUID
    phase: ReceiverOperationPhase
    freshness: Literal["current", "stale", "unavailable"] = "current"
    target: ReceiverInstallationTarget
    skill_version_id: str
    runtime_skill_name: str
    package_digest: str
    observed: ReceiverObservedSkill | None = None
    accepted_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: ReceiverOperationError | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> ReceiverOperation:
        terminal = self.phase in {"succeeded", "rejected", "failed"}
        if terminal != (self.completed_at is not None):
            raise ValueError("completedAt must be set exactly for terminal operations")
        if self.phase == "succeeded":
            if self.observed is None or self.error is not None:
                raise ValueError("succeeded operation requires Observed and no error")
        if self.phase in {"rejected", "failed"} and self.error is None:
            raise ValueError("unsuccessful terminal operation requires an error")
        return self
