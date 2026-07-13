"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  Clock3Icon,
  FileTextIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  UploadIcon,
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
import { useI18n } from "@/core/i18n/hooks";
import {
  knowledgeScopeQueryKey,
  useKnowledgeDocuments,
  useUploadKnowledgeDocument,
} from "@/core/knowledge";
import type {
  KnowledgeBaseFeature,
  KnowledgeDocument,
  KnowledgeScope,
} from "@/core/knowledge";
import { formatUploadSize } from "@/core/uploads/file-validation";

function formatTimestamp(value: string, locale: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale === "zh-CN" ? "zh-CN" : "en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusIcon(status: KnowledgeDocument["status"]) {
  if (status === "ready") return <CheckCircle2Icon />;
  if (status === "failed") return <CircleAlertIcon />;
  if (status === "indexing")
    return <LoaderCircleIcon className="animate-spin" />;
  return <Clock3Icon />;
}

export function KnowledgeDocumentsCard({
  scope,
  dataPlane,
}: {
  scope: KnowledgeScope;
  dataPlane: KnowledgeBaseFeature;
}) {
  const { t, locale } = useI18n();
  const copy = t.knowledgeBase.documents;
  const queryClient = useQueryClient();
  const documentsQuery = useKnowledgeDocuments();
  const uploadMutation = useUploadKnowledgeDocument();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [accepted, setAccepted] = useState<{
    filename: string;
    deduplicated: boolean;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const submittingRef = useRef(false);
  const previousStatusSignatureRef = useRef<string | null>(null);

  const documents = documentsQuery.data?.documents ?? [];
  const statusSignature = documents
    .map((document) => `${document.id}:${document.status}`)
    .join("|");

  useEffect(() => {
    const previous = previousStatusSignatureRef.current;
    previousStatusSignatureRef.current = statusSignature;
    if (previous !== null && previous !== statusSignature) {
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    }
  }, [queryClient, statusSignature]);

  let uploadUnavailable: string | null = null;
  if (!scope.enabled) {
    uploadUnavailable = copy.scopeDisabled;
  } else if (dataPlane.status === "disabled") {
    uploadUnavailable = copy.featureDisabled;
  } else if (dataPlane.status !== "ready") {
    uploadUnavailable = copy.dataPlaneUnavailable;
  }

  const selectFile = (file: File | null) => {
    setSelectedFile(file);
    setAccepted(null);
    uploadMutation.reset();
    idempotencyKeyRef.current = file ? crypto.randomUUID() : null;
  };

  const upload = () => {
    const idempotencyKey = idempotencyKeyRef.current;
    if (
      submittingRef.current ||
      uploadMutation.isPending ||
      uploadUnavailable !== null ||
      selectedFile === null ||
      idempotencyKey === null
    ) {
      return;
    }
    submittingRef.current = true;
    uploadMutation.mutate(
      { file: selectedFile, idempotencyKey },
      {
        onSuccess: (result) => {
          setAccepted({
            filename: result.document.original_filename,
            deduplicated: result.deduplicated,
          });
          setSelectedFile(null);
          idempotencyKeyRef.current = null;
          if (fileInputRef.current) fileInputRef.current.value = "";
        },
        onSettled: () => {
          submittingRef.current = false;
        },
      },
    );
  };

  return (
    <Card data-testid="knowledge-documents-card">
      <CardHeader>
        <div className="flex items-start gap-3">
          <FileTextIcon className="text-muted-foreground mt-0.5 size-5" />
          <div className="space-y-1">
            <CardTitle>{copy.title}</CardTitle>
            <CardDescription>{copy.description}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-3" aria-labelledby="knowledge-upload-title">
          <h2 id="knowledge-upload-title" className="text-sm font-medium">
            {copy.uploadTitle}
          </h2>
          <label className="block space-y-2 text-sm font-medium">
            <span>{copy.fileLabel}</span>
            <Input
              ref={fileInputRef}
              data-testid="knowledge-document-input"
              type="file"
              aria-label={copy.fileLabel}
              disabled={uploadMutation.isPending || uploadUnavailable !== null}
              onChange={(event) =>
                selectFile(event.target.files?.item(0) ?? null)
              }
            />
          </label>
          {selectedFile ? (
            <p
              data-testid="knowledge-selected-file"
              className="text-muted-foreground text-sm"
            >
              {copy.selectedFile}: {selectedFile.name} ·{" "}
              {formatUploadSize(selectedFile.size)}
            </p>
          ) : (
            <p className="text-muted-foreground text-sm">{copy.noFile}</p>
          )}
          {uploadUnavailable ? (
            <p
              data-testid="knowledge-upload-unavailable"
              className="text-destructive text-sm"
            >
              {uploadUnavailable}
            </p>
          ) : null}
          {uploadMutation.isError ? (
            <Alert variant="destructive" data-testid="knowledge-upload-error">
              <CircleAlertIcon />
              <AlertTitle>{copy.uploadErrorTitle}</AlertTitle>
              <AlertDescription>
                {uploadMutation.error.message}
              </AlertDescription>
            </Alert>
          ) : null}
          {accepted ? (
            <Alert data-testid="knowledge-upload-success">
              <CheckCircle2Icon />
              <AlertTitle>
                {accepted.deduplicated ? copy.alreadyAccepted : copy.accepted}
              </AlertTitle>
              <AlertDescription>{accepted.filename}</AlertDescription>
            </Alert>
          ) : null}
          <Button
            data-testid="knowledge-upload"
            type="button"
            disabled={
              selectedFile === null ||
              uploadUnavailable !== null ||
              uploadMutation.isPending
            }
            onClick={upload}
          >
            {uploadMutation.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <UploadIcon />
            )}
            {uploadMutation.isPending
              ? copy.uploading
              : uploadMutation.isError
                ? copy.retryUpload
                : copy.upload}
          </Button>
        </section>

        <section className="space-y-3" aria-labelledby="knowledge-list-title">
          <h2 id="knowledge-list-title" className="text-sm font-medium">
            {copy.listTitle}
          </h2>
          {documentsQuery.isPending ? (
            <div
              data-testid="knowledge-documents-loading"
              className="text-muted-foreground flex items-center gap-2 text-sm"
            >
              <LoaderCircleIcon className="size-4 animate-spin" />
              {copy.loading}
            </div>
          ) : documentsQuery.isError && documentsQuery.data === undefined ? (
            <Alert
              variant="destructive"
              data-testid="knowledge-documents-error"
            >
              <CircleAlertIcon />
              <AlertTitle>{copy.listErrorTitle}</AlertTitle>
              <AlertDescription>
                <p>{documentsQuery.error.message}</p>
                <Button
                  data-testid="knowledge-documents-retry"
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void documentsQuery.refetch()}
                >
                  <RefreshCwIcon />
                  {copy.retryList}
                </Button>
              </AlertDescription>
            </Alert>
          ) : documents.length === 0 ? (
            <div
              data-testid="knowledge-documents-empty"
              className="rounded-md border border-dashed p-5 text-center"
            >
              <p className="font-medium">{copy.emptyTitle}</p>
              <p className="text-muted-foreground mt-1 text-sm">
                {copy.emptyDescription}
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {documents.map((document) => (
                <article
                  key={document.id}
                  data-testid={`knowledge-document-${document.id}`}
                  className="space-y-3 rounded-md border p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="truncate font-medium">
                        {document.original_filename}
                      </h3>
                      <p className="text-muted-foreground text-xs">
                        {formatUploadSize(document.size_bytes)} ·{" "}
                        {document.content_type}
                      </p>
                    </div>
                    <Badge
                      data-testid={`knowledge-document-status-${document.id}`}
                      variant={
                        document.status === "ready"
                          ? "default"
                          : document.status === "failed"
                            ? "destructive"
                            : "outline"
                      }
                    >
                      {statusIcon(document.status)}
                      {copy.status[document.status]}
                    </Badge>
                  </div>
                  <dl className="grid gap-3 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="text-muted-foreground">
                        {copy.trackingId}
                      </dt>
                      <dd className="font-mono break-all">
                        {document.lightrag_tracking_id ?? copy.trackingPending}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">
                        {copy.ingestionJobId}
                      </dt>
                      <dd className="font-mono break-all">
                        {document.ingestion_job_id}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">
                        {copy.updatedAt}
                      </dt>
                      <dd>{formatTimestamp(document.updated_at, locale)}</dd>
                    </div>
                  </dl>
                  {document.status === "failed" && document.failure_reason ? (
                    <Alert variant="destructive">
                      <CircleAlertIcon />
                      <AlertTitle>{copy.failureReason}</AlertTitle>
                      <AlertDescription>
                        {document.failure_reason}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                </article>
              ))}
            </div>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
