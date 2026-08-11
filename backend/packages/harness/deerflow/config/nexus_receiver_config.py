"""Startup-only release gates for the Nexus Skill receiver."""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_REFERENCE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_POLICY_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class ReceiverSecretReference(BaseModel):
    """Opaque deployment Secret location; never contains Secret bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    key: str

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if _REFERENCE_PART.fullmatch(self.name) is None or _REFERENCE_PART.fullmatch(self.key) is None:
            raise ValueError("receiver Secret reference has an invalid shape")
        return self


class NexusReceiverConfig(BaseModel):
    """Fail-closed release configuration for the receiver adapters."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    recovery_poll_interval_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    ssh_account: str | None = None
    host_key_secret_ref: ReceiverSecretReference | None = None
    principal_map_secret_ref: ReceiverSecretReference | None = None
    directory_policy_revision: str | None = None
    trust_policy_revision: str | None = None
    compatibility_policy_revision: str | None = None

    @model_validator(mode="after")
    def require_closed_release_decisions(self) -> Self:
        if not self.enabled:
            return self
        required = (
            "ssh_account",
            "host_key_secret_ref",
            "principal_map_secret_ref",
            "directory_policy_revision",
            "trust_policy_revision",
            "compatibility_policy_revision",
        )
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"enabled Nexus receiver is missing release gates: {', '.join(missing)}")
        if self.ssh_account is None or _ACCOUNT.fullmatch(self.ssh_account) is None:
            raise ValueError("ssh_account has an invalid shape")
        for name in required[3:]:
            value = getattr(self, name)
            if not isinstance(value, str) or _POLICY_REVISION.fullmatch(value) is None:
                raise ValueError(f"{name} has an invalid shape")
        return self
