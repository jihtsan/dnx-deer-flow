import { useQuery } from "@tanstack/react-query";

import { fetchKnowledgeBaseFeature } from "./api";

export function useKnowledgeBaseFeature() {
  return useQuery({
    queryKey: ["features", "knowledge_base"],
    queryFn: fetchKnowledgeBaseFeature,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
}
