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

export type KnowledgeDocumentProgressStage =
  | "pending"
  | "parsing"
  | "analyzing"
  | "processing"
  | "preprocessed"
  | "processed"
  | "failed";

export interface KnowledgeDocumentProgress {
  stage: KnowledgeDocumentProgressStage;
  chunks_count: number | null;
  stage_updated_at: string;
}

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

interface KnowledgeDocumentBase {
  id: string;
  directory_id: string | null;
  original_filename: string;
  content_type: string;
  size_bytes: number | null;
  content_length: number | null;
  status: KnowledgeDocumentStatus;
  lightrag_tracking_id: string | null;
  failure_code: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  progress: KnowledgeDocumentProgress | null;
}

export interface KnowledgeManagedDocument extends KnowledgeDocumentBase {
  source: "managed";
  original_available: true;
  size_bytes: number;
  content_length: null;
  ingestion_job_id: string;
  ingestion: KnowledgeIngestionDiagnostics;
}

export interface KnowledgeRemoteDocument extends KnowledgeDocumentBase {
  source: "remote";
  original_available: false;
  size_bytes: null;
  content_length: number;
  ingestion_job_id: null;
  ingestion: null;
}

export type KnowledgeDocument =
  | KnowledgeManagedDocument
  | KnowledgeRemoteDocument;

export interface KnowledgeDocumentsEnvelope {
  documents: KnowledgeDocument[];
}

export interface KnowledgeDocumentAccepted {
  document: KnowledgeDocument;
  deduplicated: boolean;
}

export interface KnowledgeDirectory {
  id: string;
  parent_id: string | null;
  name: string;
  document_count: number;
  child_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDirectoriesEnvelope {
  directories: KnowledgeDirectory[];
}

export interface KnowledgeDirectoryCreateInput {
  name: string;
  parent_id: string | null;
}

export interface KnowledgeGraphLabelsEnvelope {
  labels: string[];
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  entity_type: string;
  description: string;
  file_path: string;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  description: string;
  keywords: string;
  weight: number;
  file_path: string;
}

export interface KnowledgeGraphEnvelope {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  is_truncated: boolean;
}

export interface KnowledgeGlobalGraphEnvelope extends KnowledgeGraphEnvelope {
  total_labels: number;
  components: number;
}

export type KnowledgeRetrievalMode =
  | "local"
  | "global"
  | "hybrid"
  | "naive"
  | "mix";

export interface KnowledgeRetrievalInput {
  query: string;
  mode: KnowledgeRetrievalMode;
  top_k?: number;
  chunk_top_k?: number;
  max_total_tokens?: number;
}

export interface KnowledgeRetrievalEntity {
  entity_name: string;
  entity_type: string;
  description: string;
  file_path: string;
  reference_id: string;
}

export interface KnowledgeRetrievalRelationship {
  src_id: string;
  tgt_id: string;
  description: string;
  keywords: string;
  weight: number;
  file_path: string;
  reference_id: string;
}

export interface KnowledgeRetrievalChunk {
  chunk_id: string;
  content: string;
  file_path: string;
  reference_id: string;
}

export interface KnowledgeRetrievalReference {
  reference_id: string;
  file_path: string;
}

export interface KnowledgeRetrievalMetadata {
  query_mode: KnowledgeRetrievalMode;
  keywords: {
    high_level: string[];
    low_level: string[];
  };
  processing_info: {
    total_entities_found: number;
    total_relations_found: number;
    entities_after_truncation: number;
    relations_after_truncation: number;
    final_chunks_count: number;
  };
}

export interface KnowledgeRetrievalResponse {
  entities: KnowledgeRetrievalEntity[];
  relationships: KnowledgeRetrievalRelationship[];
  chunks: KnowledgeRetrievalChunk[];
  references: KnowledgeRetrievalReference[];
  metadata: KnowledgeRetrievalMetadata;
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
