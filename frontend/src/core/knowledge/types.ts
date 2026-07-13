export type KnowledgeBaseStatus =
  | "unconfigured"
  | "disabled"
  | "offline"
  | "incompatible"
  | "ready";

export interface KnowledgeBaseDiagnostics {
  workspace_mode: "single";
  expected_tag: string;
  expected_commit: string;
  expected_core_version: string;
  expected_api_version: string;
  observed_core_version: string | null;
  observed_api_version: string | null;
  service_status: string | null;
}

export interface KnowledgeBaseFeature {
  enabled: boolean;
  status: KnowledgeBaseStatus;
  reason: string;
  diagnostics: KnowledgeBaseDiagnostics;
}

export type KnowledgeDocumentStatus =
  | "pending"
  | "indexing"
  | "ready"
  | "failed";

export type KnowledgeIngestionJobStatus =
  | "pending"
  | "leased"
  | "retry_wait"
  | "succeeded"
  | "dead"
  | "cancelled";

export interface KnowledgeIngestionDiagnostics {
  status: KnowledgeIngestionJobStatus;
  attempt_count: number;
  max_attempts: number;
  last_attempt_at: string | null;
  next_attempt_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  manual_retry_count: number;
  retry_allowed: boolean;
}

export interface KnowledgeDocument {
  id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  status: KnowledgeDocumentStatus;
  lightrag_tracking_id: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  ingestion_job_id: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  ingestion: KnowledgeIngestionDiagnostics;
}

export interface KnowledgeDocumentsEnvelope {
  documents: KnowledgeDocument[];
}

export interface KnowledgeDocumentAccepted {
  document: KnowledgeDocument;
  deduplicated: boolean;
}

export interface KnowledgeDocumentStats {
  total: number;
  pending: number;
  indexing: number;
  ready: number;
  failed: number;
}

export interface KnowledgeScope {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  available_for_retrieval: boolean;
  document_stats: KnowledgeDocumentStats;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeScopeEnvelope {
  scope: KnowledgeScope | null;
  data_plane: KnowledgeBaseFeature;
}

export interface KnowledgeScopeCreateInput {
  name: string;
  description: string;
  enabled: boolean;
}

export interface KnowledgeScopeUpdateInput {
  name?: string;
  description?: string;
  enabled?: boolean;
}
