"""Secure filesystem storage for Knowledge Base source documents."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from deerflow.utils.file_io import run_file_io

logger = logging.getLogger(__name__)


class KnowledgeUploadFilenameError(ValueError):
    """An upload or storage filename is unsafe."""


class KnowledgeUploadTypeError(ValueError):
    """The filename extension and declared media type are not supported."""


class KnowledgeFileTooLargeError(ValueError):
    """The uploaded file exceeds the configured single-file limit."""


class KnowledgeQuotaExceededError(ValueError):
    """Persisting the uploaded file would exceed the Knowledge Base quota."""


_SUPPORTED_MEDIA_TYPES: dict[str, frozenset[str]] = {
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown"}),
    ".pdf": frozenset({"application/pdf"}),
    ".docx": frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ".pptx": frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"}),
    ".xlsx": frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
}

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

_LOCKS_GUARD = threading.Lock()
_DIRECTORY_LOCKS: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class SavedKnowledgeFile:
    """Metadata for an original file durably persisted by the service."""

    path: Path
    original_filename: str
    storage_name: str
    content_type: str
    size_bytes: int
    content_sha256: str


@dataclass(frozen=True)
class KnowledgeFileFingerprint:
    size_bytes: int
    content_sha256: str


def normalize_knowledge_filename(filename: str) -> str:
    """Normalize a display filename while rejecting every path-like input."""

    if not isinstance(filename, str):
        raise KnowledgeUploadFilenameError("filename must be a string")

    normalized = unicodedata.normalize("NFKC", filename).strip()
    if not normalized or normalized in {".", ".."}:
        raise KnowledgeUploadFilenameError("filename is empty or reserved")
    try:
        encoded_length = len(normalized.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise KnowledgeUploadFilenameError("filename contains invalid Unicode") from exc
    if encoded_length > 255:
        raise KnowledgeUploadFilenameError("filename is too long")
    if "/" in normalized or "\\" in normalized:
        raise KnowledgeUploadFilenameError("filename must not contain path components")
    if Path(normalized).is_absolute() or _looks_like_windows_absolute_path(normalized):
        raise KnowledgeUploadFilenameError("absolute filenames are not allowed")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise KnowledgeUploadFilenameError("filename contains control characters")
    if normalized[-1] in {".", " "}:
        raise KnowledgeUploadFilenameError("filename has an unsafe trailing character")
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise KnowledgeUploadFilenameError("filename is reserved by the operating system")
    return normalized


def validate_knowledge_upload_type(filename: str, content_type: str) -> None:
    """Require an explicitly supported extension/media-type pairing."""

    normalized = normalize_knowledge_filename(filename)
    if not isinstance(content_type, str):
        raise KnowledgeUploadTypeError("content type must be a string")
    normalized_content_type = content_type.partition(";")[0].strip().lower()
    allowed = _SUPPORTED_MEDIA_TYPES.get(Path(normalized).suffix.lower())
    if allowed is None or normalized_content_type not in allowed:
        raise KnowledgeUploadTypeError("unsupported or mismatched upload type")


class KnowledgeFileStore:
    """Persist original Knowledge Base files below an owner/scope boundary."""

    def __init__(self, root: str | os.PathLike[str], *, chunk_size: int = 1024 * 1024) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._root = Path(root)
        self._chunk_size = chunk_size

    async def save(
        self,
        stream: BinaryIO,
        *,
        owner_user_id: str,
        scope_id: str,
        storage_name: str,
        original_filename: str,
        content_type: str,
        max_file_size_bytes: int,
        max_total_size_bytes: int | None,
    ) -> SavedKnowledgeFile:
        """Stage, fsync, and atomically commit one source document."""

        worker = asyncio.create_task(
            run_file_io(
                self._save_sync,
                stream,
                owner_user_id=owner_user_id,
                scope_id=scope_id,
                storage_name=storage_name,
                original_filename=original_filename,
                content_type=content_type,
                max_file_size_bytes=max_file_size_bytes,
                max_total_size_bytes=max_total_size_bytes,
            )
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                saved = await worker
            except Exception:
                pass
            else:
                try:
                    await run_file_io(self._delete_sync, saved.path)
                except Exception:
                    logger.error("Knowledge file cancellation compensation failed")
            raise

    async def fingerprint(self, stream: BinaryIO, *, max_file_size_bytes: int) -> KnowledgeFileFingerprint:
        """Read a replay candidate without writing another physical file."""

        return await run_file_io(
            self._fingerprint_sync,
            stream,
            max_file_size_bytes=max_file_size_bytes,
        )

    async def read(self, *, owner_user_id: str, scope_id: str, storage_name: str) -> bytes:
        """Read a persisted source document without blocking the event loop."""

        return await run_file_io(
            self._read_sync,
            owner_user_id=owner_user_id,
            scope_id=scope_id,
            storage_name=storage_name,
        )

    async def delete(self, path: str | os.PathLike[str]) -> None:
        """Idempotently remove a persisted file during database compensation."""

        await run_file_io(self._delete_sync, path)

    def _save_sync(
        self,
        stream: BinaryIO,
        *,
        owner_user_id: str,
        scope_id: str,
        storage_name: str,
        original_filename: str,
        content_type: str,
        max_file_size_bytes: int,
        max_total_size_bytes: int | None,
    ) -> SavedKnowledgeFile:
        if max_file_size_bytes < 0 or (max_total_size_bytes is not None and max_total_size_bytes < 0):
            raise ValueError("upload limits must not be negative")
        if not hasattr(stream, "read"):
            raise TypeError("stream must provide a read method")

        normalized_original = normalize_knowledge_filename(original_filename)
        validate_knowledge_upload_type(normalized_original, content_type)
        safe_storage_name = _server_path_component(storage_name, label="storage name")
        validate_knowledge_upload_type(safe_storage_name, content_type)
        safe_owner = _server_path_component(owner_user_id, label="owner user id")
        safe_scope = _server_path_component(scope_id, label="scope id")
        normalized_content_type = content_type.partition(";")[0].strip().lower()

        root, directory = self._ensure_scope_directory(safe_owner, safe_scope)
        destination = directory / safe_storage_name
        _require_within(root, destination)
        directory_lock = _directory_lock(directory)

        with directory_lock:
            if destination.exists():
                raise FileExistsError(f"knowledge file already exists: {safe_storage_name}")
            persisted_size = _persisted_size(directory) if max_total_size_bytes is not None else 0
            temp_path: Path | None = None
            committed = False
            try:
                descriptor, raw_temp_path = tempfile.mkstemp(
                    prefix=f".{safe_storage_name}.",
                    suffix=".part",
                    dir=directory,
                )
                temp_path = Path(raw_temp_path)
                size_bytes = 0
                digest = hashlib.sha256()
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        while True:
                            chunk = stream.read(self._chunk_size)
                            if not chunk:
                                break
                            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                                raise TypeError("upload stream must return bytes")
                            chunk_bytes = bytes(chunk)
                            size_bytes += len(chunk_bytes)
                            if size_bytes > max_file_size_bytes:
                                raise KnowledgeFileTooLargeError("single-file upload limit exceeded")
                            if max_total_size_bytes is not None and persisted_size + size_bytes > max_total_size_bytes:
                                raise KnowledgeQuotaExceededError("knowledge storage quota exceeded")
                            handle.write(chunk_bytes)
                            digest.update(chunk_bytes)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    _close_descriptor(descriptor)
                    raise

                _validate_persisted_content(temp_path, Path(safe_storage_name).suffix.lower())
                os.replace(temp_path, destination)
                temp_path = None
                committed = True
                return SavedKnowledgeFile(
                    path=destination,
                    original_filename=normalized_original,
                    storage_name=safe_storage_name,
                    content_type=normalized_content_type,
                    size_bytes=size_bytes,
                    content_sha256=digest.hexdigest(),
                )
            except Exception:
                if temp_path is not None:
                    _unlink_if_present(temp_path)
                if committed:
                    _unlink_if_present(destination)
                raise

    def _read_sync(self, *, owner_user_id: str, scope_id: str, storage_name: str) -> bytes:
        safe_owner = _server_path_component(owner_user_id, label="owner user id")
        safe_scope = _server_path_component(scope_id, label="scope id")
        safe_storage_name = _server_path_component(storage_name, label="storage name")
        root = self._root.resolve(strict=False)
        path = root / safe_owner / safe_scope / safe_storage_name
        resolved = path.resolve(strict=False)
        _require_within(root, resolved)
        if resolved != path:
            raise KnowledgeUploadFilenameError("knowledge file path contains a symbolic link")
        return path.read_bytes()

    def _fingerprint_sync(self, stream: BinaryIO, *, max_file_size_bytes: int) -> KnowledgeFileFingerprint:
        if max_file_size_bytes < 0:
            raise ValueError("max_file_size_bytes must not be negative")
        size_bytes = 0
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(self._chunk_size)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("upload stream must return bytes")
            chunk_bytes = bytes(chunk)
            size_bytes += len(chunk_bytes)
            if size_bytes > max_file_size_bytes:
                raise KnowledgeFileTooLargeError("single-file upload limit exceeded")
            digest.update(chunk_bytes)
        return KnowledgeFileFingerprint(size_bytes=size_bytes, content_sha256=digest.hexdigest())

    def _delete_sync(self, path: str | os.PathLike[str]) -> None:
        root = self._root.resolve(strict=False)
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        _require_within(root, resolved)
        if resolved != candidate:
            raise KnowledgeUploadFilenameError("knowledge file path contains a symbolic link")
        _unlink_if_present(candidate)

    def _ensure_scope_directory(self, owner_user_id: str, scope_id: str) -> tuple[Path, Path]:
        self._root.mkdir(parents=True, exist_ok=True)
        root = self._root.resolve(strict=True)
        directory = root / owner_user_id / scope_id
        directory.mkdir(parents=True, exist_ok=True)
        resolved_directory = directory.resolve(strict=True)
        _require_within(root, resolved_directory)
        if resolved_directory != directory:
            raise KnowledgeUploadFilenameError("knowledge directory contains a symbolic link")
        return root, resolved_directory


def _looks_like_windows_absolute_path(value: str) -> bool:
    return len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in {"/", "\\"}


def _server_path_component(value: str, *, label: str) -> str:
    try:
        normalized = normalize_knowledge_filename(value)
    except KnowledgeUploadFilenameError as exc:
        raise KnowledgeUploadFilenameError(f"unsafe {label}") from exc
    if normalized != value:
        raise KnowledgeUploadFilenameError(f"{label} must already be normalized")
    return normalized


def _require_within(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise KnowledgeUploadFilenameError("knowledge path escapes its storage root") from exc


def _directory_lock(directory: Path) -> threading.Lock:
    key = str(directory)
    with _LOCKS_GUARD:
        return _DIRECTORY_LOCKS.setdefault(key, threading.Lock())


def _persisted_size(directory: Path) -> int:
    total = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.endswith(".part") or not entry.is_file(follow_symlinks=False):
                continue
            total += entry.stat(follow_symlinks=False).st_size
    return total


def _validate_persisted_content(path: Path, extension: str) -> None:
    if extension in {".txt", ".md"}:
        if b"\x00" in path.read_bytes():
            raise KnowledgeUploadTypeError("text document contains binary NUL bytes")
        return
    if extension == ".pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise KnowledgeUploadTypeError("PDF signature is missing")
        return

    required_member = {
        ".docx": "word/document.xml",
        ".pptx": "ppt/presentation.xml",
        ".xlsx": "xl/workbook.xml",
    }.get(extension)
    if required_member is None:
        raise KnowledgeUploadTypeError("unsupported persisted document type")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise KnowledgeUploadTypeError("Office document is not a valid ZIP container") from exc
    if len(names) > 10_000:
        raise KnowledgeUploadTypeError("Office document contains too many archive entries")
    if any(not name or name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/") for name in names):
        raise KnowledgeUploadTypeError("Office document contains an unsafe archive path")
    if "[Content_Types].xml" not in names or required_member not in names:
        raise KnowledgeUploadTypeError("Office document content does not match its extension")


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
