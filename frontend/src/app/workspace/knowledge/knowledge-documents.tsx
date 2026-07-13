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
import { useEffect, useMemo, useRef, useState } from "react";

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
import { cn } from "@/lib/utils";

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

function statusDotClass(status: KnowledgeDocument["status"]): string {
  if (status === "ready") return "bg-emerald-500";
  if (status === "failed") return "bg-destructive";
  if (status === "indexing") return "bg-amber-500 animate-pulse";
  return "bg-muted-foreground/50";
}

function statusBadgeVariant(status: KnowledgeDocument["status"]) {
  if (status === "ready") return "default" as const;
  if (status === "failed") return "destructive" as const;
  return "outline" as const;
}

function fileTypeLabel(document: KnowledgeDocument): string {
  const dot = document.original_filename.lastIndexOf(".");
  if (dot > -1 && dot < document.original_filename.length - 1) {
    return document.original_filename.slice(dot + 1).toUpperCase();
  }
  return document.content_type.split("/").pop()?.toUpperCase() ?? "FILE";
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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<{
    filename: string;
    deduplicated: boolean;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const submittingRef = useRef(false);
  const previousStatusSignatureRef = useRef<string | null>(null);

  const documents = useMemo(
    () => documentsQuery.data?.documents ?? [],
    [documentsQuery.data],
  );
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

  // Keep a valid selection: fall back to the first document whenever the
  // current selection is missing (initial load, deletion, or refresh).
  useEffect(() => {
    const first = documents[0];
    if (first === undefined) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!documents.some((document) => document.id === selectedId)) {
      setSelectedId(first.id);
    }
  }, [documents, selectedId]);

  const selectedDocument =
    documents.find((document) => document.id === selectedId) ?? null;

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
          setSelectedId(result.document.id);
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
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <label className="block flex-1 space-y-2 text-sm font-medium">
              <span className="sr-only">{copy.fileLabel}</span>
              <Input
                ref={fileInputRef}
                data-testid="knowledge-document-input"
                type="file"
                aria-label={copy.fileLabel}
                disabled={
                  uploadMutation.isPending || uploadUnavailable !== null
                }
                onChange={(event) =>
                  selectFile(event.target.files?.item(0) ?? null)
                }
              />
            </label>
            <Button
              data-testid="knowledge-upload"
              type="button"
              className="shrink-0"
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
          </div>
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
        </section>

        <section
          className="grid gap-4 lg:grid-cols-[300px_1fr]"
          aria-labelledby="knowledge-list-title"
        >
          <div className="space-y-3">
            <h2 id="knowledge-list-title" className="text-sm font-medium">
              {copy.catalogTitle}
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
              <ul className="space-y-1 rounded-md border p-1.5">
                {documents.map((document) => {
                  const active = document.id === selectedId;
                  return (
                    <li key={document.id}>
                      <button
                        type="button"
                        data-testid={`knowledge-document-row-${document.id}`}
                        aria-current={active}
                        onClick={() => setSelectedId(document.id)}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                          active
                            ? "bg-accent text-accent-foreground"
                            : "hover:bg-accent/50",
                        )}
                      >
                        <FileTextIcon className="text-muted-foreground size-4 shrink-0" />
                        <span className="min-w-0 flex-1 truncate">
                          {document.original_filename}
                        </span>
                        <span
                          className={cn(
                            "size-2 shrink-0 rounded-full",
                            statusDotClass(document.status),
                          )}
                          aria-hidden
                        />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="min-h-[220px] rounded-md border p-5">
            {selectedDocument ? (
              <article
                key={selectedDocument.id}
                data-testid={`knowledge-document-${selectedDocument.id}`}
                className="space-y-5"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="bg-muted text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-lg text-[11px] font-semibold">
                      {fileTypeLabel(selectedDocument)}
                    </div>
                    <div className="min-w-0">
                      <h3 className="truncate font-medium">
                        {selectedDocument.original_filename}
                      </h3>
                      <p className="text-muted-foreground text-xs">
                        {formatUploadSize(selectedDocument.size_bytes)} ·{" "}
                        {copy.uploadedAt}{" "}
                        {formatTimestamp(selectedDocument.created_at, locale)}
                      </p>
                    </div>
                  </div>
                  <Badge
                    data-testid={`knowledge-document-status-${selectedDocument.id}`}
                    variant={statusBadgeVariant(selectedDocument.status)}
                  >
                    {statusIcon(selectedDocument.status)}
                    {copy.status[selectedDocument.status]}
                  </Badge>
                </div>
                <dl className="grid gap-4 text-xs sm:grid-cols-2">
                  <div className="space-y-1">
                    <dt className="text-muted-foreground">{copy.fileType}</dt>
                    <dd>{selectedDocument.content_type}</dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-muted-foreground">{copy.updatedAt}</dt>
                    <dd>
                      {formatTimestamp(selectedDocument.updated_at, locale)}
                    </dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-muted-foreground">{copy.trackingId}</dt>
                    <dd className="font-mono break-all">
                      {selectedDocument.lightrag_tracking_id ??
                        copy.trackingPending}
                    </dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-muted-foreground">
                      {copy.ingestionJobId}
                    </dt>
                    <dd className="font-mono break-all">
                      {selectedDocument.ingestion_job_id}
                    </dd>
                  </div>
                </dl>
                {selectedDocument.status === "failed" &&
                selectedDocument.failure_reason ? (
                  <Alert variant="destructive">
                    <CircleAlertIcon />
                    <AlertTitle>{copy.failureReason}</AlertTitle>
                    <AlertDescription>
                      {selectedDocument.failure_reason}
                    </AlertDescription>
                  </Alert>
                ) : null}
              </article>
            ) : (
              <div className="text-muted-foreground flex h-full min-h-[180px] items-center justify-center text-center text-sm">
                {copy.detailEmpty}
              </div>
            )}
          </div>
        </section>
      </CardContent>
    </Card>
  );
}
