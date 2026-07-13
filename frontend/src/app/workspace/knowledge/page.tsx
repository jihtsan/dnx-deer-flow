"use client";

import {
  CheckCircle2Icon,
  CircleAlertIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

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
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useI18n } from "@/core/i18n/hooks";
import {
  useCreateKnowledgeScope,
  useKnowledgeScope,
  useUpdateKnowledgeScope,
} from "@/core/knowledge";
import type {
  KnowledgeBaseFeature,
  KnowledgeScope,
  KnowledgeScopeUpdateInput,
} from "@/core/knowledge";

import { KnowledgeDocumentsCard } from "./knowledge-documents";

const NONE = "—";

export default function KnowledgeBasePage() {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const scopeQuery = useKnowledgeScope();

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

          {scopeQuery.isPending ? (
            <Card data-testid="knowledge-status-loading">
              <CardContent className="flex items-center gap-3">
                <LoaderCircleIcon className="size-5 animate-spin" />
                <span>{kb.loading}</span>
              </CardContent>
            </Card>
          ) : scopeQuery.isError ? (
            <RequestError onRetry={() => void scopeQuery.refetch()} />
          ) : (
            <>
              <KnowledgeStatusCard feature={scopeQuery.data.data_plane} />
              {scopeQuery.data.scope === null ? (
                <CreateScopeCard dataPlane={scopeQuery.data.data_plane} />
              ) : (
                <>
                  <ManageScopeCard
                    scope={scopeQuery.data.scope}
                    dataPlane={scopeQuery.data.data_plane}
                  />
                  <KnowledgeDocumentsCard
                    scope={scopeQuery.data.scope}
                    dataPlane={scopeQuery.data.data_plane}
                  />
                </>
              )}
            </>
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
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

function MutationError({
  error,
  onRetry,
}: {
  error: Error;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  return (
    <Alert variant="destructive" data-testid="knowledge-save-error">
      <CircleAlertIcon />
      <AlertTitle>{t.knowledgeBase.saveErrorTitle}</AlertTitle>
      <AlertDescription>
        <p>{error.message}</p>
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          {t.knowledgeBase.retrySave}
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
          <MutationError error={mutation.error} onRetry={create} />
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

function ManageScopeCard({
  scope,
  dataPlane,
}: {
  scope: KnowledgeScope;
  dataPlane: KnowledgeBaseFeature;
}) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const [name, setName] = useState(scope.name);
  const [description, setDescription] = useState(scope.description);
  const mutation = useUpdateKnowledgeScope();
  const [saveSucceeded, setSaveSucceeded] = useState(false);
  const submitting = useRef(false);
  const lastUpdate = useRef<KnowledgeScopeUpdateInput>({
    name: scope.name,
    description: scope.description,
  });

  useEffect(() => {
    setName(scope.name);
    setDescription(scope.description);
  }, [scope.description, scope.name]);

  const submit = (input: KnowledgeScopeUpdateInput) => {
    if (submitting.current || mutation.isPending) return;
    submitting.current = true;
    setSaveSucceeded(false);
    lastUpdate.current = input;
    mutation.mutate(input, {
      onSuccess: () => setSaveSucceeded(true),
      onSettled: () => (submitting.current = false),
    });
  };
  const save = () => {
    if (!name.trim()) return;
    submit({ name: name.trim(), description: description.trim() });
  };
  const toggle = (enabled: boolean) => {
    submit({ enabled });
  };
  const canEnable = dataPlane.status === "ready";

  return (
    <Card data-testid="knowledge-scope-card">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{scope.name}</CardTitle>
            <CardDescription>{kb.scopeStableId}</CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={scope.enabled ? "default" : "outline"}>
              {scope.enabled ? kb.scopeEnabled : kb.scopeDisabled}
            </Badge>
            <Switch
              data-testid="knowledge-scope-enabled"
              aria-label={kb.enabledControl}
              checked={scope.enabled}
              disabled={mutation.isPending || (!scope.enabled && !canEnable)}
              onCheckedChange={toggle}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <Stat label={kb.documentsTotal} value={scope.document_stats.total} />
          <Stat label={kb.documentsReady} value={scope.document_stats.ready} />
          <Stat
            label={kb.documentsFailed}
            value={scope.document_stats.failed}
          />
        </div>
        <ScopeFields
          name={name}
          description={description}
          disabled={mutation.isPending}
          onNameChange={setName}
          onDescriptionChange={setDescription}
        />
        {!scope.enabled ? (
          <p className="text-muted-foreground text-sm">
            {kb.disabledRetrieval}
          </p>
        ) : null}
        {!scope.enabled && !canEnable ? (
          <p className="text-destructive text-sm">{kb.enableUnavailable}</p>
        ) : null}
        {mutation.isError ? (
          <MutationError
            error={mutation.error}
            onRetry={() => submit(lastUpdate.current)}
          />
        ) : null}
        {saveSucceeded ? (
          <Alert data-testid="knowledge-save-success">
            <CheckCircle2Icon />
            <AlertTitle>{kb.saveSuccess}</AlertTitle>
          </Alert>
        ) : null}
        <Button
          data-testid="knowledge-save"
          type="button"
          disabled={mutation.isPending || !name.trim()}
          onClick={save}
        >
          {mutation.isPending ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : null}
          {mutation.isPending ? kb.saving : kb.save}
        </Button>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}

function KnowledgeStatusCard({ feature }: { feature: KnowledgeBaseFeature }) {
  const { t } = useI18n();
  const kb = t.knowledgeBase;
  const diagnostics = feature.diagnostics;
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
            {kb.status[feature.status]}
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
