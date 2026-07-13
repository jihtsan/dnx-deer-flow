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
        lambda: client.query_data("probe", mode="bypass"),
        lambda: client.delete_documents(["doc-probe"]),
    ]
    for operation in operations:
        with pytest.raises(LightRAGOfflineError):
            await operation()
