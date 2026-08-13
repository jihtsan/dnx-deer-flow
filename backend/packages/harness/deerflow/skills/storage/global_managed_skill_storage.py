"""Atomic storage primitive for receiver-managed GLOBAL Skills.

This module intentionally does not wire receiver actions or capability
advertisement. It owns only the native managed integration tree used by a
future GLOBAL installer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from deerflow.config.paths import Paths
from deerflow.skills.permissions import make_skill_tree_sandbox_readable
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.types import SKILL_MD_FILE, Skill, SkillCategory

_PROVIDER = "nexus"
_METADATA_FILE = ".nexus-receiver.json"
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


class GlobalManagedSkillStorage:
    """Write-once managed storage under ``integrations/skills/nexus``."""

    def __init__(self, *, base_dir: Path | str | None = None, app_config: Any = None) -> None:
        self._paths = Paths(base_dir)
        self.provider_root = self._paths.integration_skills_dir() / _PROVIDER
        self._app_config = app_config

    def skill_dir(self, name: str) -> Path:
        return self.provider_root / LocalSkillStorage.validate_skill_name(name)

    @contextmanager
    def _install_lock(self, skill_name: str):
        lock_root = self.provider_root / ".locks"
        lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = lock_root / f"{skill_name}.lock"
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                lock_file.write(b"\0")
                lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _write_metadata(skill_dir: Path, metadata: dict[str, Any]) -> None:
        metadata_path = skill_dir / _METADATA_FILE
        with metadata_path.open("w", encoding="utf-8") as output:
            json.dump(metadata, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _sync_tree(root: Path) -> None:
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as source:
                    os.fsync(source.fileno())
        if os.name != "nt":
            directories = [path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()]
            for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True) + [root]:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)

    def _commit(self, staged_skill: Path, target: Path, skill_name: str) -> None:
        from deerflow.skills.installer import SkillAlreadyExistsError

        with self._install_lock(skill_name):
            if self._name_conflicts(skill_name):
                raise SkillAlreadyExistsError(f"Skill '{skill_name}' already exists")
            make_skill_tree_sandbox_readable(staged_skill)
            self._sync_tree(staged_skill)
            os.replace(staged_skill, target)
            if os.name != "nt":
                provider_fd = os.open(self.provider_root, os.O_RDONLY)
                try:
                    os.fsync(provider_fd)
                finally:
                    os.close(provider_fd)

    def _name_conflicts(self, skill_name: str) -> bool:
        if (self._paths.base_dir / "skills" / "public" / skill_name / SKILL_MD_FILE).is_file():
            return True
        integration_root = self._paths.integration_skills_dir()
        return any((provider / skill_name / SKILL_MD_FILE).is_file() for provider in integration_root.iterdir() if provider.is_dir() and provider.name != ".locks") if integration_root.is_dir() else False

    async def install_from_archive(
        self,
        archive_path: str | Path,
        *,
        receiver_metadata: dict[str, Any],
        precommit_scan: Callable[[Path, str], Awaitable[None]],
    ) -> dict[str, Any]:
        """Validate and atomically publish one immutable managed Skill tree."""
        if precommit_scan is None:
            raise ValueError("GLOBAL managed installs require an explicit precommit scan")
        required_strings = ("contractVersion", "skillVersionId", "version", "runtimeSkillName", "packageDigest")
        if any(not isinstance(receiver_metadata.get(key), str) or not receiver_metadata[key] for key in required_strings):
            raise ValueError("GLOBAL receiver metadata is incomplete")
        if _DIGEST_PATTERN.fullmatch(receiver_metadata["packageDigest"]) is None:
            raise ValueError("GLOBAL receiver package digest is invalid")
        self.provider_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        validator = LocalSkillStorage(
            host_path=str(self._paths.base_dir / "skills"),
            app_config=self._app_config,
        )
        staging_root = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.provider_root))
        extraction_root = staging_root / "extracted"
        extraction_root.mkdir()
        try:
            skill_dir, skill_name, _unused_target = await asyncio.to_thread(
                validator._prepare_skill_archive,
                Path(archive_path),
                extraction_root,
                staging_root / "validated",
                archive_path,
            )
            if receiver_metadata.get("runtimeSkillName") != skill_name:
                raise ValueError("receiver metadata runtimeSkillName does not match the Skill manifest")
            await precommit_scan(skill_dir, skill_name)
            await asyncio.to_thread(self._write_metadata, skill_dir, receiver_metadata)
            staged_skill = staging_root / "commit" / skill_name
            await asyncio.to_thread(staged_skill.parent.mkdir, parents=True)
            await asyncio.to_thread(shutil.copytree, skill_dir, staged_skill)
            staged_probe = await asyncio.to_thread(self._probe_path, staged_skill, skill_name)
            if staged_probe is None:
                raise RuntimeError("GLOBAL managed Skill load probe failed before commit")
            target = self.skill_dir(skill_name)
            await asyncio.to_thread(self._commit, staged_skill, target, skill_name)
            probe = await asyncio.to_thread(self.load_probe, skill_name)
            if probe is None or probe.name != skill_name:
                raise RuntimeError("GLOBAL managed Skill load probe failed")
            return {"success": True, "skill_name": skill_name}
        finally:
            await asyncio.to_thread(shutil.rmtree, staging_root, True)

    def load_probe(self, name: str) -> Skill | None:
        """Parse the committed tree through the runtime's real Skill parser."""
        normalized = LocalSkillStorage.validate_skill_name(name)
        return self._probe_path(self.skill_dir(normalized), normalized)

    def receiver_metadata(self, name: str) -> dict[str, Any] | None:
        metadata_path = self.skill_dir(name) / _METADATA_FILE
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _probe_path(skill_dir: Path, normalized: str) -> Skill | None:
        from deerflow.skills.parser import parse_skill_file

        skill_file = skill_dir / SKILL_MD_FILE
        skill = parse_skill_file(
            skill_file,
            category=SkillCategory.INTEGRATION,
            relative_path=Path(_PROVIDER) / normalized,
        )
        if skill is None or skill.name != normalized:
            return None
        return skill
