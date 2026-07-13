import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  KnowledgeBaseDiagnostics,
  KnowledgeBaseFeature,
  KnowledgeBaseStatus,
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

function parseKnowledgeBaseFeature(value: unknown): KnowledgeBaseFeature {
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
