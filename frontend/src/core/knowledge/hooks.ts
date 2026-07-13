import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createKnowledgeScope,
  fetchKnowledgeBaseFeature,
  fetchKnowledgeScope,
  updateKnowledgeScope,
} from "./api";
import type {
  KnowledgeScopeCreateInput,
  KnowledgeScopeEnvelope,
  KnowledgeScopeUpdateInput,
} from "./types";

export const knowledgeScopeQueryKey = ["knowledge", "scope"] as const;

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
