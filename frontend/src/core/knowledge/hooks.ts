import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createKnowledgeScope,
  fetchKnowledgeBaseFeature,
  fetchKnowledgeDocuments,
  fetchKnowledgeScope,
  retryKnowledgeDocument,
  uploadKnowledgeDocument,
  updateKnowledgeScope,
} from "./api";
import type {
  KnowledgeDocument,
  KnowledgeDocumentsEnvelope,
  KnowledgeScopeCreateInput,
  KnowledgeScopeEnvelope,
  KnowledgeScopeUpdateInput,
} from "./types";

export const knowledgeScopeQueryKey = ["knowledge", "scope"] as const;
export const knowledgeDocumentsQueryKey = ["knowledge", "documents"] as const;
export const KNOWLEDGE_DOCUMENT_POLL_INTERVAL_MS = 2000;

export function getKnowledgeDocumentsRefetchInterval(
  data: KnowledgeDocumentsEnvelope | undefined,
): number | false {
  return data?.documents.some(
    (document) =>
      document.status === "pending" ||
      document.status === "indexing" ||
      ["pending", "leased", "retry_wait"].includes(document.ingestion.status),
  )
    ? KNOWLEDGE_DOCUMENT_POLL_INTERVAL_MS
    : false;
}

export function upsertKnowledgeDocument(
  current: KnowledgeDocumentsEnvelope | undefined,
  document: KnowledgeDocument,
): KnowledgeDocumentsEnvelope {
  const documents = current?.documents ?? [];
  const existingIndex = documents.findIndex((item) => item.id === document.id);
  if (existingIndex === -1) {
    return { documents: [document, ...documents] };
  }
  return {
    documents: documents.map((item) =>
      item.id === document.id ? document : item,
    ),
  };
}

export function useKnowledgeBaseFeature() {
  return useQuery({
    queryKey: ["features", "knowledge_base"],
    queryFn: fetchKnowledgeBaseFeature,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}

export function useKnowledgeScope() {
  return useQuery({
    queryKey: knowledgeScopeQueryKey,
    queryFn: fetchKnowledgeScope,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}

export function useKnowledgeDocuments(enabled = true) {
  return useQuery({
    queryKey: knowledgeDocumentsQueryKey,
    queryFn: ({ signal }) => fetchKnowledgeDocuments(signal),
    enabled,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
    refetchInterval: (query) =>
      getKnowledgeDocumentsRefetchInterval(query.state.data),
    refetchIntervalInBackground: false,
  });
}

function useScopeMutation<TInput>(
  mutationFn: (input: TInput) => Promise<KnowledgeScopeEnvelope>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (data) => {
      queryClient.setQueryData(knowledgeScopeQueryKey, data);
    },
  });
}

export function useCreateKnowledgeScope() {
  return useScopeMutation<KnowledgeScopeCreateInput>(createKnowledgeScope);
}

export function useUpdateKnowledgeScope() {
  return useScopeMutation<KnowledgeScopeUpdateInput>(updateKnowledgeScope);
}

export function useUploadKnowledgeDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      idempotencyKey,
    }: {
      file: File;
      idempotencyKey: string;
    }) => uploadKnowledgeDocument(file, idempotencyKey),
    onSuccess: (data) => {
      queryClient.setQueryData<KnowledgeDocumentsEnvelope>(
        knowledgeDocumentsQueryKey,
        (current) => upsertKnowledgeDocument(current, data.document),
      );
      void queryClient.invalidateQueries({
        queryKey: knowledgeDocumentsQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    },
  });
}

export function useRetryKnowledgeDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      idempotencyKey,
    }: {
      documentId: string;
      idempotencyKey: string;
    }) => retryKnowledgeDocument(documentId, idempotencyKey),
    onSuccess: (data) => {
      queryClient.setQueryData<KnowledgeDocumentsEnvelope>(
        knowledgeDocumentsQueryKey,
        (current) => upsertKnowledgeDocument(current, data.document),
      );
      void queryClient.invalidateQueries({
        queryKey: knowledgeDocumentsQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    },
  });
}
