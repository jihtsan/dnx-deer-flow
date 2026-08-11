"use client";

import {
  CircleAlertIcon,
  DatabaseIcon,
  FileTextIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  SearchIcon,
  Share2Icon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { useCreateKnowledgeScope, useKnowledgeScope } from "@/core/knowledge";
import type { KnowledgeBaseFeature, KnowledgeScope } from "@/core/knowledge";
import type { KnowledgeDocumentStats } from "@/core/knowledge";
import { cn } from "@/lib/utils";

import { KnowledgeDocumentsCard } from "./knowledge-documents";
import {
  KnowledgeGraphPanel,
  KnowledgeRetrievalPanel,
} from "./knowledge-panels";

export default function KnowledgeBasePage() {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const scopeQuery = useKnowledgeScope();
  const [reconciledStats, setReconciledStats] =
    useState<KnowledgeDocumentStats | null>(null);

  useEffect(() => {
    document.title = `${kb.title} - ${t.pages.appName}`;
  }, [kb.title, t.pages.appName]);

  const feature = scopeQuery.data?.data_plane;
  const scopeId = scopeQuery.data?.scope?.id;
  const dataPlaneReady = feature?.status === "ready";

  useEffect(() => {
    setReconciledStats(null);
  }, [scopeId]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody className="overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1240px] flex-col gap-5 px-4 py-6 sm:px-6 lg:px-10">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-3">
              <span className="bg-foreground text-background flex size-9 shrink-0 items-center justify-center rounded-[10px]">
                <DatabaseIcon className="size-5" />
              </span>
              <h1 className="text-2xl font-bold">{kb.title}</h1>
              {feature ? <LightRAGBadge feature={feature} /> : null}
            </div>
            <p className="text-muted-foreground max-w-3xl text-sm leading-6">
              {kb.description}
            </p>
          </div>

          {scopeQuery.isPending ? (
            <Card data-testid="knowledge-status-loading">
              <CardContent className="flex items-center gap-3">
                <LoaderCircleIcon className="size-5 animate-spin" />
                <span>{kb.loading}</span>
              </CardContent>
            </Card>
          ) : scopeQuery.isError ? (
            <RequestError onRetry={() => void scopeQuery.refetch()} />
          ) : scopeQuery.data.scope === null ? (
            <CreateScopeCard dataPlane={scopeQuery.data.data_plane} />
          ) : (
            <>
              <OverviewBar
                scope={scopeQuery.data.scope}
                feature={scopeQuery.data.data_plane}
                stats={reconciledStats ?? scopeQuery.data.scope.document_stats}
              />
              <Tabs defaultValue="documents" className="gap-3">
                <TabsList
                  variant="line"
                  className="h-11 w-full justify-start gap-5 overflow-x-auto border-b p-0"
                >
                  <TabsTrigger
                    value="documents"
                    className="h-11 flex-none rounded-none px-1.5"
                  >
                    <FileTextIcon />
                    {kb.tabs.documents}
                  </TabsTrigger>
                  <TabsTrigger
                    value="graph"
                    className="h-11 flex-none rounded-none px-1.5"
                  >
                    <Share2Icon />
                    {kb.tabs.graph}
                  </TabsTrigger>
                  <TabsTrigger
                    value="retrieval"
                    className="h-11 flex-none rounded-none px-1.5"
                  >
                    <SearchIcon />
                    {kb.tabs.retrieval}
                  </TabsTrigger>
                </TabsList>
                <TabsContent
                  value="documents"
                  className="data-[state=active]:animate-in data-[state=active]:fade-in-0 data-[state=active]:slide-in-from-bottom-1 mt-2 space-y-6 data-[state=active]:duration-200 motion-reduce:animate-none"
                >
                  {dataPlaneReady ? (
                    <KnowledgeDocumentsCard
                      scope={scopeQuery.data.scope}
                      dataPlane={scopeQuery.data.data_plane}
                      onStatsChange={setReconciledStats}
                    />
                  ) : (
                    <KnowledgeWorkbenchUnavailable
                      onRetry={() => void scopeQuery.refetch()}
                    />
                  )}
                </TabsContent>
                <TabsContent
                  value="graph"
                  className="data-[state=active]:animate-in data-[state=active]:fade-in-0 data-[state=active]:slide-in-from-bottom-1 mt-2 data-[state=active]:duration-200 motion-reduce:animate-none"
                >
                  {dataPlaneReady ? (
                    <KnowledgeGraphPanel
                      enabled={scopeQuery.data.scope.enabled}
                    />
                  ) : (
                    <KnowledgeWorkbenchUnavailable
                      onRetry={() => void scopeQuery.refetch()}
                    />
                  )}
                </TabsContent>
                <TabsContent
                  value="retrieval"
                  className="data-[state=active]:animate-in data-[state=active]:fade-in-0 data-[state=active]:slide-in-from-bottom-1 mt-2 data-[state=active]:duration-200 motion-reduce:animate-none"
                >
                  {dataPlaneReady ? (
                    <KnowledgeRetrievalPanel
                      enabled={scopeQuery.data.scope.enabled}
                    />
                  ) : (
                    <KnowledgeWorkbenchUnavailable
                      onRetry={() => void scopeQuery.refetch()}
                    />
                  )}
                </TabsContent>
              </Tabs>
            </>
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function KnowledgeWorkbenchUnavailable({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  return (
    <Alert
      variant="destructive"
      data-testid="knowledge-workbench-unavailable"
      className="min-h-32 items-start"
    >
      <CircleAlertIcon />
      <AlertTitle>{kb.offlineTitle}</AlertTitle>
      <AlertDescription className="space-y-3">
        <p className="max-w-3xl leading-6">{kb.offlineDescription}</p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          {kb.checkAgain}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function LightRAGBadge({ feature }: { feature: KnowledgeBaseFeature }) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const ready = feature.status === "ready";
  return (
    <span
      data-testid="knowledge-status"
      title={feature.reason}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold",
        ready
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          ready ? "bg-emerald-500" : "bg-amber-500",
        )}
      />
      {kb.status[feature.status]}
    </span>
  );
}

function OverviewBar({
  scope,
  feature,
  stats,
}: {
  scope: KnowledgeScope;
  feature: KnowledgeBaseFeature;
  stats: KnowledgeDocumentStats;
}) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const processing = stats.pending + stats.indexing;
  return (
    <Card
      data-testid="knowledge-overview"
      className="rounded-xl py-0 shadow-none"
    >
      <CardContent className="flex min-h-14 flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3">
        <div className="flex items-center gap-2 pr-5 sm:border-r">
          <DatabaseIcon className="text-muted-foreground size-4" />
          <span className="font-medium">{scope.name}</span>
          <span className="text-muted-foreground text-xs">
            {kb.enterpriseLabel}
          </span>
        </div>
        <OverviewStat label={kb.documentsTotal} value={stats.total} />
        <OverviewStat
          label={kb.documentsReady}
          value={stats.ready}
          className="text-emerald-600 dark:text-emerald-400"
        />
        <OverviewStat
          label={kb.documentsProcessing}
          value={processing}
          className="text-amber-600 dark:text-amber-400"
        />
        <OverviewStat
          label={kb.documentsFailed}
          value={stats.failed}
          className={stats.failed > 0 ? "text-destructive" : undefined}
        />
        {!feature.enabled ? (
          <span className="text-muted-foreground ml-auto text-xs">
            {feature.reason}
          </span>
        ) : null}
      </CardContent>
    </Card>
  );
}

function OverviewStat({
  label,
  value,
  className,
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span
        className={cn(
          "font-mono text-xl leading-none font-semibold",
          className,
        )}
      >
        {value}
      </span>
      <span className="text-muted-foreground text-xs">{label}</span>
    </div>
  );
}

function RequestError({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  return (
    <Alert variant="destructive">
      <CircleAlertIcon />
      <AlertTitle>{kb.requestErrorTitle}</AlertTitle>
      <AlertDescription>
        <p>{kb.requestErrorDescription}</p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          {kb.retry}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function CreateScopeError({
  error,
  onRetry,
}: {
  error: Error;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  return (
    <Alert variant="destructive" data-testid="knowledge-create-error">
      <CircleAlertIcon />
      <AlertTitle>{t.knowledgeBase.createErrorTitle}</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          {t.knowledgeBase.retryCreate}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

function ScopeFields({
  name,
  description,
  disabled,
  onNameChange,
  onDescriptionChange,
}: {
  name: string;
  description: string;
  disabled: boolean;
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  return (
    <div className="space-y-4">
      <label className="block space-y-2 text-sm font-medium">
        <span>{kb.scopeName}</span>
        <Input
          data-testid="knowledge-scope-name"
          value={name}
          maxLength={255}
          disabled={disabled}
          onChange={(event) => onNameChange(event.target.value)}
          placeholder={kb.scopeNamePlaceholder}
        />
      </label>
      <label className="block space-y-2 text-sm font-medium">
        <span>{kb.scopeDescription}</span>
        <Textarea
          data-testid="knowledge-scope-description"
          value={description}
          maxLength={4000}
          disabled={disabled}
          onChange={(event) => onDescriptionChange(event.target.value)}
          placeholder={kb.scopeDescriptionPlaceholder}
        />
      </label>
    </div>
  );
}

function CreateScopeCard({ dataPlane }: { dataPlane: KnowledgeBaseFeature }) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const mutation = useCreateKnowledgeScope();
  const submitting = useRef(false);

  const create = () => {
    if (submitting.current || mutation.isPending || !name.trim()) return;
    submitting.current = true;
    mutation.mutate(
      { name: name.trim(), description: description.trim(), enabled: true },
      { onSettled: () => (submitting.current = false) },
    );
  };
  const canCreate = dataPlane.status === "ready" && name.trim().length > 0;

  return (
    <Card data-testid="knowledge-empty-state">
      <CardHeader>
        <CardTitle>{kb.emptyTitle}</CardTitle>
        <CardDescription>{kb.emptyDescription}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ScopeFields
          name={name}
          description={description}
          disabled={mutation.isPending}
          onNameChange={setName}
          onDescriptionChange={setDescription}
        />
        {dataPlane.status !== "ready" ? (
          <p className="text-destructive text-sm">{kb.createUnavailable}</p>
        ) : null}
        {mutation.isError ? (
          <CreateScopeError error={mutation.error} onRetry={create} />
        ) : null}
        <Button
          data-testid="knowledge-create"
          type="button"
          disabled={!canCreate || mutation.isPending}
          onClick={create}
        >
          {mutation.isPending ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : null}
          {mutation.isPending ? kb.creating : kb.create}
        </Button>
      </CardContent>
    </Card>
  );
}
