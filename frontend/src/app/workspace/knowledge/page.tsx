"use client";

import {
  CheckCircle2Icon,
  CircleAlertIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useEffect } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import { useKnowledgeBaseFeature } from "@/core/knowledge";

const NONE = "—";

export default function KnowledgeBasePage() {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const featureQuery = useKnowledgeBaseFeature();

  useEffect(() => {
    document.title = `${kb.title} - ${t.pages.appName}`;
  }, [kb.title, t.pages.appName]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <div className="mx-auto flex w-full max-w-(--container-width-md) flex-col gap-6 p-6">
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <DatabaseIcon className="text-muted-foreground size-6" />
              <h1 className="text-2xl font-semibold">{kb.title}</h1>
            </div>
            <p className="text-muted-foreground max-w-2xl text-sm">
              {kb.description}
            </p>
          </div>

          {featureQuery.isPending ? (
            <Card data-testid="knowledge-status-loading">
              <CardContent className="flex items-center gap-3">
                <LoaderCircleIcon className="size-5 animate-spin" />
                <span>{kb.loading}</span>
              </CardContent>
            </Card>
          ) : featureQuery.isError ? (
            <Alert variant="destructive">
              <CircleAlertIcon />
              <AlertTitle>{kb.requestErrorTitle}</AlertTitle>
              <AlertDescription>
                <p>{kb.requestErrorDescription}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void featureQuery.refetch()}
                >
                  <RefreshCwIcon />
                  {kb.retry}
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <KnowledgeStatusCard feature={featureQuery.data} />
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}

function KnowledgeStatusCard({
  feature,
}: {
  feature: NonNullable<ReturnType<typeof useKnowledgeBaseFeature>["data"]>;
}) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const diagnostics = feature.diagnostics;
  const statusLabel = kb.status[feature.status];
  const diagnosticRows = [
    [kb.labels.workspaceMode, kb.singleWorkspace],
    [kb.labels.expectedTag, diagnostics.expected_tag],
    [kb.labels.expectedCommit, diagnostics.expected_commit],
    [kb.labels.expectedCoreVersion, diagnostics.expected_core_version],
    [kb.labels.expectedApiVersion, diagnostics.expected_api_version],
    [kb.labels.observedCoreVersion, diagnostics.observed_core_version ?? NONE],
    [kb.labels.observedApiVersion, diagnostics.observed_api_version ?? NONE],
    [kb.labels.serviceStatus, diagnostics.service_status ?? NONE],
  ];

  return (
    <Card data-testid="knowledge-status-card">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-2">
            <CardTitle>{kb.statusTitle}</CardTitle>
            <CardDescription>{feature.reason}</CardDescription>
          </div>
          <Badge
            data-testid="knowledge-status"
            variant={feature.status === "ready" ? "default" : "outline"}
          >
            {feature.status === "ready" ? (
              <CheckCircle2Icon />
            ) : (
              <CircleAlertIcon />
            )}
            {statusLabel}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <h2 className="text-sm font-medium">{kb.diagnosticsTitle}</h2>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          {diagnosticRows.map(([label, value]) => (
            <div key={label} className="min-w-0 space-y-1">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-mono break-all">{value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
