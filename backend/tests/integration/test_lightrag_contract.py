"""Real contract tests for the pinned LightRAG checkout.

Run explicitly with::

    LIGHTRAG_CHECKOUT=/path/to/LightRAG PYTHONPATH=.:packages/harness \
      uv run pytest tests/integration/test_lightrag_contract.py -q
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from app.knowledge.ingestion import map_lightrag_tracking_status
from app.knowledge.lightrag import LightRAGClient
from deerflow.config.knowledge_base_config import (
    LIGHTRAG_EXPECTED_COMMIT,
    LIGHTRAG_EXPECTED_TAG,
    LightRAGConfig,
)

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class RunningLightRAG:
    base_url: str
    api_key: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def running_lightrag(tmp_path_factory: pytest.TempPathFactory) -> RunningLightRAG:
    raw_checkout = os.getenv("LIGHTRAG_CHECKOUT")
    if not raw_checkout:
        pytest.skip("set LIGHTRAG_CHECKOUT to run the real LightRAG contract tests")
    checkout = Path(raw_checkout).resolve()
    server = checkout / ".venv" / "bin" / "lightrag-server"
    if not server.is_file():
        pytest.skip(f"LightRAG server executable is missing under {checkout}")

    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "-C", str(checkout), "describe", "--tags", "--always"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert commit == LIGHTRAG_EXPECTED_COMMIT
    assert tag == LIGHTRAG_EXPECTED_TAG

    root = tmp_path_factory.mktemp("lightrag-contract")
    working_dir = root / "rag"
    input_dir = root / "inputs"
    working_dir.mkdir()
    input_dir.mkdir()
    port = _free_port()
    workspace = f"dnx_contract_{uuid.uuid4().hex}"
    api_key = f"contract-{uuid.uuid4().hex}"
    base_url = f"http://127.0.0.1:{port}"
    log_path = root / "server.log"
    process_env = os.environ.copy()
    process_env["INPUT_DIR"] = str(input_dir)
    process_env["WORKING_DIR"] = str(working_dir)
    process_env["LIGHTRAG_API_KEY"] = api_key

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                str(server),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--working-dir",
                str(working_dir),
                "--input-dir",
                str(input_dir),
                "--workspace",
                workspace,
                "--key",
                api_key,
                "--log-level",
                "INFO",
            ],
            cwd=checkout,
            env=process_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 90
            headers = {"X-API-Key": api_key}
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    response = httpx.get(f"{base_url}/health", headers=headers, timeout=2)
                    if response.status_code == 200:
                        health = response.json()
                        assert health["configuration"]["workspace"] == workspace
                        yield RunningLightRAG(base_url=base_url, api_key=api_key)
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.25)
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            pytest.fail(f"LightRAG did not become healthy. Log tail:\n{tail}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _client(server: RunningLightRAG) -> LightRAGClient:
    return LightRAGClient(
        LightRAGConfig(
            base_url=server.base_url,
            api_key=server.api_key,
            timeout_seconds=30,
        )
    )


@pytest.mark.asyncio
async def test_real_health_and_query_data_contract(running_lightrag: RunningLightRAG) -> None:
    client = _client(running_lightrag)
    health = await client.health()
    assert health.status == "healthy"
    assert health.core_version == "1.5.2"
    assert health.api_version == "0308"

    query = await client.query_data("contract probe", mode="bypass")
    assert query.status == "success"
    assert query.data == {
        "entities": [],
        "relationships": [],
        "chunks": [],
        "references": [],
    }
    assert query.metadata["query_mode"] == "bypass"


@pytest.mark.asyncio
async def test_real_upload_tracking_and_delete_contract(running_lightrag: RunningLightRAG) -> None:
    client = _client(running_lightrag)
    upload = await client.upload_document(
        filename=f"ready-contract-{uuid.uuid4().hex}.txt",
        content=b"DeerFlow uploads this non-empty contract document and waits for indexing to finish.",
        content_type="text/plain",
    )
    assert upload.status == "success"
    assert upload.track_id.startswith("upload_")

    deadline = time.monotonic() + 180
    tracking = None
    while time.monotonic() < deadline:
        tracking = await client.get_track_status(upload.track_id)
        if tracking.total_count == 1 and tracking.documents[0]["status"] in {"processed", "failed"}:
            break
        await asyncio.sleep(0.25)
    assert tracking is not None
    assert tracking.total_count == 1
    document = tracking.documents[0]
    assert document["status"] == "processed"
    assert map_lightrag_tracking_status(tracking).status == "ready"

    deletion = await client.delete_documents([document["id"]], delete_file=True)
    assert deletion.status in {"deletion_started", "busy"}
    assert deletion.doc_id == document["id"]
