"""USER-scoped DeerFlow Skill installer adapter for the receiver runtime."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

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

    async def list_user_skills(self, *, user_id: str) -> list[dict[str, Any]]:
        storage = await asyncio.to_thread(self._storage_factory, user_id)
        return await asyncio.to_thread(storage.list_receiver_inventory)


class _GlobalCatalog(Protocol):
    async def get_global_skill(self, runtime_skill_name: str): ...


class _GlobalCommitter(Protocol):
    async def commit(self, archive_path, *, receiver_metadata, precommit_scan, now): ...


class GlobalScopedReceiverInstaller:
    """Adapter over native GLOBAL managed storage plus its durable catalog."""

    def __init__(self, *, storage: Any, catalog: _GlobalCatalog, committer: _GlobalCommitter, precommit_scan: Callable, clock: Callable[[], Any] | None = None) -> None:
        from datetime import UTC, datetime

        self._storage = storage
        self._catalog = catalog
        self._committer = committer
        self._precommit_scan = precommit_scan
        self._clock = clock or (lambda: datetime.now(UTC))

    async def install_global_skill(self, *, command: ReceiverInstallCommand, manifest_version: str, package: bytes) -> None:
        archive_path = await asyncio.to_thread(UserScopedReceiverInstaller._write_package, package)
        try:
            await self._committer.commit(
                archive_path,
                receiver_metadata={
                    "contractVersion": "1.0.0",
                    "skillVersionId": command.skill_version_id,
                    "version": manifest_version,
                    "runtimeSkillName": command.runtime_skill_name,
                    "packageDigest": command.package_digest,
                },
                precommit_scan=self._precommit_scan,
                now=self._clock(),
            )
        finally:
            await asyncio.to_thread(Path(archive_path).unlink, missing_ok=True)

    async def activate_global_skill(self, *, runtime_skill_name: str) -> None:
        observed = await self.observe_global_skill(runtime_skill_name=runtime_skill_name)
        if observed is None or observed.get("loadState") != "loaded" or observed.get("freshness") != "current":
            raise ValueError("GLOBAL Skill is not exactly loaded from the active catalog")

    async def observe_global_skill(self, *, runtime_skill_name: str) -> dict[str, Any] | None:
        metadata, probe, catalog_entry = await asyncio.gather(
            asyncio.to_thread(self._storage.receiver_metadata, runtime_skill_name),
            asyncio.to_thread(self._storage.load_probe, runtime_skill_name),
            self._catalog.get_global_skill(runtime_skill_name),
        )
        if metadata is None and catalog_entry is None and probe is None:
            return None
        if not isinstance(metadata, dict) or catalog_entry is None or probe is None or getattr(probe, "name", None) != runtime_skill_name:
            return {
                "skillVersionId": (metadata or {}).get("skillVersionId"),
                "version": (metadata or {}).get("version"),
                "packageDigest": (metadata or {}).get("packageDigest"),
                "enabled": None,
                "loadState": "unknown",
                "freshness": "unavailable",
            }
        exact = metadata.get("skillVersionId") == catalog_entry.skill_version_id and metadata.get("version") == catalog_entry.version and metadata.get("packageDigest") == catalog_entry.package_digest
        return {
            "skillVersionId": metadata.get("skillVersionId"),
            "version": metadata.get("version"),
            "packageDigest": metadata.get("packageDigest"),
            "enabled": True if exact else False,
            "loadState": "loaded" if exact else "unknown",
            "freshness": "current" if exact else "stale",
            "catalogRevision": catalog_entry.activated_revision,
        }
