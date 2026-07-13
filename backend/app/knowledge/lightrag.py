"""Strict client for the operator-managed LightRAG data plane.

Only normalized response fields leave this module. Raw LightRAG health data is
never forwarded because it contains filesystem paths and provider endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from deerflow.config.knowledge_base_config import LightRAGConfig


class LightRAGError(RuntimeError):
    """Base error whose message is a stable, non-sensitive reason code."""


class LightRAGOfflineError(LightRAGError):
    """The configured LightRAG service could not serve the request."""


class LightRAGTimeoutError(LightRAGOfflineError):
    """LightRAG timed out before returning a response."""


class LightRAGConnectionError(LightRAGOfflineError):
    """A connection to LightRAG could not be established."""


class LightRAGServerError(LightRAGOfflineError):
    """LightRAG returned a transient server-side failure."""


class LightRAGRateLimitError(LightRAGOfflineError):
    """LightRAG temporarily rate-limited the request."""


class LightRAGAuthenticationError(LightRAGError):
    """LightRAG is reachable, but rejected the configured API key."""


class LightRAGContractError(LightRAGError):
    """LightRAG responded, but its JSON did not match the pinned contract."""


class LightRAGRequestRejectedError(LightRAGContractError):
    """LightRAG permanently rejected the submitted request."""


class LightRAGConflictError(LightRAGError):
    """LightRAG reported a conflict that may require reconciliation."""


@dataclass(frozen=True)
class LightRAGHealth:
    status: str
    core_version: str
    api_version: str
    pipeline_active: bool


@dataclass(frozen=True)
class LightRAGUpload:
    status: str
    message: str
    track_id: str


@dataclass(frozen=True)
class LightRAGTrackStatus:
    track_id: str
    documents: tuple[dict[str, Any], ...]
    total_count: int
    status_summary: dict[str, int]


@dataclass(frozen=True)
class LightRAGRemoteDocument:
    id: str
    track_id: str
    status: str
    file_path: str


@dataclass(frozen=True)
class LightRAGQueryData:
    status: str
    message: str
    data: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LightRAGDelete:
    status: str
    message: str
    doc_id: str


def _required_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise LightRAGContractError(f"missing_{field}")
    return value


def _required_dict(data: dict[str, Any], field: str) -> dict[str, Any]:
    value = data.get(field)
    if not isinstance(value, dict):
        raise LightRAGContractError(f"missing_{field}")
    return value


def _required_list(data: dict[str, Any], field: str) -> list[Any]:
    value = data.get(field)
    if not isinstance(value, list):
        raise LightRAGContractError(f"missing_{field}")
    return value


class LightRAGClient:
    """Async adapter for the pinned LightRAG FastAPI contract."""

    def __init__(self, config: LightRAGConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not config.base_url:
            raise ValueError("LightRAG base_url is required")
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key.get_secret_value() if config.api_key is not None else None
        self._timeout = config.timeout_seconds
        self._transport = transport

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LightRAGTimeoutError("timeout") from exc
        except httpx.TransportError as exc:
            raise LightRAGConnectionError("connection_failed") from exc

        if response.status_code >= 500:
            raise LightRAGServerError("server_error")
        if response.status_code == 408:
            raise LightRAGTimeoutError("temporarily_unavailable")
        if response.status_code == 429:
            raise LightRAGRateLimitError("temporarily_unavailable")
        if response.status_code in {401, 403}:
            raise LightRAGAuthenticationError("authentication_failed")
        if response.status_code == 409:
            raise LightRAGConflictError("conflict")
        if not response.is_success:
            raise LightRAGRequestRejectedError("request_rejected")
        try:
            data = response.json()
        except ValueError as exc:
            raise LightRAGContractError("invalid_json") from exc
        if not isinstance(data, dict):
            raise LightRAGContractError("invalid_root")
        return data

    async def health(self) -> LightRAGHealth:
        data = await self._request_json("GET", "/health")
        status = _required_string(data, "status")
        core_version = _required_string(data, "core_version")
        api_version = _required_string(data, "api_version").removesuffix("⚠️").strip()
        if "pipeline_active" not in data:
            raise LightRAGContractError("missing_pipeline_active")
        pipeline_active = data["pipeline_active"]
        if not isinstance(pipeline_active, bool):
            raise LightRAGContractError("invalid_pipeline_active")
        return LightRAGHealth(
            status=status,
            core_version=core_version,
            api_version=api_version,
            pipeline_active=pipeline_active,
        )

    async def upload_document(self, *, filename: str, content: bytes, content_type: str) -> LightRAGUpload:
        data = await self._request_json(
            "POST",
            "/documents/upload",
            files={"file": (filename, content, content_type)},
        )
        return LightRAGUpload(
            status=_required_string(data, "status"),
            message=_required_string(data, "message"),
            track_id=_required_string(data, "track_id"),
        )

    async def get_track_status(self, track_id: str) -> LightRAGTrackStatus:
        data = await self._request_json("GET", f"/documents/track_status/{track_id}")
        documents = _required_list(data, "documents")
        required_document_fields = {
            "id",
            "content_summary",
            "content_length",
            "status",
            "created_at",
            "updated_at",
            "file_path",
        }
        if any(not isinstance(document, dict) or not required_document_fields.issubset(document) for document in documents):
            raise LightRAGContractError("invalid_documents")
        total_count = data.get("total_count")
        status_summary = data.get("status_summary")
        if not isinstance(total_count, int) or not isinstance(status_summary, dict):
            raise LightRAGContractError("invalid_track_summary")
        if any(not isinstance(key, str) or not isinstance(value, int) for key, value in status_summary.items()):
            raise LightRAGContractError("invalid_status_summary")
        return LightRAGTrackStatus(
            track_id=_required_string(data, "track_id"),
            documents=tuple(documents),
            total_count=total_count,
            status_summary=status_summary,
        )

    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        """Reconcile a stable upload filename through LightRAG's paginated API."""
        page = 1
        while True:
            data = await self._request_json(
                "POST",
                "/documents/paginated",
                json={
                    "page": page,
                    "page_size": 200,
                    "sort_field": "updated_at",
                    "sort_direction": "desc",
                },
            )
            documents = _required_list(data, "documents")
            pagination = _required_dict(data, "pagination")
            for document in documents:
                if not isinstance(document, dict):
                    raise LightRAGContractError("invalid_paginated_document")
                file_path = document.get("file_path")
                if file_path != filename:
                    continue
                return LightRAGRemoteDocument(
                    id=_required_string(document, "id"),
                    track_id=_required_string(document, "track_id"),
                    status=_required_string(document, "status"),
                    file_path=file_path,
                )
            has_next = pagination.get("has_next")
            if not isinstance(has_next, bool):
                raise LightRAGContractError("invalid_pagination")
            if not has_next:
                return None
            returned_page = pagination.get("page")
            if not isinstance(returned_page, int) or returned_page != page:
                raise LightRAGContractError("invalid_pagination")
            page += 1

    async def query_data(
        self,
        query: str,
        *,
        mode: Literal["local", "global", "hybrid", "naive", "mix", "bypass"] = "mix",
        top_k: int | None = None,
        chunk_top_k: int | None = None,
        max_total_tokens: int | None = None,
    ) -> LightRAGQueryData:
        body: dict[str, Any] = {"query": query, "mode": mode}
        for name, value in (
            ("top_k", top_k),
            ("chunk_top_k", chunk_top_k),
            ("max_total_tokens", max_total_tokens),
        ):
            if value is not None:
                body[name] = value
        response = await self._request_json("POST", "/query/data", json=body)
        data = _required_dict(response, "data")
        for field in ("entities", "relationships", "chunks", "references"):
            _required_list(data, field)
        return LightRAGQueryData(
            status=_required_string(response, "status"),
            message=_required_string(response, "message"),
            data=data,
            metadata=_required_dict(response, "metadata"),
        )

    async def delete_documents(
        self,
        doc_ids: list[str],
        *,
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> LightRAGDelete:
        response = await self._request_json(
            "DELETE",
            "/documents/delete_document",
            json={
                "doc_ids": doc_ids,
                "delete_file": delete_file,
                "delete_llm_cache": delete_llm_cache,
            },
        )
        return LightRAGDelete(
            status=_required_string(response, "status"),
            message=_required_string(response, "message"),
            doc_id=_required_string(response, "doc_id"),
        )
