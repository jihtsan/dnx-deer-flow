"use client";

import { NetworkIcon, SearchIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { useI18n } from "@/core/i18n/hooks";

export function KnowledgeGraphPanel() {
  const { t } = useI18n();
  const copy = t.knowledgeBase.graphPlaceholder;
  return (
    <Card data-testid="knowledge-graph-panel" className="p-0">
      <Empty className="min-h-[420px] border-0">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <NetworkIcon />
          </EmptyMedia>
          <EmptyTitle>{copy.title}</EmptyTitle>
          <EmptyDescription>{copy.description}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    </Card>
  );
}

export function KnowledgeRetrievalPanel() {
  const { t } = useI18n();
  const copy = t.knowledgeBase.retrievalPlaceholder;
  return (
    <Card data-testid="knowledge-retrieval-panel" className="p-0">
      <Empty className="min-h-[420px] border-0">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <SearchIcon />
          </EmptyMedia>
          <EmptyTitle>{copy.title}</EmptyTitle>
          <EmptyDescription>{copy.description}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    </Card>
  );
}
