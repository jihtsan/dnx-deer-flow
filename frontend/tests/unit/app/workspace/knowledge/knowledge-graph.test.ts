import { expect, test } from "@rstest/core";

import {
  buildDirectedRelations,
  layoutKnowledgeGraph,
  twoHopNeighborhood,
} from "@/app/workspace/knowledge/knowledge-graph";

test("preserves source and target when summarizing either end of a directed edge", () => {
  const edges = [
    {
      source: { id: "controller", label: "Grid controller" },
      target: { id: "storage", label: "Energy storage" },
      label: "dispatches",
    },
    {
      source: { id: "meter", label: "Smart meter" },
      target: { id: "controller", label: "Grid controller" },
      label: "reports to",
    },
  ];

  expect(buildDirectedRelations("controller", edges)).toEqual([
    {
      rel: "dispatches",
      source: "Grid controller",
      target: "Energy storage",
    },
    {
      rel: "reports to",
      source: "Smart meter",
      target: "Grid controller",
    },
  ]);
  expect(buildDirectedRelations("storage", edges)).toEqual([
    {
      rel: "dispatches",
      source: "Grid controller",
      target: "Energy storage",
    },
  ]);
});

test("lays out disconnected components deterministically and finds two hops", () => {
  const data = {
    nodes: ["a", "b", "c", "d"].map((id) => ({
      id,
      label: id,
      entity_type: "concept",
      description: "",
      file_path: "one.md",
    })),
    edges: [
      {
        id: "ab",
        source: "a",
        target: "b",
        relation_type: "r",
        description: "",
        keywords: "r",
        weight: 1,
        file_path: "one.md",
      },
      {
        id: "bc",
        source: "b",
        target: "c",
        relation_type: "r",
        description: "",
        keywords: "r",
        weight: 1,
        file_path: "two.md",
      },
    ],
    is_truncated: false,
    total_labels: 4,
    components: 2,
  };
  const first = layoutKnowledgeGraph(data);
  const second = layoutKnowledgeGraph(data);
  expect(first.nodes.map(({ id, x, y }) => ({ id, x, y }))).toEqual(
    second.nodes.map(({ id, x, y }) => ({ id, x, y })),
  );
  expect([...twoHopNeighborhood(first.adjacency, "a")].sort()).toEqual([
    "a",
    "b",
    "c",
  ]);
});

test("lays out five thousand logical nodes without dropping graph data", () => {
  const nodeCount = 5_000;
  const data = {
    nodes: Array.from({ length: nodeCount }, (_, index) => ({
      id: `node-${index}`,
      label: `Node ${index}`,
      entity_type: index % 2 === 0 ? "equipment" : "concept",
      description: "",
      file_path: index < nodeCount / 2 ? "large.md" : "small.md",
    })),
    edges: Array.from({ length: nodeCount - 1 }, (_, index) => ({
      id: `edge-${index}`,
      source: `node-${index}`,
      target: `node-${index + 1}`,
      relation_type: "connected",
      description: "",
      keywords: "connected",
      weight: 1,
      file_path: index < nodeCount / 2 ? "large.md" : "small.md",
    })),
    is_truncated: false,
    total_labels: nodeCount,
    components: 1,
  };

  const layout = layoutKnowledgeGraph(data);

  expect(layout.nodes).toHaveLength(nodeCount);
  expect(layout.edges).toHaveLength(nodeCount - 1);
  expect(
    layout.nodes.every(
      (node) => Number.isFinite(node.x) && Number.isFinite(node.y),
    ),
  ).toBe(true);
  expect(twoHopNeighborhood(layout.adjacency, "node-2500").size).toBe(5);
});
