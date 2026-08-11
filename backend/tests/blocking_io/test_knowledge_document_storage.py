"""Knowledge document filesystem work must stay off the event loop."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from app.knowledge.storage import KnowledgeFileStore

pytestmark = pytest.mark.asyncio


async def test_fingerprint_save_read_and_compensation_delete_do_not_block_event_loop(tmp_path: Path) -> None:
    store = KnowledgeFileStore(tmp_path / "knowledge")
    fingerprint = await store.fingerprint(
        io.BytesIO(b"non-blocking knowledge document"),
        max_file_size_bytes=1024,
    )
    assert fingerprint.size_bytes == len(b"non-blocking knowledge document")

    saved = await store.save(
        io.BytesIO(b"non-blocking knowledge document"),
        owner_user_id="alice",
        scope_id="scope-stable",
        storage_name="doc-stable.txt",
        original_filename="说明.txt",
        content_type="text/plain",
        max_file_size_bytes=1024,
        max_total_size_bytes=4096,
    )
    assert (
        await store.read(
            owner_user_id="alice",
            scope_id="scope-stable",
            storage_name="doc-stable.txt",
        )
        == b"non-blocking knowledge document"
    )

    await store.delete(saved.path)
    assert not await asyncio.to_thread(saved.path.exists)
