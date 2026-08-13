"""USER-scoped DeerFlow Skill installer adapter for the receiver runtime."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.gateway.nexus_receiver.models import ReceiverInstallCommand

_METADATA_FILE = ".nexus-receiver.json"


class UserScopedReceiverInstaller:
    """Adapter over DeerFlow's existing secure, atomic USER Skill storage."""

    def __init__(
        self,
        storage_factory: Callable[[str], Any] | None = None,
        *,
        precommit_scan: Callable[[Path, str], Awaitable[None]] | None = None,
    ) -> None:
        if storage_factory is None:
            from deerflow.skills.storage import get_or_new_user_skill_storage

            storage_factory = get_or_new_user_skill_storage
        self._storage_factory = storage_factory
        self._precommit_scan = precommit_scan

    @staticmethod
    def _write_package(package: bytes) -> str:
        fd, path = tempfile.mkstemp(prefix="nexus-receiver-", suffix=".skill")
        with os.fdopen(fd, "wb") as output:
            output.write(package)
            output.flush()
            os.fsync(output.fileno())
        return path

    async def install_user_skill(
        self,
        *,
        user_id: str,
        command: ReceiverInstallCommand,
        manifest_version: str,
        package: bytes,
    ) -> None:
        storage = await asyncio.to_thread(self._storage_factory, user_id)
        archive_path = await asyncio.to_thread(self._write_package, package)
        try:
            metadata = {
                "contractVersion": "1.0.0",
                "skillVersionId": command.skill_version_id,
                "version": manifest_version,
                "runtimeSkillName": command.runtime_skill_name,
                "packageDigest": command.package_digest,
            }
            if self._precommit_scan is None:
                result = await storage.ainstall_skill_from_archive_with_metadata(
                    archive_path,
                    receiver_metadata=metadata,
                )
            else:
                result = await storage.ainstall_skill_from_archive_with_metadata(
                    archive_path,
                    receiver_metadata=metadata,
                    precommit_scan=self._precommit_scan,
                )
            if result.get("skill_name") != command.runtime_skill_name:
                raise ValueError("installed Skill name does not match the validated receiver command")
        finally:
            await asyncio.to_thread(Path(archive_path).unlink, missing_ok=True)

    async def activate_user_skill(self, *, user_id: str, runtime_skill_name: str) -> None:
        storage = await asyncio.to_thread(self._storage_factory, user_id)
        await asyncio.to_thread(storage.set_skill_enabled_state, runtime_skill_name, True)

    async def observe_user_skill(
        self,
        *,
        user_id: str,
        runtime_skill_name: str,
    ) -> dict[str, Any] | None:
        storage = await asyncio.to_thread(self._storage_factory, user_id)
        skills = await asyncio.to_thread(storage.load_skills)
        named_skill = next((skill for skill in skills if skill.name == runtime_skill_name), None)
        if named_skill is None:
            return None
        metadata_path = storage.get_custom_skill_dir(runtime_skill_name) / _METADATA_FILE
        metadata: dict[str, Any] = {}
        try:
            metadata = await asyncio.to_thread(lambda: json.loads(metadata_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            # A pre-existing/non-receiver Skill is still an observable name
            # conflict; it simply carries no receiver identity proof.
            pass
        enabled = bool(getattr(named_skill, "enabled", False))
        return {
            "skillVersionId": metadata.get("skillVersionId"),
            "version": metadata.get("version"),
            "packageDigest": metadata.get("packageDigest"),
            "enabled": enabled,
            "loadState": "loaded" if enabled else "disabled",
        }
