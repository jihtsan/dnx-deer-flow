"""Startup-only release gates for the Nexus Skill receiver."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_REFERENCE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_POLICY_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FACTORY_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


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
    production: bool = False
    recovery_poll_interval_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    ssh_account: Literal["nexus-receiver"] | None = None
    recovery_signal_socket: str | None = None
    host_key_secret_ref: ReceiverSecretReference | None = None
    principal_map_secret_ref: ReceiverSecretReference | None = None
    cursor_signing_key_secret_ref: ReceiverSecretReference | None = None
    provider_factory: str | None = None
    package_stage_path: str | None = None
    global_storage_path: str | None = None
    directory_policy_revision: str | None = None
    trust_policy_revision: str | None = None
    compatibility_policy_revision: str | None = None
    audit_policy_revision: str | None = None
    rate_limit_policy_revision: str | None = None

    @model_validator(mode="after")
    def require_closed_release_decisions(self) -> Self:
        if not self.enabled:
            return self
        required = [
            "ssh_account",
            "recovery_signal_socket",
            "host_key_secret_ref",
            "principal_map_secret_ref",
            "directory_policy_revision",
            "trust_policy_revision",
            "compatibility_policy_revision",
        ]
        if self.production:
            required.extend(
                (
                    "cursor_signing_key_secret_ref",
                    "provider_factory",
                    "package_stage_path",
                    "global_storage_path",
                    "audit_policy_revision",
                    "rate_limit_policy_revision",
                )
            )
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"enabled Nexus receiver is missing release gates: {', '.join(missing)}")
        if self.recovery_signal_socket is None or not Path(self.recovery_signal_socket).is_absolute():
            raise ValueError("recovery_signal_socket must be an absolute deployment path")
        if self.production:
            if self.provider_factory is None or _FACTORY_PATH.fullmatch(self.provider_factory) is None:
                raise ValueError("provider_factory must be a module:callable path")
            for name in ("package_stage_path", "global_storage_path"):
                value = getattr(self, name)
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise ValueError(f"{name} must be an absolute deployment path")
            if self.package_stage_path == self.global_storage_path:
                raise ValueError("package_stage_path and global_storage_path must be different")
        for name in [
            "directory_policy_revision",
            "trust_policy_revision",
            "compatibility_policy_revision",
        ] + (["audit_policy_revision", "rate_limit_policy_revision"] if self.production else []):
            value = getattr(self, name)
            if not isinstance(value, str) or _POLICY_REVISION.fullmatch(value) is None:
                raise ValueError(f"{name} has an invalid shape")
        return self
