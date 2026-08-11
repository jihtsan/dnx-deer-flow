"use client";

import {
  BoxesIcon,
  CircleAlertIcon,
  FileTextIcon,
  LoaderCircleIcon,
  NetworkIcon,
  PlayIcon,
  RefreshCwIcon,
  TagsIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useI18n } from "@/core/i18n/hooks";
import { useKnowledgeRetrieval } from "@/core/knowledge";
import type {
  KnowledgeRetrievalInput,
  KnowledgeRetrievalMode,
  KnowledgeRetrievalResponse,
} from "@/core/knowledge";
import { cn } from "@/lib/utils";

export { KnowledgeGraphPanel } from "./knowledge-graph";

const MODES: KnowledgeRetrievalMode[] = [
  "naive",
  "local",
  "global",
  "hybrid",
  "mix",
];

function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}

function hasEvidence(data: KnowledgeRetrievalResponse): boolean {
  return (
    data.entities.length > 0 ||
    data.relationships.length > 0 ||
    data.chunks.length > 0 ||
    data.references.length > 0
  );
}

export function KnowledgeRetrievalPanel({ enabled }: { enabled: boolean }) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.retrieval;
  const retrieval = useKnowledgeRetrieval(enabled);
  const [mode, setMode] = useState<KnowledgeRetrievalMode>("hybrid");
  const [query, setQuery] = useState("");
  const [lastRequest, setLastRequest] =
    useState<KnowledgeRetrievalInput | null>(null);

  const submit = (request: KnowledgeRetrievalInput) => {
    setLastRequest(request);
    retrieval.mutate(request);
  };

  const runRetrieval = () => {
    const normalizedQuery = query.trim();
    if (!enabled || normalizedQuery.length < 3 || retrieval.isPending) return;
    submit({ query: normalizedQuery, mode });
  };

  return (
    <div
      data-testid="knowledge-retrieval-panel"
      className="flex min-w-0 flex-col gap-4"
    >
      <Card className="gap-0 p-5 shadow-none sm:p-6">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            runRetrieval();
          }}
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold">{copy.modesTitle}</span>
            <Badge variant="outline" className="font-normal">
              {copy.liveData}
            </Badge>
          </div>
          <div
            role="group"
            aria-label={copy.modesTitle}
            className="bg-muted mb-3 grid grid-cols-2 gap-1 rounded-lg p-1 sm:inline-grid sm:grid-cols-5"
          >
            {MODES.map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={mode === item}
                onClick={() => setMode(item)}
                className={cn(
                  "focus-visible:ring-ring min-h-9 rounded-md px-3 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none",
                  mode === item
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {copy.modes[item]}
              </button>
            ))}
          </div>
          <p className="text-muted-foreground mb-4 text-[13px] leading-relaxed">
            {copy.modeHints[mode]}
          </p>
          <label className="block">
            <span className="sr-only">{copy.queryPlaceholder}</span>
            <textarea
              data-testid="knowledge-retrieval-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  runRetrieval();
                }
              }}
              rows={4}
              disabled={!enabled || retrieval.isPending}
              placeholder={copy.queryPlaceholder}
              className="border-input bg-background focus-visible:border-ring focus-visible:ring-ring/30 min-h-28 w-full resize-y rounded-lg border px-4 py-3 text-base leading-relaxed outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-60 sm:text-sm"
            />
          </label>
          <div className="mt-4 flex justify-end">
            <Button
              data-testid="knowledge-retrieval-run"
              type="submit"
              disabled={
                !enabled || query.trim().length < 3 || retrieval.isPending
              }
              className="min-w-28"
            >
              {retrieval.isPending ? (
                <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
              ) : (
                <PlayIcon />
              )}
              {retrieval.isPending ? copy.running : copy.run}
            </Button>
          </div>
        </form>
      </Card>

      {!enabled ? (
        <RetrievalState>{copy.unavailable}</RetrievalState>
      ) : retrieval.isPending ? (
        <RetrievalState>
          <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" />
          {copy.running}
        </RetrievalState>
      ) : retrieval.isError ? (
        <Alert variant="destructive">
          <CircleAlertIcon />
          <AlertTitle>{copy.errorTitle}</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>{retrieval.error.message}</p>
            {lastRequest ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => submit(lastRequest)}
              >
                <RefreshCwIcon />
                {copy.retry}
              </Button>
            ) : null}
          </AlertDescription>
        </Alert>
      ) : retrieval.data ? (
        hasEvidence(retrieval.data) ? (
          <RetrievalResults data={retrieval.data} />
        ) : (
          <RetrievalState>{copy.empty}</RetrievalState>
        )
      ) : null}
    </div>
  );
}

function RetrievalResults({ data }: { data: KnowledgeRetrievalResponse }) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.retrieval;
  const referencesById = useMemo(
    () =>
      new Map(
        data.references.map((reference) => [
          reference.reference_id,
          reference.file_path,
        ]),
      ),
    [data.references],
  );
  const visibleChunks = data.chunks.slice(0, 8);

  return (
    <div
      data-testid="knowledge-retrieval-results"
      aria-live="polite"
      className="animate-in fade-in-0 slide-in-from-bottom-2 flex min-w-0 flex-col gap-4 duration-200 motion-reduce:animate-none"
    >
      <Card className="gap-4 p-5 shadow-none">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">{copy.resultTitle}</span>
          <Badge variant="outline" className="font-normal">
            {copy.modes[data.metadata.query_mode]}
          </Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label={copy.entitiesCount} value={data.entities.length} />
          <Metric
            label={copy.relationsCount}
            value={data.relationships.length}
          />
          <Metric label={copy.chunksCount} value={data.chunks.length} />
          <Metric label={copy.referencesCount} value={data.references.length} />
        </div>
      </Card>

      {(data.metadata.keywords.high_level.length > 0 ||
        data.metadata.keywords.low_level.length > 0) && (
        <Card className="gap-4 p-5 shadow-none">
          <SectionTitle icon={TagsIcon}>{copy.keywordsTitle}</SectionTitle>
          <KeywordGroup
            label={copy.highLevelKeywords}
            values={data.metadata.keywords.high_level}
          />
          <KeywordGroup
            label={copy.lowLevelKeywords}
            values={data.metadata.keywords.low_level}
          />
        </Card>
      )}

      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
        <Card className="min-w-0 gap-3 p-5 shadow-none">
          <SectionTitle icon={FileTextIcon} count={data.chunks.length}>
            {copy.sourcesTitle}
          </SectionTitle>
          {visibleChunks.length > 0 ? (
            <div className="flex min-w-0 flex-col gap-2.5">
              {visibleChunks.map((chunk, index) => {
                const referencedPath =
                  referencesById.get(chunk.reference_id) ?? "";
                const filePath =
                  chunk.file_path.length > 0 ? chunk.file_path : referencedPath;
                return (
                  <article
                    key={`${chunk.chunk_id}-${index}`}
                    className="min-w-0 rounded-lg border p-4"
                  >
                    <div className="mb-2 flex min-w-0 items-center justify-between gap-3">
                      <span
                        className="min-w-0 truncate text-xs font-semibold"
                        title={filePath}
                      >
                        {basename(filePath) || chunk.chunk_id}
                      </span>
                      <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                        {chunk.reference_id}
                      </span>
                    </div>
                    <p className="text-muted-foreground line-clamp-5 text-[13px] leading-relaxed whitespace-pre-wrap">
                      {chunk.content}
                    </p>
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">{copy.empty}</p>
          )}
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card className="min-w-0 gap-4 p-5 shadow-none">
            <SectionTitle icon={NetworkIcon}>{copy.contextTitle}</SectionTitle>
            <EvidenceGroup
              label={copy.hitEntities}
              count={data.entities.length}
            >
              <div className="flex flex-wrap gap-1.5">
                {data.entities.map((entity, index) => (
                  <Badge
                    key={`${entity.reference_id}-${entity.entity_name}-${index}`}
                    variant="secondary"
                    title={entity.description}
                    className="max-w-full font-normal"
                  >
                    <span className="truncate">{entity.entity_name}</span>
                  </Badge>
                ))}
              </div>
            </EvidenceGroup>
            <EvidenceGroup
              label={copy.hitRelations}
              count={data.relationships.length}
            >
              <div className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-1">
                {data.relationships.map((relation, index) => (
                  <div
                    key={`${relation.reference_id}-${relation.src_id}-${relation.tgt_id}-${index}`}
                    className="bg-muted/50 min-w-0 rounded-md border px-3 py-2 text-xs"
                    title={relation.description}
                  >
                    <span className="font-medium">{relation.src_id}</span>
                    <span className="text-muted-foreground px-1.5">-&gt;</span>
                    <span className="font-medium">{relation.tgt_id}</span>
                    {relation.keywords ? (
                      <p className="text-muted-foreground mt-1 truncate">
                        {relation.keywords}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </EvidenceGroup>
          </Card>

          <Card className="min-w-0 gap-3 p-5 shadow-none">
            <SectionTitle icon={BoxesIcon} count={data.references.length}>
              {copy.referencesTitle}
            </SectionTitle>
            <div className="flex min-w-0 flex-col gap-2">
              {data.references.map((reference) => (
                <div
                  key={reference.reference_id}
                  className="flex min-w-0 items-center gap-2 rounded-md border px-3 py-2"
                >
                  <FileTextIcon className="text-muted-foreground size-4 shrink-0" />
                  <span
                    className="min-w-0 flex-1 truncate text-xs font-medium"
                    title={reference.file_path}
                  >
                    {basename(reference.file_path)}
                  </span>
                  <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                    {reference.reference_id}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted/50 rounded-lg border px-3 py-2.5">
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className="mt-0.5 font-mono text-lg font-semibold tabular-nums">
        {value}
      </div>
    </div>
  );
}

function SectionTitle({
  children,
  count,
  icon: Icon,
}: {
  children: React.ReactNode;
  count?: number;
  icon: typeof TagsIcon;
}) {
  return (
    <div className="flex items-center gap-2 text-[13px] font-semibold">
      <Icon className="text-muted-foreground size-4" />
      <span>{children}</span>
      {count !== undefined ? (
        <span className="text-muted-foreground font-mono font-normal">
          {count}
        </span>
      ) : null}
    </div>
  );
}

function KeywordGroup({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div>
      <div className="text-muted-foreground mb-2 text-xs">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {values.map((value) => (
          <Badge key={value} variant="outline" className="font-normal">
            {value}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function EvidenceGroup({
  children,
  count,
  label,
}: {
  children: React.ReactNode;
  count: number;
  label: string;
}) {
  if (count === 0) return null;
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs">
        <span>{label}</span>
        <span className="font-mono">{count}</span>
      </div>
      {children}
    </div>
  );
}

function RetrievalState({ children }: { children: React.ReactNode }) {
  return (
    <Card className="text-muted-foreground flex min-h-32 flex-row items-center justify-center gap-2 p-6 text-center text-sm shadow-none">
      {children}
    </Card>
  );
}
