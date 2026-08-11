import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  createKnowledgeScope,
  createKnowledgeDirectory,
  deleteKnowledgeDocument,
  deleteKnowledgeDirectory,
  fetchKnowledgeBaseFeature,
  fetchKnowledgeDirectories,
  fetchKnowledgeDocuments,
  fetchKnowledgeGlobalGraph,
  fetchKnowledgeGraphLabels,
  searchKnowledgeGraph,
  KNOWLEDGE_GLOBAL_GRAPH_MAX_NODES,
  KNOWLEDGE_GRAPH_LABEL_LIMIT,
  fetchKnowledgeScope,
  moveKnowledgeDocument,
  renameKnowledgeDirectory,
  retrieveKnowledge,
  retryKnowledgeDocument,
  uploadKnowledgeDocument,
  updateKnowledgeScope,
} from "./api";
import type {
  KnowledgeDirectoryCreateInput,
  KnowledgeDocument,
  KnowledgeDocumentsEnvelope,
  KnowledgeRetrievalInput,
  KnowledgeScopeCreateInput,
  KnowledgeScopeEnvelope,
  KnowledgeScopeUpdateInput,
} from "./types";

export const knowledgeScopeQueryKey = ["knowledge", "scope"] as const;
export const knowledgeDocumentsQueryKey = ["knowledge", "documents"] as const;
export const knowledgeDirectoriesQueryKey = [
  "knowledge",
  "directories",
] as const;
export const knowledgeGraphLabelsQueryKey = [
  "knowledge",
  "graph",
  "labels",
  KNOWLEDGE_GRAPH_LABEL_LIMIT,
] as const;
export const KNOWLEDGE_DOCUMENT_POLL_INTERVAL_MS = 2000;

export const knowledgeGlobalGraphQueryKey = [
  "knowledge",
  "graph",
  "global",
  KNOWLEDGE_GLOBAL_GRAPH_MAX_NODES,
] as const;

export function getKnowledgeGraphSearchQueryKey(query: string) {
  return ["knowledge", "graph", "search", query.trim(), 20] as const;
}

export function getKnowledgeDocumentsRefetchInterval(
  data: KnowledgeDocumentsEnvelope | undefined,
): number | false {
  return data?.documents.some(
    (document) =>
      document.status === "pending" ||
      document.status === "indexing" ||
      (document.source === "managed" &&
        ["pending", "leased", "retry_wait"].includes(
          document.ingestion.status,
        )),
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

export function removeKnowledgeDocument(
  current: KnowledgeDocumentsEnvelope | undefined,
  documentId: string,
): KnowledgeDocumentsEnvelope {
  return {
    documents: (current?.documents ?? []).filter(
      (document) => document.id !== documentId,
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

export function useKnowledgeDirectories(enabled = true) {
  return useQuery({
    queryKey: knowledgeDirectoriesQueryKey,
    queryFn: ({ signal }) => fetchKnowledgeDirectories(signal),
    enabled,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}

export function useKnowledgeGraphLabels(enabled = true) {
  return useQuery({
    queryKey: knowledgeGraphLabelsQueryKey,
    queryFn: ({ signal }) => fetchKnowledgeGraphLabels(signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

export function useKnowledgeGlobalGraph(enabled = true) {
  return useQuery({
    queryKey: knowledgeGlobalGraphQueryKey,
    queryFn: ({ signal }) => fetchKnowledgeGlobalGraph(signal),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

export function useKnowledgeGraphSearch(query: string, enabled = true) {
  const normalized = query.trim();
  return useQuery({
    queryKey: getKnowledgeGraphSearchQueryKey(normalized),
    queryFn: ({ signal }) => searchKnowledgeGraph(normalized, signal),
    enabled: enabled && normalized.length > 0,
    staleTime: 30_000,
    retry: false,
  });
}

export function useKnowledgeRetrieval(enabled = true) {
  const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!enabled) controllerRef.current?.abort();
    return () => controllerRef.current?.abort();
  }, [enabled]);

  return useMutation({
    mutationFn: async (input: KnowledgeRetrievalInput) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        return await retrieveKnowledge(input, controller.signal);
      } finally {
        if (controllerRef.current === controller) controllerRef.current = null;
      }
    },
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
      directoryId,
      file,
      idempotencyKey,
    }: {
      file: File;
      idempotencyKey: string;
      directoryId: string | null;
    }) => uploadKnowledgeDocument(file, idempotencyKey, directoryId),
    onSuccess: (data) => {
      queryClient.setQueryData<KnowledgeDocumentsEnvelope>(
        knowledgeDocumentsQueryKey,
        (current) => upsertKnowledgeDocument(current, data.document),
      );
      void queryClient.invalidateQueries({
        queryKey: knowledgeDocumentsQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
      void queryClient.invalidateQueries({
        queryKey: knowledgeDirectoriesQueryKey,
      });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    },
  });
}

function useDirectoryMutation<TInput>(
  mutationFn: (input: TInput) => Promise<unknown>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: knowledgeDirectoriesQueryKey,
      });
    },
  });
}

export function useCreateKnowledgeDirectory() {
  return useDirectoryMutation<KnowledgeDirectoryCreateInput>(
    createKnowledgeDirectory,
  );
}

export function useRenameKnowledgeDirectory() {
  return useDirectoryMutation<{ directoryId: string; name: string }>(
    ({ directoryId, name }) => renameKnowledgeDirectory(directoryId, name),
  );
}

export function useDeleteKnowledgeDirectory() {
  return useDirectoryMutation<string>(deleteKnowledgeDirectory);
}

export function useMoveKnowledgeDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      directoryId,
    }: {
      documentId: string;
      directoryId: string | null;
    }) => moveKnowledgeDocument(documentId, directoryId),
    onSuccess: (document) => {
      queryClient.setQueryData<KnowledgeDocumentsEnvelope>(
        knowledgeDocumentsQueryKey,
        (current) => upsertKnowledgeDocument(current, document),
      );
      void queryClient.invalidateQueries({
        queryKey: knowledgeDirectoriesQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    },
  });
}

export function useDeleteKnowledgeDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteKnowledgeDocument,
    onSuccess: (_data, documentId) => {
      queryClient.setQueryData<KnowledgeDocumentsEnvelope>(
        knowledgeDocumentsQueryKey,
        (current) => removeKnowledgeDocument(current, documentId),
      );
      void queryClient.invalidateQueries({
        queryKey: knowledgeDocumentsQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
      void queryClient.invalidateQueries({
        queryKey: knowledgeDirectoriesQueryKey,
      });
      void queryClient.invalidateQueries({ queryKey: ["knowledge", "graph"] });
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
