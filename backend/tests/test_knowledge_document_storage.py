from __future__ import annotations

import asyncio
import io
import os
import threading
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.knowledge.storage import (
    KnowledgeFileStore,
    KnowledgeFileTooLargeError,
    KnowledgeQuotaExceededError,
    KnowledgeUploadFilenameError,
    KnowledgeUploadTypeError,
    normalize_knowledge_filename,
    validate_knowledge_upload_type,
)


@pytest.mark.parametrize(
    "filename",
    [
        "/etc/passwd",
        "../secret.txt",
        "folder/file.txt",
        r"folder\file.txt",
        r"C:\secret.txt",
        ".",
        "..",
        "bad\x00name.txt",
    ],
)
def test_filename_rejects_absolute_traversal_and_path_components(filename: str) -> None:
    with pytest.raises(KnowledgeUploadFilenameError):
        normalize_knowledge_filename(filename)


def test_filename_is_normalized_without_trusting_a_client_path() -> None:
    assert normalize_knowledge_filename("  产品说明.txt  ") == "产品说明.txt"
    assert normalize_knowledge_filename("ＲＥＡＤＭＥ.md") == "README.md"


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("notes.txt", "text/plain"),
        ("guide.md", "text/markdown"),
        ("manual.pdf", "application/pdf"),
        ("spec.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
def test_supported_extension_and_media_type_are_accepted(filename: str, content_type: str) -> None:
    validate_knowledge_upload_type(filename, content_type)


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("payload.exe", "application/octet-stream"),
        ("manual.pdf", "text/plain"),
        ("notes.txt", "application/pdf"),
        ("archive.zip", "application/zip"),
    ],
)
def test_unsupported_or_mismatched_file_types_are_rejected(filename: str, content_type: str) -> None:
    with pytest.raises(KnowledgeUploadTypeError):
        validate_knowledge_upload_type(filename, content_type)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("forged.pdf", "application/pdf"),
        ("forged.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("forged.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("forged.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ],
)
async def test_binary_formats_require_a_matching_server_verified_signature(
    tmp_path: Path,
    filename: str,
    content_type: str,
) -> None:
    store = KnowledgeFileStore(tmp_path)
    with pytest.raises(KnowledgeUploadTypeError):
        await store.save(
            io.BytesIO(b"client-controlled content is not the declared document type"),
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name=f"server-{filename}",
            original_filename=filename,
            content_type=content_type,
            max_file_size_bytes=1024,
            max_total_size_bytes=4096,
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_pdf_and_office_container_signatures_are_accepted(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    pdf = await store.save(
        io.BytesIO(b"%PDF-1.7\nminimal contract fixture"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="server-manual.pdf",
        original_filename="manual.pdf",
        content_type="application/pdf",
        max_file_size_bytes=4096,
        max_total_size_bytes=8192,
    )

    office_content = io.BytesIO()
    with zipfile.ZipFile(office_content, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    docx = await store.save(
        io.BytesIO(office_content.getvalue()),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="server-spec.docx",
        original_filename="spec.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        max_file_size_bytes=4096,
        max_total_size_bytes=8192,
    )

    assert pdf.path.read_bytes().startswith(b"%PDF-")
    assert docx.path.read_bytes() == office_content.getvalue()


@pytest.mark.asyncio
async def test_text_documents_reject_embedded_nul_bytes(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    with pytest.raises(KnowledgeUploadTypeError):
        await store.save(
            io.BytesIO(b"text\x00binary"),
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name="server-notes.txt",
            original_filename="notes.txt",
            content_type="text/plain",
            max_file_size_bytes=1024,
            max_total_size_bytes=4096,
        )


@pytest.mark.asyncio
async def test_file_is_staged_and_atomically_persisted_with_a_server_name(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    saved = await store.save(
        io.BytesIO(b"knowledge-body"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="doc-stable.txt",
        original_filename="产品说明.txt",
        content_type="text/plain",
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
    )

    assert saved.original_filename == "产品说明.txt"
    assert saved.storage_name == "doc-stable.txt"
    assert saved.size_bytes == len(b"knowledge-body")
    assert saved.content_sha256 == "495ac604971c09f44cd5588a23f8f4922937157c324f8d35bddf5f326447da64"
    assert saved.path.read_bytes() == b"knowledge-body"
    assert saved.path.name == "doc-stable.txt"
    assert list(saved.path.parent.glob("*.part")) == []


@pytest.mark.asyncio
async def test_size_limit_removes_staging_file_and_never_creates_destination(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    with pytest.raises(KnowledgeFileTooLargeError):
        await store.save(
            io.BytesIO(b"too-large"),
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name="doc-large.txt",
            original_filename="large.txt",
            content_type="text/plain",
            max_file_size_bytes=4,
            max_total_size_bytes=4096,
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_total_quota_counts_only_persisted_documents(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    await store.save(
        io.BytesIO(b"1234"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="doc-first.txt",
        original_filename="first.txt",
        content_type="text/plain",
        max_file_size_bytes=10,
        max_total_size_bytes=6,
    )

    with pytest.raises(KnowledgeQuotaExceededError):
        await store.save(
            io.BytesIO(b"567"),
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name="doc-second.txt",
            original_filename="second.txt",
            content_type="text/plain",
            max_file_size_bytes=10,
            max_total_size_bytes=6,
        )

    persisted = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert [path.name for path in persisted] == ["doc-first.txt"]


class InterruptedStream(io.BytesIO):
    def __init__(self) -> None:
        super().__init__(b"partial")
        self._reads = 0

    def read(self, size: int = -1) -> bytes:
        self._reads += 1
        if self._reads > 1:
            raise OSError("client upload interrupted")
        return super().read(3)


@pytest.mark.asyncio
async def test_interrupted_upload_cleans_temporary_and_destination_files(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path, chunk_size=3)
    with pytest.raises(OSError, match="client upload interrupted"):
        await store.save(
            InterruptedStream(),
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name="doc-interrupted.txt",
            original_filename="interrupted.txt",
            content_type="text/plain",
            max_file_size_bytes=1024,
            max_total_size_bytes=4096,
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


class CancelledStream(io.BytesIO):
    def __init__(self) -> None:
        super().__init__(b"complete body")
        self.started = threading.Event()
        self.release = threading.Event()

    def read(self, size: int = -1) -> bytes:
        self.started.set()
        self.release.wait(timeout=2)
        return super().read(size)


@pytest.mark.asyncio
async def test_cancelled_save_waits_for_worker_and_removes_any_committed_file(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    stream = CancelledStream()
    committed = threading.Event()
    original_replace = os.replace

    def replace_and_record(source, destination) -> None:
        original_replace(source, destination)
        committed.set()

    with patch("app.knowledge.storage.os.replace", side_effect=replace_and_record):
        task = asyncio.create_task(
            store.save(
                stream,
                owner_user_id="alice",
                scope_id="scope-stable",
                storage_name="doc-cancelled.txt",
                original_filename="cancelled.txt",
                content_type="text/plain",
                max_file_size_bytes=1024,
                max_total_size_bytes=4096,
            )
        )
        assert await asyncio.to_thread(stream.started.wait, 1)
        task.cancel()
        stream.release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(committed.wait, 1)
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_atomic_replace_failure_cleans_temporary_and_destination_files(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    with patch("app.knowledge.storage.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            await store.save(
                io.BytesIO(b"body"),
                owner_user_id="alice",
                scope_id="scope-stable",
                storage_name="doc-replace.txt",
                original_filename="replace.txt",
                content_type="text/plain",
                max_file_size_bytes=1024,
                max_total_size_bytes=4096,
            )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


@pytest.mark.asyncio
async def test_delete_is_idempotent_for_database_compensation(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path)
    saved = await store.save(
        io.BytesIO(b"body"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="doc-compensate.txt",
        original_filename="compensate.txt",
        content_type="text/plain",
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
    )

    await store.delete(saved.path)
    await store.delete(saved.path)
    assert not saved.path.exists()
