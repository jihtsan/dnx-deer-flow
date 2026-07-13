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
