"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2Icon,
  CheckIcon,
  CircleAlertIcon,
  Clock3Icon,
  LoaderCircleIcon,
  RefreshCwIcon,
  Trash2Icon,
  UploadIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useI18n } from "@/core/i18n/hooks";
import {
  flattenKnowledgeDirectories,
  knowledgeScopeQueryKey,
  useCreateKnowledgeDirectory,
  useDeleteKnowledgeDocument,
  useDeleteKnowledgeDirectory,
  useKnowledgeDirectories,
  useKnowledgeDocuments,
  useMoveKnowledgeDocument,
  useRenameKnowledgeDirectory,
  useRetryKnowledgeDocument,
  useUploadKnowledgeDocument,
} from "@/core/knowledge";
import type {
  KnowledgeBaseFeature,
  KnowledgeDocument,
  KnowledgeDocumentProgressStage,
  KnowledgeDocumentStats,
  KnowledgeScope,
} from "@/core/knowledge";
import { formatUploadSize } from "@/core/uploads/file-validation";
import { cn } from "@/lib/utils";

import { KnowledgeDirectoryTree } from "./knowledge-directory-tree";

const ROOT_DIRECTORY_VALUE = "__root__";

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

function statusPillClass(status: KnowledgeDocument["status"]): string {
  if (status === "ready")
    return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (status === "failed") return "bg-destructive/15 text-destructive";
  if (status === "indexing")
    return "bg-amber-500/15 text-amber-700 dark:text-amber-400";
  return "bg-muted text-muted-foreground";
}

function statusDotColor(status: KnowledgeDocument["status"]): string {
  if (status === "ready") return "bg-emerald-500";
  if (status === "failed") return "bg-destructive";
  if (status === "indexing") return "bg-amber-500";
  return "bg-muted-foreground/50";
}

function fileTypeLabel(document: KnowledgeDocument): string {
  const dot = document.original_filename.lastIndexOf(".");
  if (dot > -1 && dot < document.original_filename.length - 1) {
    return document.original_filename.slice(dot + 1).toUpperCase();
  }
  return document.content_type.split("/").pop()?.toUpperCase() ?? "FILE";
}

function documentStats(documents: KnowledgeDocument[]): KnowledgeDocumentStats {
  return documents.reduce<KnowledgeDocumentStats>(
    (stats, document) => ({
      ...stats,
      total: stats.total + 1,
      [document.status]: stats[document.status] + 1,
    }),
    { total: 0, pending: 0, indexing: 0, ready: 0, failed: 0 },
  );
}

export function KnowledgeDocumentsCard({
  scope,
  dataPlane,
  onStatsChange,
}: {
  scope: KnowledgeScope;
  dataPlane: KnowledgeBaseFeature;
  onStatsChange?: (stats: KnowledgeDocumentStats) => void;
}) {
  const { t, locale } = useI18n();
  const copy = t.knowledgeBase.documents;
  const queryClient = useQueryClient();
  const documentsQuery = useKnowledgeDocuments();
  const directoriesQuery = useKnowledgeDirectories();
  const createDirectoryMutation = useCreateKnowledgeDirectory();
  const renameDirectoryMutation = useRenameKnowledgeDirectory();
  const deleteDirectoryMutation = useDeleteKnowledgeDirectory();
  const deleteDocumentMutation = useDeleteKnowledgeDocument();
  const moveMutation = useMoveKnowledgeDocument();
  const uploadMutation = useUploadKnowledgeDocument();
  const retryMutation = useRetryKnowledgeDocument();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedDirectoryId, setSelectedDirectoryId] = useState<string | null>(
    null,
  );
  const [uploadDirectoryId, setUploadDirectoryId] = useState<string | null>(
    null,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<{
    filename: string;
    deduplicated: boolean;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const submittingRef = useRef(false);
  const retryingDocumentRef = useRef<string | null>(null);
  const deletingDocumentRef = useRef<string | null>(null);
  const deletionPromiseRef = useRef<Promise<void> | null>(null);
  const retryKeysRef = useRef(new Map<string, string>());
  const previousStatusSignatureRef = useRef<string | null>(null);

  const documents = useMemo(
    () => documentsQuery.data?.documents ?? [],
    [documentsQuery.data],
  );
  const directories = useMemo(
    () => directoriesQuery.data?.directories ?? [],
    [directoriesQuery.data],
  );
  const directoryOptions = useMemo(
    () => flattenKnowledgeDirectories(directories),
    [directories],
  );
  const stats = useMemo(() => documentStats(documents), [documents]);
  const statusSignature = documents
    .map((document) =>
      document.source !== "remote"
        ? `${document.id}:${document.status}:${document.ingestion.status}:${document.ingestion.attempt_count}`
        : `${document.id}:${document.status}:${document.updated_at}`,
    )
    .join("|");

  useEffect(() => {
    if (documentsQuery.data !== undefined) onStatsChange?.(stats);
  }, [documentsQuery.data, onStatsChange, stats]);

  useEffect(() => {
    const previous = previousStatusSignatureRef.current;
    previousStatusSignatureRef.current = statusSignature;
    if (previous !== null && previous !== statusSignature) {
      void queryClient.invalidateQueries({ queryKey: knowledgeScopeQueryKey });
    }
  }, [queryClient, statusSignature]);

  useEffect(() => {
    if (
      selectedDirectoryId !== null &&
      !directories.some((directory) => directory.id === selectedDirectoryId)
    ) {
      setSelectedDirectoryId(null);
      setUploadDirectoryId(null);
    }
  }, [directories, selectedDirectoryId]);

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
  if (!scope.enabled) uploadUnavailable = copy.scopeDisabled;
  else if (dataPlane.status === "disabled")
    uploadUnavailable = copy.featureDisabled;
  else if (dataPlane.status !== "ready")
    uploadUnavailable = copy.dataPlaneUnavailable;

  const selectDirectory = (directoryId: string | null) => {
    setSelectedDirectoryId(directoryId);
    setUploadDirectoryId(directoryId);
  };
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
    )
      return;
    submittingRef.current = true;
    uploadMutation.mutate(
      { file: selectedFile, idempotencyKey, directoryId: uploadDirectoryId },
      {
        onSuccess: (result) => {
          setAccepted({
            filename: result.document.original_filename,
            deduplicated: result.deduplicated,
          });
          selectDirectory(result.document.directory_id);
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
  const retryDocument = (document: KnowledgeDocument) => {
    if (
      document.source === "remote" ||
      retryMutation.isPending ||
      retryingDocumentRef.current !== null ||
      !document.ingestion.retry_allowed
    )
      return;
    let idempotencyKey = retryKeysRef.current.get(document.id);
    if (idempotencyKey === undefined) {
      idempotencyKey = crypto.randomUUID();
      retryKeysRef.current.set(document.id, idempotencyKey);
    }
    retryingDocumentRef.current = document.id;
    retryMutation.mutate(
      { documentId: document.id, idempotencyKey },
      {
        onSuccess: () => retryKeysRef.current.delete(document.id),
        onSettled: () => {
          retryingDocumentRef.current = null;
        },
      },
    );
  };
  const deleteDocument = async (document: KnowledgeDocument) => {
    if (deletionPromiseRef.current !== null) {
      await deletionPromiseRef.current;
      return;
    }
    const documentIndex = documents.findIndex(
      (item) => item.id === document.id,
    );
    const nextDocument =
      documents[documentIndex + 1] ?? documents[documentIndex - 1] ?? null;
    deletingDocumentRef.current = document.id;
    const deletion = deleteDocumentMutation
      .mutateAsync(document.id)
      .then(() => setSelectedId(nextDocument?.id ?? null));
    deletionPromiseRef.current = deletion;
    try {
      await deletion;
    } finally {
      if (deletionPromiseRef.current === deletion) {
        deletionPromiseRef.current = null;
        deletingDocumentRef.current = null;
      }
    }
  };
  const documentStatusLabel = (document: KnowledgeDocument) =>
    document.source !== "remote" && document.ingestion.status === "retry_wait"
      ? copy.retryWaiting
      : copy.status[document.status];
  const refresh = () => {
    void documentsQuery.refetch();
    void directoriesQuery.refetch();
  };

  const directoryPending =
    createDirectoryMutation.isPending ||
    renameDirectoryMutation.isPending ||
    deleteDirectoryMutation.isPending;

  const treeLoading = documentsQuery.isPending;
  const treeError = documentsQuery.isError && documentsQuery.data === undefined;
  const directoryError =
    directoriesQuery.isError && directoriesQuery.data === undefined;
  const treeEmpty =
    !treeLoading &&
    !treeError &&
    !directoryError &&
    documents.length === 0 &&
    directories.length === 0;

  return (
    <div data-testid="knowledge-documents-card" className="space-y-3">
      <Input
        ref={fileInputRef}
        data-testid="knowledge-document-input"
        className="sr-only"
        type="file"
        aria-label={copy.fileLabel}
        disabled={uploadMutation.isPending || uploadUnavailable !== null}
        onChange={(event) => selectFile(event.target.files?.item(0) ?? null)}
      />

      {selectedFile || uploadMutation.isError ? (
        <div className="bg-card flex flex-col gap-3 rounded-xl border p-3 shadow-sm sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <p
              data-testid="knowledge-selected-file"
              className="truncate text-sm font-medium"
            >
              {selectedFile
                ? `${selectedFile.name} · ${formatUploadSize(selectedFile.size)}`
                : copy.noFile}
            </p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {copy.uploadDestination}
            </p>
          </div>
          <Select
            value={uploadDirectoryId ?? ROOT_DIRECTORY_VALUE}
            onValueChange={(value) =>
              setUploadDirectoryId(
                value === ROOT_DIRECTORY_VALUE ? null : value,
              )
            }
            disabled={uploadMutation.isPending}
          >
            <SelectTrigger
              className="w-full sm:w-[220px]"
              data-testid="knowledge-upload-directory"
            >
              <SelectValue aria-label={copy.uploadDestination} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ROOT_DIRECTORY_VALUE}>
                {copy.breadcrumbRoot}
              </SelectItem>
              {directoryOptions.map(({ directory, depth }) => (
                <SelectItem key={directory.id} value={directory.id}>
                  {`${"　".repeat(depth)}${directory.name}`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

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
          <AlertDescription>{uploadMutation.error.message}</AlertDescription>
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

      <div className="grid items-stretch gap-5 lg:h-[min(620px,calc(100vh-370px))] lg:min-h-[540px] lg:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="overflow-hidden rounded-xl py-0 shadow-none">
          <div className="flex h-12 items-center justify-between border-b px-4">
            <h2 className="text-sm font-semibold">{copy.catalogTitle}</h2>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-2"
                title={copy.fileLabel}
                aria-label={copy.fileLabel}
                disabled={
                  uploadMutation.isPending || uploadUnavailable !== null
                }
                onClick={() => fileInputRef.current?.click()}
              >
                <UploadIcon />
                {copy.fileLabel}
              </Button>
              <Button
                data-testid="knowledge-upload"
                type="button"
                variant="ghost"
                size="icon-sm"
                title={
                  uploadMutation.isPending
                    ? copy.uploading
                    : uploadMutation.isError
                      ? copy.retryUpload
                      : copy.upload
                }
                aria-label={
                  uploadMutation.isPending
                    ? copy.uploading
                    : uploadMutation.isError
                      ? copy.retryUpload
                      : copy.upload
                }
                disabled={
                  selectedFile === null ||
                  uploadUnavailable !== null ||
                  uploadMutation.isPending
                }
                onClick={upload}
              >
                {uploadMutation.isPending ? (
                  <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <CheckIcon />
                )}
                <span className="sr-only">
                  {uploadMutation.isPending
                    ? copy.uploading
                    : uploadMutation.isError
                      ? copy.retryUpload
                      : copy.upload}
                </span>
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                title={copy.refresh}
                aria-label={copy.refresh}
                onClick={refresh}
              >
                <RefreshCwIcon
                  className={cn(
                    (documentsQuery.isFetching ||
                      directoriesQuery.isFetching) &&
                      "animate-spin motion-reduce:animate-none",
                  )}
                />
              </Button>
            </div>
          </div>
          <div className="h-[430px] overflow-y-auto lg:h-[calc(100%-48px)]">
            {treeLoading ? (
              <div
                data-testid="knowledge-documents-loading"
                className="text-muted-foreground flex items-center gap-2 p-4 text-sm"
              >
                <LoaderCircleIcon className="size-4 animate-spin" />
                {copy.loading}
              </div>
            ) : treeError ? (
              <div className="p-3">
                <Alert
                  variant="destructive"
                  data-testid="knowledge-documents-error"
                >
                  <CircleAlertIcon />
                  <AlertTitle>{copy.listErrorTitle}</AlertTitle>
                  <AlertDescription>
                    <p>
                      {documentsQuery.error?.message ??
                        directoriesQuery.error?.message ??
                        copy.directoryErrorTitle}
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid="knowledge-documents-retry"
                      onClick={() => void documentsQuery.refetch()}
                    >
                      <RefreshCwIcon />
                      {copy.retryList}
                    </Button>
                  </AlertDescription>
                </Alert>
              </div>
            ) : (
              <div>
                {directoryError ? (
                  <div className="border-b p-3">
                    <Alert variant="destructive">
                      <CircleAlertIcon />
                      <AlertTitle>{copy.directoryErrorTitle}</AlertTitle>
                      <AlertDescription>
                        <p>{directoriesQuery.error.message}</p>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => void directoriesQuery.refetch()}
                        >
                          <RefreshCwIcon />
                          {copy.retryList}
                        </Button>
                      </AlertDescription>
                    </Alert>
                  </div>
                ) : null}
                {treeEmpty ? (
                  <div
                    data-testid="knowledge-documents-empty"
                    className="text-muted-foreground p-6 text-center text-sm"
                  >
                    {copy.emptyDescription}
                  </div>
                ) : (
                  <KnowledgeDirectoryTree
                    rootLabel={scope.name}
                    directories={directories}
                    documents={documents}
                    selectedDirectoryId={selectedDirectoryId}
                    selectedDocumentId={selectedId}
                    pending={directoryPending}
                    statusLabel={documentStatusLabel}
                    onSelectDirectory={selectDirectory}
                    onSelectDocument={setSelectedId}
                    onCreate={async (parentId, name) => {
                      await createDirectoryMutation.mutateAsync({
                        parent_id: parentId,
                        name,
                      });
                    }}
                    onRename={async (directoryId, name) => {
                      await renameDirectoryMutation.mutateAsync({
                        directoryId,
                        name,
                      });
                    }}
                    onDelete={async (directoryId) => {
                      await deleteDirectoryMutation.mutateAsync(directoryId);
                    }}
                  />
                )}
              </div>
            )}
          </div>
        </Card>

        <Card className="min-h-[520px] overflow-y-auto rounded-xl p-6 shadow-none lg:min-h-0">
          {selectedDocument ? (
            <DocumentDetail
              key={selectedDocument.id}
              document={selectedDocument}
              directories={directoryOptions}
              locale={locale}
              statusLabel={documentStatusLabel(selectedDocument)}
              moving={moveMutation.isPending}
              retrying={retryMutation.isPending}
              deleting={
                deleteDocumentMutation.isPending &&
                deletingDocumentRef.current === selectedDocument.id
              }
              retryError={
                retryMutation.isError ? retryMutation.error.message : null
              }
              onMove={(directoryId) =>
                moveMutation.mutate({
                  documentId: selectedDocument.id,
                  directoryId,
                })
              }
              onRetry={() => retryDocument(selectedDocument)}
              onDelete={() => deleteDocument(selectedDocument)}
            />
          ) : (
            <div className="text-muted-foreground flex min-h-[420px] items-center justify-center text-center text-sm">
              {copy.detailEmpty}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

type PipelineState = "done" | "active" | "failed" | "todo";
type PipelineStepId = "upload" | "queue" | "index" | "ready";

function buildPipeline(
  document: KnowledgeDocument,
  copy: ReturnType<typeof useI18n>["t"]["knowledgeBase"]["documents"],
): Array<{
  id: PipelineStepId;
  name: string;
  detail: string;
  state: PipelineState;
}> {
  const status = document.status;
  const ingestionStatus =
    document.source !== "remote" ? document.ingestion.status : null;
  let queue: PipelineState;
  let queueDetail: string;
  let index: PipelineState;
  let indexDetail: string;
  if (status === "failed") {
    queue = "done";
    queueDetail = copy.pipelineDetail.queued;
    index = "failed";
    indexDetail = copy.pipelineDetail.failed;
  } else if (status === "ready") {
    queue = "done";
    queueDetail = copy.pipelineDetail.queued;
    index = "done";
    indexDetail = copy.pipelineDetail.indexed;
  } else if (ingestionStatus === "retry_wait") {
    queue = "active";
    queueDetail = copy.pipelineDetail.retrying;
    index = "todo";
    indexDetail = copy.pipelineDetail.waiting;
  } else if (status === "indexing") {
    queue = "done";
    queueDetail = copy.pipelineDetail.queued;
    index = "active";
    indexDetail = copy.pipelineDetail.indexing;
  } else {
    queue = "active";
    queueDetail = copy.pipelineDetail.pending;
    index = "todo";
    indexDetail = copy.pipelineDetail.waiting;
  }
  return [
    {
      id: "upload",
      name: copy.pipeline.upload,
      detail: copy.pipelineDetail.uploaded,
      state: "done",
    },
    {
      id: "queue",
      name: copy.pipeline.queue,
      detail: queueDetail,
      state: queue,
    },
    {
      id: "index",
      name: copy.pipeline.index,
      detail: indexDetail,
      state: index,
    },
    {
      id: "ready",
      name: copy.pipeline.ready,
      detail: status === "ready" ? copy.pipelineDetail.ready : "—",
      state: status === "ready" ? "done" : "todo",
    },
  ];
}

function formatElapsedDuration(
  startedAt: string,
  now: number,
  locale: string,
): string {
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started)) return "—";
  const totalSeconds = Math.max(0, Math.floor((now - started) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (locale === "zh-CN") {
    if (hours > 0) return `${hours} 小时 ${minutes} 分`;
    if (minutes > 0) return `${minutes} 分 ${seconds} 秒`;
    return `${seconds} 秒`;
  }
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function IndexProgress({
  document,
  locale,
  now,
}: {
  document: KnowledgeDocument;
  locale: string;
  now: number;
}) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.documents;
  const progress = document.progress;
  if (progress === null) return null;

  const stage = progress.stage;
  const active =
    document.status === "pending" || document.status === "indexing";
  const phases: Array<{
    id: keyof typeof copy.progressPhase;
    state: PipelineState;
  }> = [];
  const afterParsing = new Set<KnowledgeDocumentProgressStage>([
    "analyzing",
    "preprocessed",
    "processing",
    "processed",
  ]);
  phases.push({
    id: "contentParsing",
    state:
      stage === "parsing"
        ? "active"
        : afterParsing.has(stage)
          ? "done"
          : "todo",
  });
  if (stage === "analyzing") {
    phases.push({ id: "multimodal", state: "active" });
  }
  phases.push({
    id: "entityExtraction",
    state:
      stage === "processing"
        ? "active"
        : stage === "processed"
          ? "done"
          : "todo",
  });
  phases.push({
    id: "graphWriting",
    state:
      stage === "processing"
        ? "active"
        : stage === "processed"
          ? "done"
          : "todo",
  });

  return (
    <div
      className="bg-muted/35 mt-2 space-y-2 rounded-md border px-3 py-2.5"
      role={active ? "status" : undefined}
      aria-live={active ? "polite" : undefined}
      data-testid="knowledge-document-index-progress"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          className="text-foreground text-xs font-medium"
          data-testid="knowledge-document-progress-stage"
        >
          {copy.progressStage[stage]}
        </span>
        <span className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          {active ? (
            <span data-testid="knowledge-document-progress-elapsed">
              {copy.stageElapsed}{" "}
              {formatElapsedDuration(progress.stage_updated_at, now, locale)}
            </span>
          ) : null}
          {progress.chunks_count !== null ? (
            <span data-testid="knowledge-document-progress-chunks">
              {progress.chunks_count.toLocaleString()} {copy.chunksCount}
            </span>
          ) : null}
        </span>
      </div>
      {stage !== "failed" && stage !== "pending" ? (
        <div className="grid gap-1.5 sm:grid-cols-2">
          {phases.map((phase) => (
            <div
              key={phase.id}
              className={cn(
                "flex min-h-6 items-center gap-2 text-[11.5px]",
                phase.state === "todo" && "text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "size-1.5 shrink-0 rounded-full",
                  phase.state === "done" && "bg-emerald-500",
                  phase.state === "active" && "animate-pulse bg-amber-500",
                  phase.state === "todo" && "bg-muted-foreground/30",
                )}
              />
              {copy.progressPhase[phase.id]}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PipelineStepIcon({ state }: { state: PipelineState }) {
  if (state === "done")
    return (
      <span className="bg-foreground flex size-5 items-center justify-center rounded-full">
        <CheckIcon className="text-background size-3" strokeWidth={3} />
      </span>
    );
  if (state === "active")
    return (
      <span className="flex size-5 items-center justify-center rounded-full border-[1.5px] border-amber-500 bg-amber-500/15">
        <LoaderCircleIcon className="size-3 animate-spin text-amber-600 dark:text-amber-400" />
      </span>
    );
  if (state === "failed")
    return (
      <span className="border-destructive bg-destructive/15 flex size-5 items-center justify-center rounded-full border-[1.5px]">
        <XIcon className="text-destructive size-3" strokeWidth={3} />
      </span>
    );
  return (
    <span className="bg-card flex size-5 items-center justify-center rounded-full border-[1.5px]">
      <span className="bg-muted-foreground/40 size-1.5 rounded-full" />
    </span>
  );
}

function DocumentDetail({
  document,
  directories,
  locale,
  statusLabel,
  moving,
  retrying,
  deleting,
  retryError,
  onMove,
  onRetry,
  onDelete,
}: {
  document: KnowledgeDocument;
  directories: ReturnType<typeof flattenKnowledgeDirectories>;
  locale: string;
  statusLabel: string;
  moving: boolean;
  retrying: boolean;
  deleting: boolean;
  retryError: string | null;
  onMove: (directoryId: string | null) => void;
  onRetry: () => void;
  onDelete: () => Promise<void>;
}) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.documents;
  const managed = document.source !== "remote";
  const pipeline = buildPipeline(document, copy);
  const progressActive =
    document.progress !== null &&
    (document.status === "pending" || document.status === "indexing");
  const [progressNow, setProgressNow] = useState(() => Date.now());
  useEffect(() => {
    if (!progressActive) return;
    setProgressNow(Date.now());
    const interval = window.setInterval(() => setProgressNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [progressActive, document.progress?.stage_updated_at]);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const confirmDelete = async () => {
    setDeleteError(null);
    try {
      await onDelete();
      setDeleteOpen(false);
    } catch (error) {
      setDeleteError(
        error instanceof Error ? error.message : copy.deleteErrorDescription,
      );
    }
  };
  return (
    <article
      data-testid={`knowledge-document-${document.id}`}
      className="space-y-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="bg-muted text-muted-foreground flex size-11 shrink-0 items-center justify-center rounded-[10px] font-mono text-[11px] font-bold">
            {fileTypeLabel(document)}
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold">
              {document.original_filename}
            </h3>
            <p className="text-muted-foreground mt-0.5 font-mono text-[12.5px]">
              {managed
                ? formatUploadSize(document.size_bytes)
                : `${copy.contentLength} ${document.content_length.toLocaleString()}`}
              {" · "}
              {copy.uploadedAt} {formatTimestamp(document.created_at, locale)}
            </p>
          </div>
        </div>
        <div className="flex items-center justify-end">
          <span
            data-testid={`knowledge-document-status-${document.id}`}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
              statusPillClass(document.status),
            )}
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                statusDotColor(document.status),
              )}
            />
            {statusLabel}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4">
        <Metric label={copy.fileType} value={fileTypeLabel(document)} />
        <Metric
          label={managed ? copy.selectedFile : copy.contentLength}
          value={
            managed
              ? formatUploadSize(document.size_bytes)
              : document.content_length.toLocaleString()
          }
        />
        <Metric
          label={copy.attempts}
          value={
            managed
              ? `${document.ingestion.attempt_count}/${document.ingestion.max_attempts}`
              : copy.notAvailable
          }
          testId={managed ? "knowledge-document-attempts" : undefined}
        />
        <Metric
          label={copy.updatedAt}
          value={new Intl.DateTimeFormat(
            locale === "zh-CN" ? "zh-CN" : "en-US",
            { month: "2-digit", day: "2-digit" },
          ).format(new Date(document.updated_at))}
        />
      </div>

      <div>
        <div className="mb-3 text-[13px] font-semibold">
          {copy.pipelineTitle}
        </div>
        <div className="flex flex-col">
          {pipeline.map((step, index) => {
            const last = index === pipeline.length - 1;
            return (
              <div
                key={step.id}
                className="flex items-stretch gap-3"
                aria-current={step.state === "active" ? "step" : undefined}
              >
                <div className="flex flex-col items-center">
                  <PipelineStepIcon state={step.state} />
                  {last ? null : (
                    <span
                      className={cn(
                        "w-px flex-1",
                        step.state === "done" ? "bg-foreground" : "bg-border",
                      )}
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1 pt-0.5 pb-3">
                  <div
                    className={cn(
                      "text-[13.5px] font-medium",
                      step.state === "todo" && "text-muted-foreground",
                      step.state === "failed" && "text-destructive",
                    )}
                  >
                    {step.name}
                  </div>
                  <div className="text-muted-foreground mt-0.5 text-xs">
                    {step.detail}
                  </div>
                  {step.id === "index" ? (
                    <IndexProgress
                      document={document}
                      locale={locale}
                      now={progressNow}
                    />
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="text-[13px] font-semibold">
            {copy.diagnosticsTitle}
          </div>
          <div className="flex items-center gap-2">
            <label
              className="text-muted-foreground text-[11px]"
              htmlFor={`knowledge-document-directory-${document.id}`}
            >
              {copy.moveTo}
            </label>
            <Select
              value={document.directory_id ?? ROOT_DIRECTORY_VALUE}
              disabled={moving}
              onValueChange={(value) =>
                onMove(value === ROOT_DIRECTORY_VALUE ? null : value)
              }
            >
              <SelectTrigger
                id={`knowledge-document-directory-${document.id}`}
                className="h-8 w-[150px] text-xs"
                data-testid="knowledge-document-directory"
                aria-label={copy.moveTo}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ROOT_DIRECTORY_VALUE}>
                  {copy.breadcrumbRoot}
                </SelectItem>
                {directories.map(({ directory, depth }) => (
                  <SelectItem key={directory.id} value={directory.id}>
                    {`${"　".repeat(depth)}${directory.name}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <dl className="bg-muted/40 grid gap-3 rounded-lg border p-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
          <Detail label={copy.trackingId} mono>
            {document.lightrag_tracking_id ?? copy.trackingPending}
          </Detail>
          <Detail
            label={managed ? copy.ingestionJobId : copy.contentLength}
            mono={managed}
          >
            {managed
              ? document.ingestion_job_id
              : document.content_length.toLocaleString()}
          </Detail>
          {managed ? (
            <>
              <Detail label={copy.lastAttempt}>
                {document.ingestion.last_attempt_at
                  ? formatTimestamp(document.ingestion.last_attempt_at, locale)
                  : copy.notAvailable}
              </Detail>
              <Detail
                label={copy.nextRetry}
                testId="knowledge-document-next-retry"
              >
                {document.ingestion.next_attempt_at
                  ? formatTimestamp(document.ingestion.next_attempt_at, locale)
                  : copy.notAvailable}
              </Detail>
              <Detail label={copy.errorType}>
                {document.ingestion.last_error_code ?? copy.notAvailable}
              </Detail>
            </>
          ) : null}
        </dl>
        {managed &&
        (document.ingestion.last_error_code ||
          document.ingestion.last_error_message) ? (
          <div
            data-testid={`knowledge-document-row-diagnostics-${document.id}`}
            className="bg-muted/50 text-muted-foreground rounded-lg border px-3 py-2 text-xs"
          >
            <span className="font-mono">
              {document.ingestion.last_error_code ?? copy.notAvailable}
            </span>
            {document.status !== "failed" &&
            document.ingestion.last_error_message ? (
              <span> · {document.ingestion.last_error_message}</span>
            ) : null}
          </div>
        ) : null}
      </div>

      {!managed ? (
        <Alert>
          <CircleAlertIcon />
          <AlertTitle>{copy.remoteSource}</AlertTitle>
          <AlertDescription>{copy.remoteNotice}</AlertDescription>
        </Alert>
      ) : null}
      {managed && document.ingestion.status === "retry_wait" ? (
        <Alert data-testid="knowledge-document-retry-wait">
          <Clock3Icon />
          <AlertTitle>{copy.retryWaitTitle}</AlertTitle>
          <AlertDescription>{copy.retryWaitDescription}</AlertDescription>
        </Alert>
      ) : null}
      {document.status === "failed" &&
      (managed
        ? document.ingestion.last_error_message
        : document.failure_reason) ? (
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>{copy.failureReason}</AlertTitle>
          <AlertDescription>
            {managed
              ? (document.ingestion.last_error_message ??
                document.failure_reason)
              : document.failure_reason}
          </AlertDescription>
        </Alert>
      ) : null}
      {retryError ? (
        <Alert
          variant="destructive"
          data-testid="knowledge-document-retry-error"
        >
          <CircleAlertIcon />
          <AlertTitle>{copy.retryErrorTitle}</AlertTitle>
          <AlertDescription>{retryError}</AlertDescription>
        </Alert>
      ) : null}
      {managed &&
      document.status === "failed" &&
      document.ingestion.retry_allowed ? (
        <Button
          type="button"
          data-testid={`knowledge-document-retry-${document.id}`}
          disabled={retrying}
          onClick={onRetry}
        >
          {retrying ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : (
            <RefreshCwIcon />
          )}
          {retrying ? copy.retrying : copy.manualRetry}
        </Button>
      ) : null}
      <div className="flex justify-end border-t pt-4">
        <AlertDialog
          open={deleteOpen}
          onOpenChange={(open) => {
            if (deleting) return;
            setDeleteOpen(open);
            if (open) setDeleteError(null);
          }}
        >
          <AlertDialogTrigger asChild>
            <Button
              type="button"
              variant="destructive"
              data-testid={`knowledge-document-delete-${document.id}`}
              disabled={deleting}
            >
              <Trash2Icon />
              {copy.deleteDocument}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent data-testid="knowledge-document-delete-dialog">
            <AlertDialogHeader>
              <AlertDialogTitle>{copy.deleteDialogTitle}</AlertDialogTitle>
              <AlertDialogDescription>
                {copy.deleteDialogDescription}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="bg-muted rounded-md border px-3 py-2 font-mono text-sm break-all">
              {document.original_filename}
            </div>
            {deleteError ? (
              <Alert
                variant="destructive"
                data-testid="knowledge-document-delete-error"
              >
                <CircleAlertIcon />
                <AlertTitle>{copy.deleteErrorTitle}</AlertTitle>
                <AlertDescription>{deleteError}</AlertDescription>
              </Alert>
            ) : null}
            <AlertDialogFooter>
              <AlertDialogCancel
                type="button"
                data-testid="knowledge-document-delete-cancel"
                disabled={deleting}
              >
                {copy.deleteCancel}
              </AlertDialogCancel>
              <AlertDialogAction
                type="button"
                className={buttonVariants({ variant: "destructive" })}
                data-testid="knowledge-document-delete-confirm"
                disabled={deleting}
                onClick={(event) => {
                  event.preventDefault();
                  void confirmDelete();
                }}
              >
                {deleting ? (
                  <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
                ) : (
                  <Trash2Icon />
                )}
                {deleting ? copy.deletingDocument : copy.deleteConfirm}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </article>
  );
}

function Detail({
  label,
  children,
  mono = false,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  mono?: boolean;
  testId?: string;
}) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId} className={cn("break-all", mono && "font-mono")}>
        {children}
      </dd>
    </div>
  );
}

function Metric({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border px-3 py-2.5">
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div
        data-testid={testId}
        className="mt-1 truncate font-mono text-lg leading-none font-semibold"
        title={value}
      >
        {value}
      </div>
    </div>
  );
}
