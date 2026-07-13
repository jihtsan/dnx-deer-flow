import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  KnowledgeBaseDiagnostics,
  KnowledgeBaseFeature,
  KnowledgeBaseStatus,
  KnowledgeDocumentStats,
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isOptionalString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
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
