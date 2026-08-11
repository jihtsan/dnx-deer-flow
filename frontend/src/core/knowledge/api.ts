import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  KnowledgeBaseDiagnostics,
  KnowledgeBaseFeature,
  KnowledgeBaseStatus,
  KnowledgeDirectoriesEnvelope,
  KnowledgeDirectory,
  KnowledgeDirectoryCreateInput,
  KnowledgeDocument,
  KnowledgeDocumentProgress,
  KnowledgeDocumentProgressStage,
  KnowledgeDocumentAccepted,
  KnowledgeDocumentsEnvelope,
  KnowledgeDocumentStatus,
  KnowledgeIngestionDiagnostics,
  KnowledgeIngestionJobStatus,
  KnowledgeDocumentStats,
  KnowledgeGraphEdge,
  KnowledgeGraphEnvelope,
  KnowledgeGlobalGraphEnvelope,
  KnowledgeGraphLabelsEnvelope,
  KnowledgeGraphNode,
  KnowledgeRetrievalChunk,
  KnowledgeRetrievalEntity,
  KnowledgeRetrievalInput,
  KnowledgeRetrievalMetadata,
  KnowledgeRetrievalMode,
  KnowledgeRetrievalReference,
  KnowledgeRetrievalRelationship,
  KnowledgeRetrievalResponse,
  KnowledgeScope,
  KnowledgeScopeCreateInput,
  KnowledgeScopeEnvelope,
  KnowledgeScopeUpdateInput,
} from "./types";

const KNOWLEDGE_BASE_STATUSES = new Set<KnowledgeBaseStatus>([
  "unconfigured",
  "disabled",
  "offline",
  "incompatible",
  "ready",
]);

const KNOWLEDGE_DOCUMENT_STATUSES = new Set<KnowledgeDocumentStatus>([
  "pending",
  "indexing",
  "ready",
  "failed",
]);

const KNOWLEDGE_INGESTION_JOB_STATUSES = new Set<KnowledgeIngestionJobStatus>([
  "pending",
  "leased",
  "retry_wait",
  "succeeded",
  "dead",
  "cancelled",
]);

const KNOWLEDGE_DOCUMENT_PROGRESS_STAGES =
  new Set<KnowledgeDocumentProgressStage>([
    "pending",
    "parsing",
    "analyzing",
    "processing",
    "preprocessed",
    "processed",
    "failed",
  ]);

const KNOWLEDGE_RETRIEVAL_MODES = new Set<KnowledgeRetrievalMode>([
  "local",
  "global",
  "hybrid",
  "naive",
  "mix",
]);

export const KNOWLEDGE_GRAPH_LABEL_LIMIT = 20;
export const KNOWLEDGE_GLOBAL_GRAPH_MAX_NODES = 5000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isOptionalString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isOptionalNumber(value: unknown): value is number | null {
  return typeof value === "number" || value === null;
}

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function boundedInteger(
  name: string,
  value: number,
  minimum: number,
  maximum: number,
): number {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new RangeError(
      `${name} must be an integer between ${minimum} and ${maximum}`,
    );
  }
  return value;
}

function boundedText(
  name: string,
  value: string,
  minimum: number,
  maximum: number,
): string {
  const normalized = value.trim();
  if (normalized.length < minimum || normalized.length > maximum) {
    throw new RangeError(
      `${name} must contain between ${minimum} and ${maximum} characters`,
    );
  }
  return normalized;
}

function parseDiagnostics(value: unknown): KnowledgeBaseDiagnostics | null {
  if (!isRecord(value)) return null;
  if (
    value.workspace_mode !== "single" ||
    typeof value.expected_tag !== "string" ||
    typeof value.expected_commit !== "string" ||
    typeof value.expected_core_version !== "string" ||
    typeof value.expected_api_version !== "string" ||
    !isOptionalString(value.observed_core_version) ||
    !isOptionalString(value.observed_api_version) ||
    !isOptionalString(value.service_status)
  ) {
    return null;
  }
  return {
    workspace_mode: value.workspace_mode,
    expected_tag: value.expected_tag,
    expected_commit: value.expected_commit,
    expected_core_version: value.expected_core_version,
    expected_api_version: value.expected_api_version,
    observed_core_version: value.observed_core_version,
    observed_api_version: value.observed_api_version,
    service_status: value.service_status,
  };
}

export function parseKnowledgeBaseFeature(
  value: unknown,
): KnowledgeBaseFeature {
  if (!isRecord(value)) {
    throw new Error("Invalid knowledge-base feature response");
  }
  const status = value.status;
  const diagnostics = parseDiagnostics(value.diagnostics);
  if (
    typeof value.enabled !== "boolean" ||
    typeof status !== "string" ||
    !KNOWLEDGE_BASE_STATUSES.has(status as KnowledgeBaseStatus) ||
    typeof value.reason !== "string" ||
    diagnostics === null
  ) {
    throw new Error("Invalid knowledge-base feature response");
  }
  return {
    enabled: value.enabled,
    status: status as KnowledgeBaseStatus,
    reason: value.reason,
    diagnostics,
  };
}

function parseDocumentStats(value: unknown): KnowledgeDocumentStats | null {
  if (!isRecord(value)) return null;
  const keys = ["total", "pending", "indexing", "ready", "failed"] as const;
  if (keys.some((key) => typeof value[key] !== "number")) return null;
  return {
    total: value.total as number,
    pending: value.pending as number,
    indexing: value.indexing as number,
    ready: value.ready as number,
    failed: value.failed as number,
  };
}

function parseDocument(value: unknown): KnowledgeDocument | null {
  if (!isRecord(value)) return null;
  const status = value.status;
  if (
    typeof value.id !== "string" ||
    !isOptionalString(value.directory_id) ||
    typeof value.original_filename !== "string" ||
    typeof value.content_type !== "string" ||
    !isOptionalNumber(value.size_bytes) ||
    !isOptionalNumber(value.content_length) ||
    typeof status !== "string" ||
    !KNOWLEDGE_DOCUMENT_STATUSES.has(status as KnowledgeDocumentStatus) ||
    !isOptionalString(value.lightrag_tracking_id) ||
    !isOptionalString(value.failure_code) ||
    !isOptionalString(value.failure_reason) ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    !isOptionalString(value.completed_at)
  ) {
    return null;
  }
  const progress = parseDocumentProgress(value.progress);
  if (progress === undefined) return null;
  const common = {
    id: value.id,
    directory_id: value.directory_id,
    original_filename: value.original_filename,
    content_type: value.content_type,
    status: status as KnowledgeDocumentStatus,
    lightrag_tracking_id: value.lightrag_tracking_id,
    failure_code: value.failure_code,
    failure_reason: value.failure_reason,
    created_at: value.created_at,
    updated_at: value.updated_at,
    completed_at: value.completed_at,
    progress,
  };
  if (
    value.source === "managed" &&
    value.original_available === true &&
    typeof value.size_bytes === "number" &&
    value.content_length === null &&
    typeof value.ingestion_job_id === "string"
  ) {
    const ingestion = parseIngestionDiagnostics(value.ingestion);
    if (ingestion === null) return null;
    return {
      ...common,
      source: "managed",
      original_available: true,
      size_bytes: value.size_bytes,
      content_length: null,
      ingestion_job_id: value.ingestion_job_id,
      ingestion,
    };
  }
  if (
    value.source === "remote" &&
    value.original_available === false &&
    value.size_bytes === null &&
    typeof value.content_length === "number" &&
    value.ingestion_job_id === null &&
    value.ingestion === null
  ) {
    return {
      ...common,
      source: "remote",
      original_available: false,
      size_bytes: null,
      content_length: value.content_length,
      ingestion_job_id: null,
      ingestion: null,
    };
  }
  return null;
}

function parseDocumentProgress(
  value: unknown,
): KnowledgeDocumentProgress | null | undefined {
  if (value === null) return null;
  if (!isRecord(value)) return undefined;
  const stage = value.stage;
  if (
    typeof stage !== "string" ||
    !KNOWLEDGE_DOCUMENT_PROGRESS_STAGES.has(
      stage as KnowledgeDocumentProgressStage,
    ) ||
    !isOptionalNumber(value.chunks_count) ||
    (typeof value.chunks_count === "number" &&
      (!Number.isInteger(value.chunks_count) || value.chunks_count < 0)) ||
    typeof value.stage_updated_at !== "string"
  ) {
    return undefined;
  }
  return {
    stage: stage as KnowledgeDocumentProgressStage,
    chunks_count: value.chunks_count,
    stage_updated_at: value.stage_updated_at,
  };
}

function parseIngestionDiagnostics(
  value: unknown,
): KnowledgeIngestionDiagnostics | null {
  if (!isRecord(value)) return null;
  const status = value.status;
  if (
    typeof status !== "string" ||
    !KNOWLEDGE_INGESTION_JOB_STATUSES.has(
      status as KnowledgeIngestionJobStatus,
    ) ||
    typeof value.attempt_count !== "number" ||
    typeof value.max_attempts !== "number" ||
    !isOptionalString(value.last_attempt_at) ||
    !isOptionalString(value.next_attempt_at) ||
    !isOptionalString(value.last_error_code) ||
    !isOptionalString(value.last_error_message) ||
    typeof value.manual_retry_count !== "number" ||
    typeof value.retry_allowed !== "boolean"
  ) {
    return null;
  }
  return {
    status: status as KnowledgeIngestionJobStatus,
    attempt_count: value.attempt_count,
    max_attempts: value.max_attempts,
    last_attempt_at: value.last_attempt_at,
    next_attempt_at: value.next_attempt_at,
    last_error_code: value.last_error_code,
    last_error_message: value.last_error_message,
    manual_retry_count: value.manual_retry_count,
    retry_allowed: value.retry_allowed,
  };
}

function parseDocumentsEnvelope(value: unknown): KnowledgeDocumentsEnvelope {
  if (!isRecord(value) || !Array.isArray(value.documents)) {
    throw new Error("Invalid knowledge documents response");
  }
  const documents = value.documents.map(parseDocument);
  if (documents.some((document) => document === null)) {
    throw new Error("Invalid knowledge documents response");
  }
  return { documents: documents as KnowledgeDocument[] };
}

function parseDocumentAccepted(value: unknown): KnowledgeDocumentAccepted {
  if (!isRecord(value) || typeof value.deduplicated !== "boolean") {
    throw new Error("Invalid knowledge document response");
  }
  const document = parseDocument(value.document);
  if (document === null) {
    throw new Error("Invalid knowledge document response");
  }
  return { document, deduplicated: value.deduplicated };
}

function parseDirectory(value: unknown): KnowledgeDirectory | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !isOptionalString(value.parent_id) ||
    typeof value.name !== "string" ||
    typeof value.document_count !== "number" ||
    typeof value.child_count !== "number" ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return null;
  }
  return {
    id: value.id,
    parent_id: value.parent_id,
    name: value.name,
    document_count: value.document_count,
    child_count: value.child_count,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

function parseDirectoriesEnvelope(
  value: unknown,
): KnowledgeDirectoriesEnvelope {
  if (!isRecord(value) || !Array.isArray(value.directories)) {
    throw new Error("Invalid knowledge directories response");
  }
  const directories = value.directories.map(parseDirectory);
  if (directories.some((directory) => directory === null)) {
    throw new Error("Invalid knowledge directories response");
  }
  return { directories: directories as KnowledgeDirectory[] };
}

function parseGraphLabelsEnvelope(
  value: unknown,
): KnowledgeGraphLabelsEnvelope {
  if (!isRecord(value) || !isStringArray(value.labels)) {
    throw new Error("Invalid knowledge graph labels response");
  }
  return { labels: value.labels };
}

function parseGraphNode(value: unknown): KnowledgeGraphNode | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.label !== "string" ||
    typeof value.entity_type !== "string" ||
    typeof value.description !== "string" ||
    typeof value.file_path !== "string"
  ) {
    return null;
  }
  return {
    id: value.id,
    label: value.label,
    entity_type: value.entity_type,
    description: value.description,
    file_path: value.file_path,
  };
}

function parseGraphEdge(value: unknown): KnowledgeGraphEdge | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.source !== "string" ||
    typeof value.target !== "string" ||
    typeof value.relation_type !== "string" ||
    typeof value.description !== "string" ||
    typeof value.keywords !== "string" ||
    typeof value.weight !== "number" ||
    typeof value.file_path !== "string"
  ) {
    return null;
  }
  return {
    id: value.id,
    source: value.source,
    target: value.target,
    relation_type: value.relation_type,
    description: value.description,
    keywords: value.keywords,
    weight: value.weight,
    file_path: value.file_path,
  };
}

function parseGraphEnvelope(value: unknown): KnowledgeGraphEnvelope {
  if (
    !isRecord(value) ||
    !Array.isArray(value.nodes) ||
    !Array.isArray(value.edges) ||
    typeof value.is_truncated !== "boolean"
  ) {
    throw new Error("Invalid knowledge graph response");
  }
  const nodes = value.nodes.map(parseGraphNode);
  const edges = value.edges.map(parseGraphEdge);
  if (
    nodes.some((node) => node === null) ||
    edges.some((edge) => edge === null)
  ) {
    throw new Error("Invalid knowledge graph response");
  }
  return {
    nodes: nodes as KnowledgeGraphNode[],
    edges: edges as KnowledgeGraphEdge[],
    is_truncated: value.is_truncated,
  };
}

function parseGlobalGraphEnvelope(
  value: unknown,
): KnowledgeGlobalGraphEnvelope {
  const graph = parseGraphEnvelope(value);
  if (
    !isRecord(value) ||
    !isNonNegativeInteger(value.total_labels) ||
    !isNonNegativeInteger(value.components)
  ) {
    throw new Error("Invalid global knowledge graph response");
  }
  return {
    ...graph,
    total_labels: value.total_labels,
    components: value.components,
  };
}

function parseRetrievalEntity(value: unknown): KnowledgeRetrievalEntity | null {
  if (
    !isRecord(value) ||
    typeof value.entity_name !== "string" ||
    typeof value.entity_type !== "string" ||
    typeof value.description !== "string" ||
    typeof value.file_path !== "string" ||
    typeof value.reference_id !== "string"
  ) {
    return null;
  }
  return {
    entity_name: value.entity_name,
    entity_type: value.entity_type,
    description: value.description,
    file_path: value.file_path,
    reference_id: value.reference_id,
  };
}

function parseRetrievalRelationship(
  value: unknown,
): KnowledgeRetrievalRelationship | null {
  if (
    !isRecord(value) ||
    typeof value.src_id !== "string" ||
    typeof value.tgt_id !== "string" ||
    typeof value.description !== "string" ||
    typeof value.keywords !== "string" ||
    typeof value.weight !== "number" ||
    typeof value.file_path !== "string" ||
    typeof value.reference_id !== "string"
  ) {
    return null;
  }
  return {
    src_id: value.src_id,
    tgt_id: value.tgt_id,
    description: value.description,
    keywords: value.keywords,
    weight: value.weight,
    file_path: value.file_path,
    reference_id: value.reference_id,
  };
}

function parseRetrievalChunk(value: unknown): KnowledgeRetrievalChunk | null {
  if (
    !isRecord(value) ||
    typeof value.chunk_id !== "string" ||
    typeof value.content !== "string" ||
    typeof value.file_path !== "string" ||
    typeof value.reference_id !== "string"
  ) {
    return null;
  }
  return {
    chunk_id: value.chunk_id,
    content: value.content,
    file_path: value.file_path,
    reference_id: value.reference_id,
  };
}

function parseRetrievalReference(
  value: unknown,
): KnowledgeRetrievalReference | null {
  if (
    !isRecord(value) ||
    typeof value.reference_id !== "string" ||
    typeof value.file_path !== "string"
  ) {
    return null;
  }
  return { reference_id: value.reference_id, file_path: value.file_path };
}

function parseRetrievalMetadata(
  value: unknown,
): KnowledgeRetrievalMetadata | null {
  if (
    !isRecord(value) ||
    typeof value.query_mode !== "string" ||
    !KNOWLEDGE_RETRIEVAL_MODES.has(
      value.query_mode as KnowledgeRetrievalMode,
    ) ||
    !isRecord(value.keywords) ||
    !isStringArray(value.keywords.high_level) ||
    !isStringArray(value.keywords.low_level) ||
    !isRecord(value.processing_info)
  ) {
    return null;
  }
  const processingInfo = value.processing_info;
  if (
    !isNonNegativeInteger(processingInfo.total_entities_found) ||
    !isNonNegativeInteger(processingInfo.total_relations_found) ||
    !isNonNegativeInteger(processingInfo.entities_after_truncation) ||
    !isNonNegativeInteger(processingInfo.relations_after_truncation) ||
    !isNonNegativeInteger(processingInfo.final_chunks_count)
  ) {
    return null;
  }
  return {
    query_mode: value.query_mode as KnowledgeRetrievalMode,
    keywords: {
      high_level: value.keywords.high_level,
      low_level: value.keywords.low_level,
    },
    processing_info: {
      total_entities_found: processingInfo.total_entities_found,
      total_relations_found: processingInfo.total_relations_found,
      entities_after_truncation: processingInfo.entities_after_truncation,
      relations_after_truncation: processingInfo.relations_after_truncation,
      final_chunks_count: processingInfo.final_chunks_count,
    },
  };
}

function parseRetrievalResponse(value: unknown): KnowledgeRetrievalResponse {
  if (
    !isRecord(value) ||
    !Array.isArray(value.entities) ||
    !Array.isArray(value.relationships) ||
    !Array.isArray(value.chunks) ||
    !Array.isArray(value.references)
  ) {
    throw new Error("Invalid knowledge retrieval response");
  }
  const entities = value.entities.map(parseRetrievalEntity);
  const relationships = value.relationships.map(parseRetrievalRelationship);
  const chunks = value.chunks.map(parseRetrievalChunk);
  const references = value.references.map(parseRetrievalReference);
  const metadata = parseRetrievalMetadata(value.metadata);
  if (
    entities.some((entity) => entity === null) ||
    relationships.some((relationship) => relationship === null) ||
    chunks.some((chunk) => chunk === null) ||
    references.some((reference) => reference === null) ||
    metadata === null
  ) {
    throw new Error("Invalid knowledge retrieval response");
  }
  return {
    entities: entities as KnowledgeRetrievalEntity[],
    relationships: relationships as KnowledgeRetrievalRelationship[],
    chunks: chunks as KnowledgeRetrievalChunk[],
    references: references as KnowledgeRetrievalReference[],
    metadata,
  };
}

function parseScope(value: unknown): KnowledgeScope | null {
  if (!isRecord(value)) return null;
  const documentStats = parseDocumentStats(value.document_stats);
  if (
    typeof value.id !== "string" ||
    typeof value.name !== "string" ||
    typeof value.description !== "string" ||
    typeof value.enabled !== "boolean" ||
    typeof value.available_for_retrieval !== "boolean" ||
    documentStats === null ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return null;
  }
  return {
    id: value.id,
    name: value.name,
    description: value.description,
    enabled: value.enabled,
    available_for_retrieval: value.available_for_retrieval,
    document_stats: documentStats,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

function parseScopeEnvelope(value: unknown): KnowledgeScopeEnvelope {
  if (!isRecord(value) || !("scope" in value) || !("data_plane" in value)) {
    throw new Error("Invalid Knowledge Scope response");
  }
  const scope = value.scope === null ? null : parseScope(value.scope);
  if (value.scope !== null && scope === null) {
    throw new Error("Invalid Knowledge Scope response");
  }
  try {
    return {
      scope,
      data_plane: parseKnowledgeBaseFeature(value.data_plane),
    };
  } catch {
    throw new Error("Invalid Knowledge Scope response");
  }
}

export class KnowledgeScopeRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "KnowledgeScopeRequestError";
  }
}

export class KnowledgeDocumentRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "KnowledgeDocumentRequestError";
  }
}

function documentRequestError(
  response: Response,
  data: unknown,
): KnowledgeDocumentRequestError {
  const detail = isRecord(data) && isRecord(data.detail) ? data.detail : null;
  const stringDetail =
    isRecord(data) && typeof data.detail === "string" ? data.detail : null;
  const detailMessage =
    detail && typeof detail.message === "string" ? detail.message : null;
  const message =
    detailMessage ??
    stringDetail ??
    `Knowledge documents request failed: ${response.statusText || response.status}`;
  const code =
    detail && typeof detail.code === "string" ? detail.code : undefined;
  return new KnowledgeDocumentRequestError(message, response.status, code);
}

async function requestKnowledgeScope(
  method: "GET" | "POST" | "PATCH",
  body?: KnowledgeScopeCreateInput | KnowledgeScopeUpdateInput,
): Promise<KnowledgeScopeEnvelope> {
  const url = `${getBackendBaseURL()}/api/knowledge/scope`;
  const res =
    method === "GET"
      ? await fetch(url)
      : await fetch(url, {
          method,
          ...(body === undefined
            ? {}
            : {
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
              }),
        });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    const detail = isRecord(data) && isRecord(data.detail) ? data.detail : null;
    const stringDetail =
      isRecord(data) && typeof data.detail === "string" ? data.detail : null;
    const detailMessage =
      detail && typeof detail.message === "string" ? detail.message : null;
    const message =
      detailMessage ??
      stringDetail ??
      `Knowledge Scope request failed: ${res.statusText || res.status}`;
    const code =
      detail && typeof detail.code === "string" ? detail.code : undefined;
    throw new KnowledgeScopeRequestError(message, res.status, code);
  }
  return parseScopeEnvelope(data);
}

export function fetchKnowledgeScope(): Promise<KnowledgeScopeEnvelope> {
  return requestKnowledgeScope("GET");
}

export function createKnowledgeScope(
  input: KnowledgeScopeCreateInput,
): Promise<KnowledgeScopeEnvelope> {
  return requestKnowledgeScope("POST", input);
}

export function updateKnowledgeScope(
  input: KnowledgeScopeUpdateInput,
): Promise<KnowledgeScopeEnvelope> {
  return requestKnowledgeScope("PATCH", input);
}

export async function fetchKnowledgeDocuments(
  signal?: AbortSignal,
): Promise<KnowledgeDocumentsEnvelope> {
  const res = await fetch(`${getBackendBaseURL()}/api/knowledge/documents`, {
    signal,
  });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    throw documentRequestError(res, data);
  }
  return parseDocumentsEnvelope(data);
}

export async function uploadKnowledgeDocument(
  file: File,
  idempotencyKey: string,
  directoryId?: string | null,
): Promise<KnowledgeDocumentAccepted> {
  const formData = new FormData();
  formData.append("file", file);
  if (directoryId) formData.append("directory_id", directoryId);
  const res = await fetch(`${getBackendBaseURL()}/api/knowledge/documents`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: formData,
  });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    throw documentRequestError(res, data);
  }
  return parseDocumentAccepted(data);
}

export async function fetchKnowledgeDirectories(
  signal?: AbortSignal,
): Promise<KnowledgeDirectoriesEnvelope> {
  const res = await fetch(`${getBackendBaseURL()}/api/knowledge/directories`, {
    signal,
  });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  return parseDirectoriesEnvelope(data);
}

export async function deleteKnowledgeDocument(
  documentId: string,
): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/documents/${encodeURIComponent(documentId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as unknown;
    throw documentRequestError(res, data);
  }
}

async function requestKnowledgeGraphLabels(
  signal: AbortSignal | undefined,
  limit: number,
): Promise<KnowledgeGraphLabelsEnvelope> {
  const params = new URLSearchParams({ limit: String(limit) });
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/graph/labels?${params.toString()}`,
    { signal },
  );
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  return parseGraphLabelsEnvelope(data);
}

export function fetchKnowledgeGraphLabels(
  signal?: AbortSignal,
  limit = KNOWLEDGE_GRAPH_LABEL_LIMIT,
): Promise<KnowledgeGraphLabelsEnvelope> {
  return requestKnowledgeGraphLabels(
    signal,
    boundedInteger("Knowledge graph label limit", limit, 1, 50),
  );
}

async function requestKnowledgeGlobalGraph(
  maxNodes: number,
  signal?: AbortSignal,
): Promise<KnowledgeGlobalGraphEnvelope> {
  const params = new URLSearchParams({ max_nodes: String(maxNodes) });
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/graph/global?${params.toString()}`,
    { signal },
  );
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  return parseGlobalGraphEnvelope(data);
}

export function fetchKnowledgeGlobalGraph(
  signal?: AbortSignal,
  maxNodes = KNOWLEDGE_GLOBAL_GRAPH_MAX_NODES,
): Promise<KnowledgeGlobalGraphEnvelope> {
  return requestKnowledgeGlobalGraph(
    boundedInteger("max_nodes", maxNodes, 1, 5000),
    signal,
  );
}

export function searchKnowledgeGraph(
  query: string,
  signal?: AbortSignal,
  limit = KNOWLEDGE_GRAPH_LABEL_LIMIT,
): Promise<KnowledgeGraphLabelsEnvelope> {
  const normalized = boundedText("q", query, 1, 255);
  const boundedLimit = boundedInteger("limit", limit, 1, 50);
  const params = new URLSearchParams({
    q: normalized,
    limit: String(boundedLimit),
  });
  return fetch(
    `${getBackendBaseURL()}/api/knowledge/graph/search?${params.toString()}`,
    { signal },
  ).then(async (res) => {
    const data = (await res.json().catch(() => null)) as unknown;
    if (!res.ok) throw documentRequestError(res, data);
    return parseGraphLabelsEnvelope(data);
  });
}

async function requestKnowledgeRetrieval(
  body: KnowledgeRetrievalInput,
  signal?: AbortSignal,
): Promise<KnowledgeRetrievalResponse> {
  const res = await fetch(`${getBackendBaseURL()}/api/knowledge/retrieval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    ...(signal === undefined ? {} : { signal }),
  });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  return parseRetrievalResponse(data);
}

export function retrieveKnowledge(
  input: KnowledgeRetrievalInput,
  signal?: AbortSignal,
): Promise<KnowledgeRetrievalResponse> {
  const query = boundedText("query", input.query, 3, 2000);
  if (!KNOWLEDGE_RETRIEVAL_MODES.has(input.mode)) {
    throw new RangeError("Invalid knowledge retrieval mode");
  }
  const topK =
    input.top_k === undefined
      ? undefined
      : boundedInteger("top_k", input.top_k, 1, 100);
  const chunkTopK =
    input.chunk_top_k === undefined
      ? undefined
      : boundedInteger("chunk_top_k", input.chunk_top_k, 1, 100);
  const maxTotalTokens =
    input.max_total_tokens === undefined
      ? undefined
      : boundedInteger("max_total_tokens", input.max_total_tokens, 256, 32000);
  return requestKnowledgeRetrieval(
    {
      query,
      mode: input.mode,
      ...(topK === undefined ? {} : { top_k: topK }),
      ...(chunkTopK === undefined ? {} : { chunk_top_k: chunkTopK }),
      ...(maxTotalTokens === undefined
        ? {}
        : { max_total_tokens: maxTotalTokens }),
    },
    signal,
  );
}

export async function createKnowledgeDirectory(
  input: KnowledgeDirectoryCreateInput,
): Promise<KnowledgeDirectory> {
  const res = await fetch(`${getBackendBaseURL()}/api/knowledge/directories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  const directory = parseDirectory(data);
  if (directory === null)
    throw new Error("Invalid knowledge directory response");
  return directory;
}

export async function renameKnowledgeDirectory(
  directoryId: string,
  name: string,
): Promise<KnowledgeDirectory> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/directories/${encodeURIComponent(directoryId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  const directory = parseDirectory(data);
  if (directory === null)
    throw new Error("Invalid knowledge directory response");
  return directory;
}

export async function deleteKnowledgeDirectory(
  directoryId: string,
): Promise<void> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/directories/${encodeURIComponent(directoryId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as unknown;
    throw documentRequestError(res, data);
  }
}

export async function moveKnowledgeDocument(
  documentId: string,
  directoryId: string | null,
): Promise<KnowledgeDocument> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/documents/${encodeURIComponent(documentId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory_id: directoryId }),
    },
  );
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) throw documentRequestError(res, data);
  const document = parseDocument(data);
  if (document === null) throw new Error("Invalid knowledge document response");
  return document;
}

export async function retryKnowledgeDocument(
  documentId: string,
  idempotencyKey: string,
): Promise<KnowledgeDocumentAccepted> {
  const res = await fetch(
    `${getBackendBaseURL()}/api/knowledge/documents/${encodeURIComponent(documentId)}/retry`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    },
  );
  const data = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    throw documentRequestError(res, data);
  }
  return parseDocumentAccepted(data);
}

export async function fetchKnowledgeBaseFeature(): Promise<KnowledgeBaseFeature> {
  const res = await fetch(`${getBackendBaseURL()}/api/features`);
  if (!res.ok) {
    throw new Error(
      `Failed to load knowledge-base status: ${res.statusText || res.status}`,
    );
  }
  const data = (await res.json()) as unknown;
  if (!isRecord(data)) {
    throw new Error("Invalid knowledge-base feature response");
  }
  return parseKnowledgeBaseFeature(data.knowledge_base);
}
