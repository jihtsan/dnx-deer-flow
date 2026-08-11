"use client";

import {
  CircleAlertIcon,
  LoaderCircleIcon,
  MinusIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import {
  useKnowledgeGlobalGraph,
  useKnowledgeGraphLabels,
  useKnowledgeGraphSearch,
} from "@/core/knowledge";
import type {
  KnowledgeGlobalGraphEnvelope,
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
} from "@/core/knowledge";

type NodeType = "org" | "person" | "product" | "tech" | "place" | "concept";

const TYPE_COLORS: Record<NodeType, string> = {
  org: "#52525b",
  person: "#b45309",
  product: "#047857",
  tech: "#2563eb",
  place: "#7c3aed",
  concept: "#be123c",
};

const LEGEND_ORDER: Array<{ key: NodeType; type: NodeType }> = [
  { key: "org", type: "org" },
  { key: "person", type: "person" },
  { key: "product", type: "product" },
  { key: "tech", type: "tech" },
  { key: "concept", type: "concept" },
  { key: "place", type: "place" },
];

type I18nLegend = ReturnType<
  typeof useI18n
>["t"]["knowledgeBase"]["graph"]["legend"];

interface LayoutNode extends KnowledgeGraphNode {
  x: number;
  y: number;
  radius: number;
  degree: number;
  type: NodeType;
}

interface LayoutEdge {
  source: LayoutNode;
  target: LayoutNode;
  label: string;
  weight: number;
}

export interface KnowledgeGraphLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  adjacency: Map<string, Set<string>>;
}

interface NodeInfo {
  id: string;
  label: string;
  typeLabel: string;
  color: string;
  desc: string;
  filePath: string;
  degree: number;
  relations: Array<{ rel: string; source: string; target: string }>;
}

function graphNodeType(entityType: string): NodeType {
  const value = entityType.toLowerCase();
  if (/person|people|employee|expert|manager/.test(value)) return "person";
  if (/org|company|department|team|institution|agency/.test(value))
    return "org";
  if (/place|location|city|country|site|region/.test(value)) return "place";
  if (/product|device|equipment|hardware|service/.test(value)) return "product";
  if (
    /tech|software|platform|system|protocol|algorithm|database|model/.test(
      value,
    )
  )
    return "tech";
  return "concept";
}

function edgeLabel(edge: KnowledgeGraphEdge): string {
  return (
    edge.keywords
      .split(",")
      .map((value) => value.trim())
      .find(Boolean) ?? edge.relation_type
  );
}

export function buildDirectedRelations(
  nodeId: string,
  edges: Array<{
    source: { id: string; label: string };
    target: { id: string; label: string };
    label: string;
  }>,
) {
  return edges
    .filter((edge) => edge.source.id === nodeId || edge.target.id === nodeId)
    .slice(0, 18)
    .map((edge) => ({
      rel: edge.label,
      source: edge.source.label,
      target: edge.target.label,
    }));
}

export function layoutKnowledgeGraph(
  data: KnowledgeGlobalGraphEnvelope,
): KnowledgeGraphLayout {
  const byId = new Map<string, LayoutNode>();
  const adjacency = new Map<string, Set<string>>();
  data.nodes.forEach((node) => adjacency.set(node.id, new Set()));
  const validEdges = data.edges.filter(
    (edge) => adjacency.has(edge.source) && adjacency.has(edge.target),
  );
  validEdges.forEach((edge) => {
    adjacency.get(edge.source)!.add(edge.target);
    adjacency.get(edge.target)!.add(edge.source);
  });

  const components: string[][] = [];
  const seen = new Set<string>();
  adjacency.forEach((_neighbors, start) => {
    if (seen.has(start)) return;
    const component: string[] = [];
    const queue = [start];
    seen.add(start);
    for (const id of queue) {
      component.push(id);
      (adjacency.get(id) ?? []).forEach((next) => {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      });
    }
    components.push(component);
  });
  const rawById = new Map(data.nodes.map((node) => [node.id, node]));
  const columns = Math.max(1, Math.ceil(Math.sqrt(components.length)));
  components.forEach((component, componentIndex) => {
    const centerX = (componentIndex % columns) * 520;
    const centerY = Math.floor(componentIndex / columns) * 520;
    component.forEach((id, index) => {
      const raw = rawById.get(id)!;
      const angle = index * 2.3999632297;
      const radius = index === 0 ? 0 : 34 * Math.sqrt(index);
      byId.set(id, {
        ...raw,
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius,
        degree: adjacency.get(id)?.size ?? 0,
        radius: 4 + Math.min(8, Math.sqrt(adjacency.get(id)?.size ?? 0) * 1.4),
        type: graphNodeType(raw.entity_type),
      });
    });
  });
  return {
    nodes: [...byId.values()],
    edges: validEdges.map((edge) => ({
      source: byId.get(edge.source)!,
      target: byId.get(edge.target)!,
      label: edgeLabel(edge),
      weight: edge.weight,
    })),
    adjacency,
  };
}

export function twoHopNeighborhood(
  adjacency: Map<string, Set<string>>,
  start: string,
): Set<string> {
  const result = new Set([start]);
  let frontier = [start];
  for (let depth = 0; depth < 2; depth += 1) {
    const next: string[] = [];
    frontier.forEach((id) =>
      (adjacency.get(id) ?? []).forEach((neighbor) => {
        if (!result.has(neighbor)) {
          result.add(neighbor);
          next.push(neighbor);
        }
      }),
    );
    frontier = next;
  }
  return result;
}

export function KnowledgeGraphPanel({ enabled }: { enabled: boolean }) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.graph;
  const graphQuery = useKnowledgeGlobalGraph(enabled);
  const labelsQuery = useKnowledgeGraphLabels(enabled);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const searchQuery = useKnowledgeGraphSearch(debouncedSearch, enabled);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const engine = useRef<CanvasGraphEngine | null>(null);
  const [nodeInfo, setNodeInfo] = useState<NodeInfo | null>(null);
  const [focusedLabel, setFocusedLabel] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedSearch(search.trim()),
      250,
    );
    return () => window.clearTimeout(timer);
  }, [search]);

  const layout = useMemo(
    () => (graphQuery.data ? layoutKnowledgeGraph(graphQuery.data) : null),
    [graphQuery.data],
  );
  useEffect(() => {
    if (!canvasRef.current || !layout) return;
    const instance = new CanvasGraphEngine(
      canvasRef.current,
      layout,
      copy.legend,
      setNodeInfo,
    );
    engine.current = instance;
    return () => {
      instance.destroy();
      if (engine.current === instance) engine.current = null;
    };
  }, [copy.legend, layout]);

  const byLabel = useMemo(() => {
    const map = new Map<string, KnowledgeGraphNode>();
    graphQuery.data?.nodes.forEach((node) => {
      map.set(node.label, node);
      map.set(node.id, node);
    });
    return map;
  }, [graphQuery.data]);
  const focus = (label: string, openDetails = false) => {
    const node = byLabel.get(label);
    if (!node) return;
    setFocusedLabel(node.label);
    engine.current?.focus(node.id, openDetails);
  };
  const showAll = () => {
    setFocusedLabel(null);
    setNodeInfo(null);
    engine.current?.showAll();
  };
  const hasGraph = (graphQuery.data?.nodes.length ?? 0) > 0;
  const visibleTypes = useMemo(
    () => new Set(layout?.nodes.map((node) => node.type) ?? []),
    [layout],
  );

  return (
    <Card
      data-testid="knowledge-graph-panel"
      className="overflow-hidden rounded-xl p-0 shadow-none"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-semibold">{copy.title}</span>
          <span className="text-muted-foreground font-mono text-xs">
            {graphQuery.data?.nodes.length ?? 0} {copy.nodes} /{" "}
            {graphQuery.data?.edges.length ?? 0} {copy.edges}
          </span>
          <span className="bg-muted text-muted-foreground rounded-full border px-2 py-0.5 text-[11px]">
            {copy.liveData}
          </span>
          <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-[11px]">
            {LEGEND_ORDER.filter(({ type }) => visibleTypes.has(type)).map(
              ({ key, type }) => (
                <span key={key} className="inline-flex items-center gap-1">
                  <span
                    className="size-2 rounded-full"
                    style={{ background: TYPE_COLORS[type] }}
                  />
                  {copy.legend[key]}
                </span>
              ),
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label={copy.zoomOut}
            disabled={!hasGraph}
            onClick={() => engine.current?.zoomBy(1 / 1.2)}
          >
            <MinusIcon />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            aria-label={copy.zoomIn}
            disabled={!hasGraph}
            onClick={() => engine.current?.zoomBy(1.2)}
          >
            <PlusIcon />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!hasGraph}
            onClick={showAll}
          >
            <RotateCcwIcon />
            {copy.reset}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={copy.refresh}
            disabled={!enabled || graphQuery.isFetching}
            onClick={() => void graphQuery.refetch()}
          >
            <RefreshCwIcon
              className={
                graphQuery.isFetching
                  ? "animate-spin motion-reduce:animate-none"
                  : undefined
              }
            />
          </Button>
        </div>
      </div>

      <div
        data-testid="knowledge-graph-popular"
        className="flex flex-nowrap items-center gap-1.5 overflow-x-auto border-b px-4 py-2.5"
      >
        <span className="text-muted-foreground mr-1 shrink-0 text-xs font-medium">
          {copy.popularGroups}
        </span>
        <Button
          type="button"
          variant={focusedLabel === null ? "secondary" : "ghost"}
          size="sm"
          className="shrink-0"
          data-testid="knowledge-graph-show-all"
          onClick={showAll}
        >
          {copy.showAll}
        </Button>
        {(labelsQuery.data?.labels ?? []).slice(0, 20).map((label) => (
          <Button
            key={label}
            type="button"
            variant={focusedLabel === label ? "secondary" : "ghost"}
            size="sm"
            className="shrink-0"
            data-testid="knowledge-graph-popular-label"
            onClick={() => focus(label)}
          >
            {label}
          </Button>
        ))}
      </div>

      <div className="relative border-b px-4 py-2.5">
        <SearchIcon className="text-muted-foreground absolute top-1/2 left-7 size-4 -translate-y-1/2" />
        <Input
          data-testid="knowledge-graph-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={copy.searchPlaceholder}
          aria-label={copy.searchPlaceholder}
          className="pl-9"
          disabled={!enabled}
        />
        {debouncedSearch && searchQuery.data ? (
          <div
            data-testid="knowledge-graph-search-results"
            className="bg-popover absolute top-[calc(100%-4px)] right-4 left-4 z-20 max-h-56 overflow-auto rounded-md border p-1 shadow-md"
          >
            {searchQuery.data.labels.length ? (
              searchQuery.data.labels.map((label) => (
                <button
                  key={label}
                  type="button"
                  className="hover:bg-accent focus-visible:ring-ring block w-full rounded-sm px-3 py-2 text-left text-sm outline-none focus-visible:ring-2"
                  onClick={() => {
                    focus(label, true);
                    setSearch(label);
                    setDebouncedSearch("");
                  }}
                >
                  {label}
                </button>
              ))
            ) : (
              <div className="text-muted-foreground px-3 py-2 text-sm">
                {copy.noSearchResults}
              </div>
            )}
          </div>
        ) : null}
      </div>

      <div className="bg-muted/20 relative h-[600px]">
        <canvas
          ref={canvasRef}
          data-testid="knowledge-graph-canvas"
          role="img"
          aria-label={`${copy.title}: ${graphQuery.data?.nodes.length ?? 0} ${copy.nodes}, ${graphQuery.data?.edges.length ?? 0} ${copy.edges}`}
          className="block size-full cursor-grab touch-none active:cursor-grabbing"
        />
        {!enabled ? (
          <GraphState>{copy.unavailable}</GraphState>
        ) : graphQuery.isPending || labelsQuery.isPending ? (
          <GraphState>
            <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" />
            {copy.loading}
          </GraphState>
        ) : graphQuery.isError || labelsQuery.isError ? (
          <div className="absolute inset-0 flex items-center justify-center p-6">
            <Alert variant="destructive" className="max-w-lg">
              <CircleAlertIcon />
              <AlertTitle>{copy.errorTitle}</AlertTitle>
              <AlertDescription className="space-y-3">
                <p>{graphQuery.error?.message ?? labelsQuery.error?.message}</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void graphQuery.refetch();
                    void labelsQuery.refetch();
                  }}
                >
                  <RefreshCwIcon />
                  {copy.retry}
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : !hasGraph ? (
          <GraphState>{copy.empty}</GraphState>
        ) : null}
        {graphQuery.data?.is_truncated ? (
          <div className="bg-card/90 text-muted-foreground absolute top-3 left-3 rounded border px-2 py-1 text-xs">
            {copy.truncated}
          </div>
        ) : null}
        {nodeInfo ? (
          <NodeDetails
            info={nodeInfo}
            copy={copy}
            onClose={() => {
              setNodeInfo(null);
              engine.current?.clearActive();
            }}
          />
        ) : null}
        {hasGraph ? (
          <div className="bg-card/85 text-muted-foreground pointer-events-none absolute bottom-3 left-4 rounded px-2 py-1 text-[11px]">
            {copy.hint}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function GraphState({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background/85 text-muted-foreground absolute inset-0 flex items-center justify-center gap-2 p-6 text-sm">
      {children}
    </div>
  );
}

function NodeDetails({
  info,
  copy,
  onClose,
}: {
  info: NodeInfo;
  copy: ReturnType<typeof useI18n>["t"]["knowledgeBase"]["graph"];
  onClose: () => void;
}) {
  return (
    <div
      data-testid="knowledge-graph-node-detail"
      className="bg-card absolute top-4 right-4 w-[300px] rounded-lg border p-4 shadow-lg max-sm:right-3 max-sm:left-3 max-sm:w-auto"
    >
      <div className="flex items-center justify-between">
        <span
          className="rounded-full px-2.5 py-1 text-[11px] font-semibold text-white"
          style={{ background: info.color }}
        >
          {info.typeLabel}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={copy.close}
          onClick={onClose}
        >
          <XIcon />
        </Button>
      </div>
      <div className="mt-3 text-base font-bold">{info.label}</div>
      <p className="text-muted-foreground mt-1.5 text-[13px]">{info.desc}</p>
      {info.filePath ? (
        <div className="mt-3 border-t pt-3 text-xs">
          <div className="text-muted-foreground">{copy.source}</div>
          <div className="truncate font-medium">{info.filePath}</div>
        </div>
      ) : null}
      <div className="mt-3 border-t pt-3 text-xs">
        <span className="text-muted-foreground">{copy.degree}: </span>
        <strong>{info.degree}</strong>
      </div>
      <div className="text-muted-foreground mt-3 mb-2 text-xs font-semibold">
        {copy.relations}
      </div>
      <div className="flex max-h-48 flex-col gap-1 overflow-auto">
        {info.relations.map((relation, index) => (
          <div
            key={`${relation.source}-${relation.target}-${index}`}
            data-testid="knowledge-graph-relation"
            className="text-xs"
          >
            <span className="font-medium">{relation.source}</span>{" "}
            <span className="text-amber-700">-{relation.rel}-&gt;</span>{" "}
            {relation.target}
          </div>
        ))}
      </div>
    </div>
  );
}

class CanvasGraphEngine {
  private ctx: CanvasRenderingContext2D;
  private width = 1;
  private height = 1;
  private scale = 1;
  private tx = 0;
  private ty = 0;
  private active: string | null = null;
  private focused: Set<string> | null = null;
  private pan: { x: number; y: number; tx: number; ty: number } | null = null;
  private raf: number | null = null;
  private labelPriority: LayoutNode[];
  private resizeObserver: ResizeObserver;
  private themeObserver: MutationObserver;
  private reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)")
    .matches;

  constructor(
    private canvas: HTMLCanvasElement,
    private layout: KnowledgeGraphLayout,
    private legend: I18nLegend,
    private onSelect: (info: NodeInfo | null) => void,
  ) {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D is unavailable");
    this.ctx = context;
    this.labelPriority = [...layout.nodes].sort(
      (first, second) =>
        second.degree - first.degree || first.label.localeCompare(second.label),
    );
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas);
    this.themeObserver = new MutationObserver(() => this.requestDraw());
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style"],
    });
    canvas.addEventListener("pointerdown", this.pointerDown);
    canvas.addEventListener("pointermove", this.pointerMove);
    canvas.addEventListener("pointerup", this.pointerUp);
    canvas.addEventListener("wheel", this.wheel, { passive: false });
    this.resize();
  }
  private resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.width = Math.max(1, rect.width);
    this.height = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.resetView();
  }
  private requestDraw = () => {
    if (this.raf !== null) return;
    this.raf = window.requestAnimationFrame(() => {
      this.raf = null;
      this.draw();
    });
  };
  private colors() {
    const style = getComputedStyle(document.documentElement);
    const value = (name: string, fallback: string) =>
      style.getPropertyValue(name).trim() || fallback;
    return {
      bg: value("--background", "white"),
      fg: value("--foreground", "#18181b"),
      border: value("--border", "#d4d4d8"),
      muted: value("--muted-foreground", "#71717a"),
    };
  }
  private draw = () => {
    const dpr = window.devicePixelRatio || 1;
    const c = this.colors();
    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = c.bg;
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.save();
    ctx.translate(this.tx, this.ty);
    ctx.scale(this.scale, this.scale);
    const largeGraph = this.layout.nodes.length > 1_000;
    const distant = this.scale < 0.55;
    ctx.strokeStyle = c.border;
    ctx.lineWidth = 1 / this.scale;
    this.layout.edges.forEach((edge) => {
      const visible =
        !this.focused ||
        (this.focused.has(edge.source.id) && this.focused.has(edge.target.id));
      if (
        !visible ||
        (largeGraph &&
          distant &&
          !this.focused &&
          edge.source.degree < 4 &&
          edge.target.degree < 4)
      )
        return;
      ctx.globalAlpha = this.focused ? 0.72 : largeGraph && distant ? 0.2 : 0.5;
      ctx.beginPath();
      ctx.moveTo(edge.source.x, edge.source.y);
      ctx.lineTo(edge.target.x, edge.target.y);
      ctx.stroke();
    });
    ctx.globalAlpha = 1;
    this.layout.nodes.forEach((node) => {
      const highlighted = !this.focused || this.focused.has(node.id);
      ctx.globalAlpha = highlighted ? 1 : 0.08;
      ctx.fillStyle = TYPE_COLORS[node.type];
      ctx.beginPath();
      ctx.arc(
        node.x,
        node.y,
        node.id === this.active ? node.radius + 3 : node.radius,
        0,
        Math.PI * 2,
      );
      ctx.fill();
      if (node.id === this.active) {
        ctx.strokeStyle = c.fg;
        ctx.lineWidth = 2 / this.scale;
        ctx.stroke();
      }
    });
    const labelLimit = largeGraph ? 220 : 36;
    const labelBoxes: Array<{
      left: number;
      right: number;
      top: number;
      bottom: number;
    }> = [];
    const labelCandidates = this.labelPriority.filter(
      (node) =>
        (!this.focused || this.focused.has(node.id)) &&
        (node.degree >= (largeGraph ? 3 : 4) || node.id === this.active),
    );
    if (this.scale > (largeGraph ? 0.9 : 0.1)) {
      ctx.fillStyle = c.fg;
      ctx.font = `${11 / this.scale}px sans-serif`;
      ctx.textAlign = "center";
      for (const node of labelCandidates) {
        if (labelBoxes.length >= labelLimit) break;
        const text = node.label.slice(0, 32);
        const width = ctx.measureText(text).width;
        const padding = 4 / this.scale;
        const baseline = node.y + node.radius + 13 / this.scale;
        const box = {
          left: node.x - width / 2 - padding,
          right: node.x + width / 2 + padding,
          top: baseline - 11 / this.scale - padding,
          bottom: baseline + 3 / this.scale + padding,
        };
        const overlaps = labelBoxes.some(
          (existing) =>
            box.left < existing.right &&
            box.right > existing.left &&
            box.top < existing.bottom &&
            box.bottom > existing.top,
        );
        if (overlaps) continue;
        labelBoxes.push(box);
        ctx.globalAlpha = 1;
        ctx.fillText(text, node.x, baseline);
      }
    }
    ctx.restore();
    ctx.globalAlpha = 1;
  };
  resetView = () => {
    if (!this.layout.nodes.length) {
      this.requestDraw();
      return;
    }
    const xs = this.layout.nodes.map((n) => n.x);
    const ys = this.layout.nodes.map((n) => n.y);
    const minX = Math.min(...xs),
      maxX = Math.max(...xs),
      minY = Math.min(...ys),
      maxY = Math.max(...ys);
    this.scale = Math.max(
      0.08,
      Math.min(
        1.4,
        Math.min(
          (this.width - 80) / Math.max(120, maxX - minX),
          (this.height - 80) / Math.max(120, maxY - minY),
        ),
      ),
    );
    this.tx = this.width / 2 - ((minX + maxX) / 2) * this.scale;
    this.ty = this.height / 2 - ((minY + maxY) / 2) * this.scale;
    this.requestDraw();
  };
  zoomBy = (factor: number) => {
    this.scale = Math.max(0.05, Math.min(5, this.scale * factor));
    this.requestDraw();
  };
  showAll = () => {
    this.focused = null;
    this.active = null;
    this.onSelect(null);
    this.resetView();
  };
  clearActive = () => {
    this.active = null;
    this.requestDraw();
  };
  focus = (id: string, open: boolean) => {
    const node = this.layout.nodes.find((item) => item.id === id);
    if (!node) return;
    this.focused = twoHopNeighborhood(this.layout.adjacency, id);
    this.active = open ? id : null;
    this.scale = Math.max(1, this.scale);
    this.tx = this.width / 2 - node.x * this.scale;
    this.ty = this.height / 2 - node.y * this.scale;
    if (open) this.select(node);
    this.requestDraw();
  };
  private select(node: LayoutNode) {
    this.active = node.id;
    this.onSelect({
      id: node.id,
      label: node.label,
      typeLabel: this.legend[node.type],
      color: TYPE_COLORS[node.type],
      desc: node.description,
      filePath: node.file_path,
      degree: node.degree,
      relations: buildDirectedRelations(node.id, this.layout.edges),
    });
  }
  private hit(event: PointerEvent) {
    const rect = this.canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left - this.tx) / this.scale;
    const y = (event.clientY - rect.top - this.ty) / this.scale;
    let best: LayoutNode | null = null;
    let distance = Infinity;
    this.layout.nodes.forEach((node) => {
      const d = (node.x - x) ** 2 + (node.y - y) ** 2;
      if (d < distance && d <= (node.radius + 7 / this.scale) ** 2) {
        best = node;
        distance = d;
      }
    });
    return best;
  }
  private pointerDown = (event: PointerEvent) => {
    try {
      this.canvas.setPointerCapture(event.pointerId);
    } catch {
      return;
    }
    this.pan = { x: event.clientX, y: event.clientY, tx: this.tx, ty: this.ty };
  };
  private pointerMove = (event: PointerEvent) => {
    if (!this.pan) return;
    this.tx = this.pan.tx + event.clientX - this.pan.x;
    this.ty = this.pan.ty + event.clientY - this.pan.y;
    this.requestDraw();
  };
  private pointerUp = (event: PointerEvent) => {
    const pan = this.pan;
    this.pan = null;
    if (pan && Math.hypot(event.clientX - pan.x, event.clientY - pan.y) < 4) {
      const node = this.hit(event);
      if (node) this.select(node);
      else {
        this.active = null;
        this.onSelect(null);
      }
      this.requestDraw();
    }
  };
  private wheel = (event: WheelEvent) => {
    event.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left,
      y = event.clientY - rect.top;
    const beforeX = (x - this.tx) / this.scale,
      beforeY = (y - this.ty) / this.scale;
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    this.scale = Math.max(0.05, Math.min(5, this.scale * factor));
    this.tx = x - beforeX * this.scale;
    this.ty = y - beforeY * this.scale;
    this.requestDraw();
  };
  destroy() {
    this.resizeObserver.disconnect();
    this.themeObserver.disconnect();
    if (this.raf !== null) window.cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener("pointerdown", this.pointerDown);
    this.canvas.removeEventListener("pointermove", this.pointerMove);
    this.canvas.removeEventListener("pointerup", this.pointerUp);
    this.canvas.removeEventListener("wheel", this.wheel);
    void this.reduceMotion;
  }
}
