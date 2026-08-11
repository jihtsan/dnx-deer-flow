import httpx
import pytest

from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGClient,
    LightRAGConflictError,
    LightRAGConnectionError,
    LightRAGContractError,
    LightRAGOfflineError,
    LightRAGRateLimitError,
    LightRAGRequestRejectedError,
    LightRAGServerError,
    LightRAGTimeoutError,
)
from deerflow.config.knowledge_base_config import LightRAGConfig


def _client(handler: httpx.AsyncBaseTransport) -> LightRAGClient:
    return LightRAGClient(
        LightRAGConfig(base_url="http://lightrag.invalid", api_key="do-not-leak", timeout_seconds=0.1),
        transport=handler,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError("no route"), httpx.ReadTimeout("slow")])
async def test_health_maps_network_failures_to_offline_without_leaking_config(failure: Exception) -> None:
    async def fail(_request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(LightRAGOfflineError) as exc_info:
        await _client(httpx.MockTransport(fail)).health()

    message = str(exc_info.value)
    assert "lightrag.invalid" not in message
    assert "do-not-leak" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_type"),
    [
        (httpx.ConnectError("no route"), LightRAGConnectionError),
        (httpx.ReadTimeout("slow"), LightRAGTimeoutError),
    ],
)
async def test_network_failures_keep_stable_retry_classification(
    failure: Exception,
    expected_type: type[Exception],
) -> None:
    async def fail(_request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(expected_type):
        await _client(httpx.MockTransport(fail)).health()


@pytest.mark.asyncio
async def test_health_maps_5xx_to_offline() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, json={"detail": "private upstream"}))

    with pytest.raises(LightRAGOfflineError):
        await _client(transport).health()

    with pytest.raises(LightRAGServerError):
        await _client(transport).health()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429])
async def test_health_maps_temporarily_unavailable_statuses_to_offline(status_code: int) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status_code, json={"detail": "retry later"}))

    with pytest.raises(LightRAGOfflineError) as exc_info:
        await _client(transport).health()

    assert str(exc_info.value) == "temporarily_unavailable"
    assert isinstance(
        exc_info.value,
        LightRAGRateLimitError if status_code == 429 else LightRAGTimeoutError,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_health_preserves_authentication_failure_without_leaking_response(status_code: int) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status_code, json={"detail": "private authentication detail"}))

    with pytest.raises(LightRAGAuthenticationError) as exc_info:
        await _client(transport).health()

    assert str(exc_info.value) == "authentication_failed"
    assert "private authentication detail" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404, 405, 422])
async def test_health_maps_contract_level_4xx_to_incompatible(status_code: int) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status_code, json={"detail": "private detail"}))

    with pytest.raises(LightRAGRequestRejectedError) as exc_info:
        await _client(transport).health()

    assert str(exc_info.value) == "request_rejected"
    assert "private detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_upload_conflict_is_distinct_for_filename_reconciliation() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(409, json={"detail": "private conflict"}))

    with pytest.raises(LightRAGConflictError) as exc_info:
        await _client(transport).upload_document(filename="doc-stable.txt", content=b"body", content_type="text/plain")

    assert str(exc_info.value) == "conflict"
    assert "private conflict" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_find_document_by_filename_pages_until_the_stable_upload_name_matches() -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        requests.append(body)
        page = body["page"]
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "id": f"remote-{page}",
                        "content_summary": "",
                        "content_length": 1,
                        "status": "pending",
                        "created_at": "2026-07-13T00:00:00Z",
                        "updated_at": "2026-07-13T00:00:00Z",
                        "track_id": f"track-{page}",
                        "file_path": "other.txt" if page == 1 else "doc-stable.txt",
                    }
                ],
                "pagination": {
                    "page": page,
                    "page_size": 200,
                    "total_count": 2,
                    "total_pages": 2,
                    "has_next": page == 1,
                    "has_prev": page == 2,
                },
                "status_counts": {"PENDING": 2},
            },
        )

    found = await _client(httpx.MockTransport(handler)).find_document_by_filename("doc-stable.txt")

    assert found is not None
    assert found.track_id == "track-2"
    assert [request["page"] for request in requests] == [1, 2]


@pytest.mark.asyncio
async def test_list_documents_pages_all_remote_metadata_without_exposing_content_summary() -> None:
    requested_pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        page = body["page"]
        requested_pages.append(page)
        return httpx.Response(
            200,
            json={
                "documents": [
                    {
                        "id": f"remote-{page}",
                        "content_summary": "private document excerpt",
                        "content_length": page * 10,
                        "status": "processed" if page == 1 else "processing",
                        "created_at": f"2026-07-0{page}T00:00:00Z",
                        "updated_at": f"2026-07-0{page}T01:00:00Z",
                        "track_id": f"track-{page}",
                        "file_path": f"document-{page}.txt",
                    }
                ],
                "pagination": {
                    "page": page,
                    "page_size": 200,
                    "total_count": 2,
                    "total_pages": 2,
                    "has_next": page == 1,
                    "has_prev": page == 2,
                },
                "status_counts": {"processed": 1, "processing": 1},
            },
        )

    documents = await _client(httpx.MockTransport(handler)).list_documents()

    assert requested_pages == [1, 2]
    assert [document.id for document in documents] == ["remote-1", "remote-2"]
    assert documents[0].file_path == "document-1.txt"
    assert documents[0].content_length == 10
    assert documents[0].created_at == "2026-07-01T00:00:00Z"
    assert documents[0].updated_at == "2026-07-01T01:00:00Z"
    assert not hasattr(documents[0], "content_summary")


@pytest.mark.asyncio
async def test_health_maps_invalid_json_to_incompatible_without_echoing_body() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"secret-invalid-json"))

    with pytest.raises(LightRAGContractError) as exc_info:
        await _client(transport).health()

    assert "secret-invalid-json" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_health_maps_missing_fields_to_incompatible() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"status": "healthy"}))

    with pytest.raises(LightRAGContractError):
        await _client(transport).health()


@pytest.mark.asyncio
async def test_health_requires_pipeline_active_field() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"status": "healthy", "core_version": "1.5.2", "api_version": "0308"},
        )
    )

    with pytest.raises(LightRAGContractError) as exc_info:
        await _client(transport).health()

    assert str(exc_info.value) == "missing_pipeline_active"


@pytest.mark.asyncio
async def test_adapter_never_sends_workspace_header() -> None:
    async def inspect(request: httpx.Request) -> httpx.Response:
        assert "LIGHTRAG-WORKSPACE" not in request.headers
        raise httpx.ConnectError("contract probe", request=request)

    client = _client(httpx.MockTransport(inspect))
    operations = [
        lambda: client.health(),
        lambda: client.upload_document(filename="probe.txt", content=b"", content_type="text/plain"),
        lambda: client.get_track_status("track-probe"),
        lambda: client.all_graph_labels(),
        lambda: client.popular_graph_labels(limit=10),
        lambda: client.search_graph_labels("probe", limit=10),
        lambda: client.connected_graph("probe", max_depth=2, max_nodes=10),
        lambda: client.query_data("probe", mode="mix"),
        lambda: client.delete_documents(["doc-probe"]),
    ]
    for operation in operations:
        with pytest.raises(LightRAGOfflineError):
            await operation()


@pytest.mark.asyncio
async def test_popular_graph_labels_requires_a_string_list() -> None:
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(200, json=["Alpha", "Beta"])))

    assert await client.popular_graph_labels(limit=2) == ("Alpha", "Beta")

    invalid = _client(httpx.MockTransport(lambda _request: httpx.Response(200, json=["Alpha", 3])))
    with pytest.raises(LightRAGContractError) as exc_info:
        await invalid.popular_graph_labels(limit=2)
    assert str(exc_info.value) == "invalid_graph_labels"


@pytest.mark.asyncio
async def test_tracking_status_exposes_only_progress_fields() -> None:
    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "track_id": "track-progress",
                    "documents": [
                        {
                            "id": "doc-progress",
                            "content_summary": "private summary",
                            "content_length": 42,
                            "status": "processing",
                            "chunks_count": 5,
                            "created_at": "2026-07-15T01:00:00Z",
                            "updated_at": "2026-07-15T01:02:00Z",
                            "file_path": "/private/workspace/product.md",
                            "metadata": {"secret": "must not escape"},
                        }
                    ],
                    "total_count": 1,
                    "status_summary": {"processing": 1},
                },
            )
        )
    )

    tracking = await client.get_track_status("track-progress")

    assert tracking.documents == (
        {
            "id": "doc-progress",
            "status": "processing",
            "chunks_count": 5,
            "updated_at": "2026-07-15T01:02:00Z",
        },
    )


@pytest.mark.asyncio
async def test_all_and_search_graph_labels_use_strict_string_list_contracts() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/graph/label/list":
            assert not request.url.params
            return httpx.Response(200, json=["Alpha", "Beta"])
        assert request.url.path == "/graph/label/search"
        assert dict(request.url.params) == {"q": "alp", "limit": "7"}
        return httpx.Response(200, json=["Alpha"])

    client = _client(httpx.MockTransport(handler))

    assert await client.all_graph_labels() == ("Alpha", "Beta")
    assert await client.search_graph_labels("alp", limit=7) == ("Alpha",)
    assert len(requests) == 2

    invalid = _client(httpx.MockTransport(lambda _request: httpx.Response(200, json={"labels": ["Alpha"]})))
    with pytest.raises(LightRAGContractError) as all_exc:
        await invalid.all_graph_labels()
    assert str(all_exc.value) == "invalid_graph_labels"
    with pytest.raises(LightRAGContractError) as search_exc:
        await invalid.search_graph_labels("alp", limit=7)
    assert str(search_exc.value) == "invalid_graph_labels"


@pytest.mark.asyncio
async def test_connected_graph_returns_only_normalized_fields_and_safe_filenames() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"label": "Alpha", "max_depth": "2", "max_nodes": "10"}
        return httpx.Response(
            200,
            json={
                "nodes": [
                    {
                        "id": "Alpha",
                        "labels": ["Alpha"],
                        "properties": {
                            "entity_id": "Alpha",
                            "entity_type": "concept",
                            "description": "A normalized node",
                            "file_path": "/private/workspace/document.txt",
                            "source_id": "private-source-id",
                            "provider_endpoint": "https://secret.invalid",
                        },
                    }
                ],
                "edges": [
                    {
                        "id": "Alpha-Beta",
                        "type": "DIRECTED",
                        "source": "Alpha",
                        "target": "Beta",
                        "properties": {
                            "description": "connects",
                            "keywords": "related",
                            "weight": 0.75,
                            "file_path": "C:\\private\\relation.txt",
                            "api_key": "secret",
                        },
                    }
                ],
                "is_truncated": False,
                "working_dir": "/private/workspace",
            },
        )

    graph = await _client(httpx.MockTransport(handler)).connected_graph("Alpha", max_depth=2, max_nodes=10)

    assert graph.nodes == (
        {
            "id": "Alpha",
            "label": "Alpha",
            "entity_type": "concept",
            "description": "A normalized node",
            "file_path": "document.txt",
        },
    )
    assert graph.edges == (
        {
            "id": "Alpha-Beta",
            "source": "Alpha",
            "target": "Beta",
            "relation_type": "DIRECTED",
            "description": "connects",
            "keywords": "related",
            "weight": 0.75,
            "file_path": "relation.txt",
        },
    )
    assert graph.is_truncated is False


@pytest.mark.asyncio
async def test_query_data_normalizes_known_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query/data"
        assert request.extensions["timeout"]["read"] == 60.0
        return httpx.Response(
            200,
            json={
                "status": "success",
                "message": "private upstream message",
                "data": {
                    "entities": [
                        {
                            "entity_name": "Alpha",
                            "entity_type": "concept",
                            "description": "Entity description",
                            "source_id": "private-source",
                            "file_path": "/workspace/entity.txt",
                            "reference_id": "1",
                            "raw_provider": "secret",
                        }
                    ],
                    "relationships": [
                        {
                            "src_id": "Alpha",
                            "tgt_id": "Beta",
                            "description": "connects",
                            "keywords": "related",
                            "weight": 1,
                            "source_id": "private-source",
                            "file_path": "/workspace/relation.txt",
                            "reference_id": "2",
                        }
                    ],
                    "chunks": [
                        {
                            "content": "Relevant text",
                            "file_path": "/workspace/chunk.txt",
                            "chunk_id": "chunk-1",
                            "reference_id": "3",
                        }
                    ],
                    "references": [{"reference_id": "3", "file_path": "/workspace/chunk.txt"}],
                },
                "metadata": {
                    "query_mode": "mix",
                    "keywords": {"high_level": ["energy"], "low_level": ["storage"]},
                    "processing_info": {
                        "total_entities_found": 2,
                        "total_relations_found": 1,
                        "entities_after_truncation": 1,
                        "relations_after_truncation": 1,
                        "final_chunks_count": 1,
                    },
                    "working_dir": "/private/workspace",
                },
            },
        )

    result = await _client(httpx.MockTransport(handler)).query_data("energy storage", mode="mix")

    assert result.entities[0]["file_path"] == "entity.txt"
    assert result.relationships[0]["weight"] == 1.0
    assert result.chunks[0]["file_path"] == "chunk.txt"
    assert result.references == ({"reference_id": "3", "file_path": "chunk.txt"},)
    assert result.metadata["query_mode"] == "mix"
    assert "working_dir" not in result.metadata
    assert result.data["entities"][0]["file_path"] == "entity.txt"
    assert "message" not in result.__dict__


@pytest.mark.asyncio
async def test_query_data_rejects_a_failure_payload_without_exposing_its_message() -> None:
    response = {
        "status": "failure",
        "message": "private provider failure",
        "data": {"entities": [], "relationships": [], "chunks": [], "references": []},
        "metadata": {"query_mode": "mix"},
    }
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(200, json=response)))

    with pytest.raises(LightRAGContractError) as exc_info:
        await client.query_data("energy storage", mode="mix")

    assert str(exc_info.value) == "query_failed"
    assert "private provider failure" not in str(exc_info.value)
