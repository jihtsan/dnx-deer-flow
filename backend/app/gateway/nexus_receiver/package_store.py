"""Durable, transport-neutral package staging for receiver operations."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol


class ReceiverPackageConflict(Exception):
    """A staged operation/digest binding already contains different bytes."""


class ReceiverPackageStore(Protocol):
    async def stage(self, operation_id: str, digest: str, package: bytes) -> None: ...

    async def read(self, operation_id: str, digest: str) -> bytes | None: ...

    async def delete(self, operation_id: str, digest: str) -> None: ...


class InMemoryReceiverPackageStore:
    """Deterministic package store for tests; production must use durable staging."""

    def __init__(self) -> None:
        self._packages: dict[tuple[str, str], bytes] = {}
        self._lock = asyncio.Lock()

    async def stage(self, operation_id: str, digest: str, package: bytes) -> None:
        key = (operation_id, digest)
        async with self._lock:
            existing = self._packages.get(key)
            if existing is not None and existing != package:
                raise ReceiverPackageConflict
            self._packages[key] = package

    async def read(self, operation_id: str, digest: str) -> bytes | None:
        return self._packages.get((operation_id, digest))

    async def delete(self, operation_id: str, digest: str) -> None:
        async with self._lock:
            self._packages.pop((operation_id, digest), None)


class LocalReceiverPackageStore:
    """Owner-only atomic package staging for approved future runtime wiring."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @staticmethod
    def _digest_hex(digest: str) -> str:
        prefix, separator, value = digest.partition(":")
        if prefix != "sha256" or separator != ":" or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("invalid package digest")
        return value

    def _path(self, operation_id: str, digest: str) -> Path:
        if len(operation_id) != 36 or any(character not in "0123456789abcdef-" for character in operation_id.casefold()):
            raise ValueError("invalid receiver operation ID")
        return self._root / operation_id / f"{self._digest_hex(digest)}.zip"

    @staticmethod
    def _verify(package: bytes, digest: str) -> None:
        actual = f"sha256:{hashlib.sha256(package).hexdigest()}"
        if actual != digest:
            raise ValueError("package digest mismatch")

    def _stage(self, operation_id: str, digest: str, package: bytes) -> None:
        self._verify(package, digest)
        target = self._path(operation_id, digest)
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
        if target.exists():
            if target.read_bytes() != package:
                raise ReceiverPackageConflict
            return

        descriptor, temporary_name = tempfile.mkstemp(prefix=".package-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(package)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != package:
                    raise ReceiverPackageConflict from None
            directory_descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, operation_id: str, digest: str) -> bytes | None:
        path = self._path(operation_id, digest)
        try:
            package = path.read_bytes()
        except FileNotFoundError:
            return None
        self._verify(package, digest)
        return package

    def _delete(self, operation_id: str, digest: str) -> None:
        path = self._path(operation_id, digest)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    async def stage(self, operation_id: str, digest: str, package: bytes) -> None:
        await asyncio.to_thread(self._stage, operation_id, digest, package)

    async def read(self, operation_id: str, digest: str) -> bytes | None:
        return await asyncio.to_thread(self._read, operation_id, digest)

    async def delete(self, operation_id: str, digest: str) -> None:
        await asyncio.to_thread(self._delete, operation_id, digest)
