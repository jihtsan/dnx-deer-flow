"""Strict client for the operator-managed LightRAG data plane.

Only normalized response fields leave this module. Raw LightRAG health data is
never forwarded because it contains filesystem paths and provider endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
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
    content_length: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class LightRAGQueryData:
    status: str
    entities: tuple[dict[str, Any], ...]
    relationships: tuple[dict[str, Any], ...]
    chunks: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]

    @property
    def data(self) -> dict[str, list[dict[str, Any]]]:
        """Compatibility view composed only from normalized fields."""
        return {
            "entities": list(self.entities),
            "relationships": list(self.relationships),
            "chunks": list(self.chunks),
            "references": list(self.references),
        }


@dataclass(frozen=True)
class LightRAGGraph:
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    is_truncated: bool


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


def _optional_string(data: dict[str, Any], field: str, *, error: str) -> str:
    value = data.get(field, "")
    if not isinstance(value, str):
        raise LightRAGContractError(error)
    return value


def _safe_filename(value: str) -> str:
    """Reduce upstream paths to a display filename before crossing the adapter."""
    return PurePosixPath(value.replace("\\", "/")).name


def _string_list(value: Any, *, error: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LightRAGContractError(error)
    return value


QueryMode = Literal["local", "global", "hybrid", "naive", "mix"]
QUERY_MODES: frozenset[str] = frozenset({"local", "global", "hybrid", "naive", "mix"})
LightRAGQueryMode = Literal["local", "global", "hybrid", "naive", "mix", "bypass"]
LIGHTRAG_QUERY_MODES: frozenset[str] = QUERY_MODES | {"bypass"}


class LightRAGClient:
    """Async adapter for the pinned LightRAG FastAPI contract."""

    def __init__(self, config: LightRAGConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not config.base_url:
            raise ValueError("LightRAG base_url is required")
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key.get_secret_value() if config.api_key is not None else None
        self._timeout = config.timeout_seconds
        self._query_timeout = config.query_timeout_seconds
        self._transport = transport

    async def _request_payload(
        self,
        method: str,
        path: str,
        *,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> Any:
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
                response = await client.request(
                    method,
                    path,
                    timeout=self._timeout if timeout_seconds is None else timeout_seconds,
                    **kwargs,
                )
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
        return data

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        data = await self._request_payload(method, path, **kwargs)
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
        raw_documents = _required_list(data, "documents")
        required_document_fields = {
            "id",
            "content_summary",
            "content_length",
            "status",
            "created_at",
            "updated_at",
            "file_path",
        }
        documents: list[dict[str, Any]] = []
        for document in raw_documents:
            if not isinstance(document, dict) or not required_document_fields.issubset(document):
                raise LightRAGContractError("invalid_documents")
            chunks_count = document.get("chunks_count")
            if chunks_count is not None and (not isinstance(chunks_count, int) or chunks_count < 0):
                raise LightRAGContractError("invalid_chunks_count")
            documents.append(
                {
                    "id": _required_string(document, "id"),
                    "status": _required_string(document, "status"),
                    "chunks_count": chunks_count,
                    "updated_at": _required_string(document, "updated_at"),
                }
            )
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

    async def _document_page(self, page: int) -> tuple[tuple[LightRAGRemoteDocument, ...], bool]:
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
        raw_documents = _required_list(data, "documents")
        pagination = _required_dict(data, "pagination")
        documents: list[LightRAGRemoteDocument] = []
        for raw_document in raw_documents:
            if not isinstance(raw_document, dict):
                raise LightRAGContractError("invalid_paginated_document")
            content_length = raw_document.get("content_length")
            if not isinstance(content_length, int) or content_length < 0:
                raise LightRAGContractError("invalid_content_length")
            documents.append(
                LightRAGRemoteDocument(
                    id=_required_string(raw_document, "id"),
                    track_id=_required_string(raw_document, "track_id"),
                    status=_required_string(raw_document, "status"),
                    file_path=_required_string(raw_document, "file_path"),
                    content_length=content_length,
                    created_at=_required_string(raw_document, "created_at"),
                    updated_at=_required_string(raw_document, "updated_at"),
                )
            )
        returned_page = pagination.get("page")
        has_next = pagination.get("has_next")
        if not isinstance(returned_page, int) or returned_page != page or not isinstance(has_next, bool):
            raise LightRAGContractError("invalid_pagination")
        return tuple(documents), has_next

    async def list_documents(self) -> tuple[LightRAGRemoteDocument, ...]:
        """Return normalized metadata for every document in the configured workspace."""
        page = 1
        documents: list[LightRAGRemoteDocument] = []
        while True:
            page_documents, has_next = await self._document_page(page)
            documents.extend(page_documents)
            if not has_next:
                return tuple(documents)
            page += 1

    async def find_document_by_filename(self, filename: str) -> LightRAGRemoteDocument | None:
        """Reconcile a stable upload filename through LightRAG's paginated API."""
        page = 1
        while True:
            documents, has_next = await self._document_page(page)
            for document in documents:
                if document.file_path != filename:
                    continue
                return document
            if not has_next:
                return None
            page += 1

    async def popular_graph_labels(self, *, limit: int) -> tuple[str, ...]:
        """Return the most connected graph labels without upstream metadata."""
        data = await self._request_payload("GET", "/graph/label/popular", params={"limit": limit})
        return tuple(_string_list(data, error="invalid_graph_labels"))

    async def all_graph_labels(self) -> tuple[str, ...]:
        """Return every graph label in the configured LightRAG workspace."""
        data = await self._request_payload("GET", "/graph/label/list")
        return tuple(_string_list(data, error="invalid_graph_labels"))

    async def search_graph_labels(self, query: str, *, limit: int) -> tuple[str, ...]:
        """Return graph labels matching an operator-provided search term."""
        data = await self._request_payload(
            "GET",
            "/graph/label/search",
            params={"q": query, "limit": limit},
        )
        return tuple(_string_list(data, error="invalid_graph_labels"))

    async def connected_graph(self, label: str, *, max_depth: int, max_nodes: int) -> LightRAGGraph:
        """Return a connected graph with only UI-safe node and edge fields."""
        response = await self._request_json(
            "GET",
            "/graphs",
            params={"label": label, "max_depth": max_depth, "max_nodes": max_nodes},
        )
        raw_nodes = _required_list(response, "nodes")
        raw_edges = _required_list(response, "edges")
        is_truncated = response.get("is_truncated")
        if not isinstance(is_truncated, bool):
            raise LightRAGContractError("invalid_graph_truncation")

        nodes: list[dict[str, Any]] = []
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                raise LightRAGContractError("invalid_graph_node")
            properties = raw_node.get("properties", {})
            labels = raw_node.get("labels", [])
            if not isinstance(properties, dict) or not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
                raise LightRAGContractError("invalid_graph_node")
            node_id = _required_string(raw_node, "id")
            file_path = _optional_string(properties, "file_path", error="invalid_graph_node")
            nodes.append(
                {
                    "id": node_id,
                    "label": labels[0] if labels else node_id,
                    "entity_type": _optional_string(properties, "entity_type", error="invalid_graph_node"),
                    "description": _optional_string(properties, "description", error="invalid_graph_node"),
                    "file_path": _safe_filename(file_path),
                }
            )

        edges: list[dict[str, Any]] = []
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, dict):
                raise LightRAGContractError("invalid_graph_edge")
            properties = raw_edge.get("properties", {})
            if not isinstance(properties, dict):
                raise LightRAGContractError("invalid_graph_edge")
            weight = properties.get("weight", 0.0)
            if isinstance(weight, bool) or not isinstance(weight, int | float):
                raise LightRAGContractError("invalid_graph_edge")
            file_path = _optional_string(properties, "file_path", error="invalid_graph_edge")
            edges.append(
                {
                    "id": _required_string(raw_edge, "id"),
                    "source": _required_string(raw_edge, "source"),
                    "target": _required_string(raw_edge, "target"),
                    "relation_type": _optional_string(raw_edge, "type", error="invalid_graph_edge"),
                    "description": _optional_string(properties, "description", error="invalid_graph_edge"),
                    "keywords": _optional_string(properties, "keywords", error="invalid_graph_edge"),
                    "weight": float(weight),
                    "file_path": _safe_filename(file_path),
                }
            )
        return LightRAGGraph(nodes=tuple(nodes), edges=tuple(edges), is_truncated=is_truncated)

    async def query_data(
        self,
        query: str,
        *,
        mode: LightRAGQueryMode = "mix",
        top_k: int | None = None,
        chunk_top_k: int | None = None,
        max_total_tokens: int | None = None,
    ) -> LightRAGQueryData:
        if mode not in LIGHTRAG_QUERY_MODES:
            raise ValueError("unsupported query mode")
        body: dict[str, Any] = {"query": query, "mode": mode}
        for name, value in (
            ("top_k", top_k),
            ("chunk_top_k", chunk_top_k),
            ("max_total_tokens", max_total_tokens),
        ):
            if value is not None:
                body[name] = value
        response = await self._request_json(
            "POST",
            "/query/data",
            timeout_seconds=self._query_timeout,
            json=body,
        )
        data = _required_dict(response, "data")
        for field in ("entities", "relationships", "chunks", "references"):
            _required_list(data, field)
        entities = tuple(self._normalize_query_entity(item) for item in data["entities"])
        relationships = tuple(self._normalize_query_relationship(item) for item in data["relationships"])
        chunks = tuple(self._normalize_query_chunk(item) for item in data["chunks"])
        references = tuple(self._normalize_query_reference(item) for item in data["references"])
        metadata = self._normalize_query_metadata(
            _required_dict(response, "metadata"),
            mode=mode,
            entity_count=len(entities),
            relationship_count=len(relationships),
            chunk_count=len(chunks),
        )
        status = _required_string(response, "status")
        if status != "success":
            raise LightRAGContractError("query_failed")
        return LightRAGQueryData(
            status=status,
            entities=entities,
            relationships=relationships,
            chunks=chunks,
            references=references,
            metadata=metadata,
        )

    @staticmethod
    def _normalize_query_entity(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LightRAGContractError("invalid_query_entity")
        file_path = _optional_string(value, "file_path", error="invalid_query_entity")
        return {
            "entity_name": _required_string(value, "entity_name"),
            "entity_type": _optional_string(value, "entity_type", error="invalid_query_entity"),
            "description": _optional_string(value, "description", error="invalid_query_entity"),
            "file_path": _safe_filename(file_path),
            "reference_id": _optional_string(value, "reference_id", error="invalid_query_entity"),
        }

    @staticmethod
    def _normalize_query_relationship(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LightRAGContractError("invalid_query_relationship")
        weight = value.get("weight", 0.0)
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise LightRAGContractError("invalid_query_relationship")
        file_path = _optional_string(value, "file_path", error="invalid_query_relationship")
        return {
            "src_id": _required_string(value, "src_id"),
            "tgt_id": _required_string(value, "tgt_id"),
            "description": _optional_string(value, "description", error="invalid_query_relationship"),
            "keywords": _optional_string(value, "keywords", error="invalid_query_relationship"),
            "weight": float(weight),
            "file_path": _safe_filename(file_path),
            "reference_id": _optional_string(value, "reference_id", error="invalid_query_relationship"),
        }

    @staticmethod
    def _normalize_query_chunk(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LightRAGContractError("invalid_query_chunk")
        file_path = _optional_string(value, "file_path", error="invalid_query_chunk")
        return {
            "chunk_id": _required_string(value, "chunk_id"),
            "content": _required_string(value, "content"),
            "file_path": _safe_filename(file_path),
            "reference_id": _optional_string(value, "reference_id", error="invalid_query_chunk"),
        }

    @staticmethod
    def _normalize_query_reference(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise LightRAGContractError("invalid_query_reference")
        file_path = _optional_string(value, "file_path", error="invalid_query_reference")
        return {
            "reference_id": _required_string(value, "reference_id"),
            "file_path": _safe_filename(file_path),
        }

    @staticmethod
    def _normalize_query_metadata(
        value: dict[str, Any],
        *,
        mode: LightRAGQueryMode,
        entity_count: int,
        relationship_count: int,
        chunk_count: int,
    ) -> dict[str, Any]:
        query_mode = _required_string(value, "query_mode")
        if query_mode != mode:
            raise LightRAGContractError("invalid_query_mode")
        keywords = value.get("keywords", {})
        if not isinstance(keywords, dict):
            raise LightRAGContractError("invalid_query_keywords")
        high_level = _string_list(keywords.get("high_level", []), error="invalid_query_keywords")
        low_level = _string_list(keywords.get("low_level", []), error="invalid_query_keywords")
        processing = value.get("processing_info", {})
        if not isinstance(processing, dict):
            raise LightRAGContractError("invalid_query_processing_info")
        defaults = {
            "total_entities_found": entity_count,
            "total_relations_found": relationship_count,
            "entities_after_truncation": entity_count,
            "relations_after_truncation": relationship_count,
            "final_chunks_count": chunk_count,
        }
        normalized_processing: dict[str, int] = {}
        for field, default in defaults.items():
            field_value = processing.get(field, default)
            if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 0:
                raise LightRAGContractError("invalid_query_processing_info")
            normalized_processing[field] = field_value
        return {
            "query_mode": query_mode,
            "keywords": {"high_level": high_level, "low_level": low_level},
            "processing_info": normalized_processing,
        }

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
