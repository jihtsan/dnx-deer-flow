import { expect, test, type Page, type Route } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

function fulfill(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function dataPlane(status: "ready" | "offline" = "ready") {
  return {
    enabled: true,
    status,
    reason: status === "ready" ? "LightRAG is ready." : "LightRAG is offline.",
    diagnostics: {
      workspace_mode: "single",
      expected_tag: "v1.5.2-4-gab86f430",
      expected_commit: "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
      expected_core_version: "1.5.2",
      expected_api_version: "0308",
      observed_core_version: status === "ready" ? "1.5.2" : null,
      observed_api_version: status === "ready" ? "0308" : null,
      service_status: status === "ready" ? "healthy" : null,
    },
  };
}

function scope(enabled = true) {
  return {
    id: "ks-live",
    name: "Energy operations",
    description: "Station operation manuals",
    enabled,
    available_for_retrieval: enabled,
    document_stats: { total: 2, pending: 0, indexing: 0, ready: 2, failed: 0 },
    created_at: "2026-07-15T01:00:00Z",
    updated_at: "2026-07-15T01:00:00Z",
  };
}

async function mockKnowledgeWorkspace(
  page: Page,
  options: { enabled?: boolean; status?: "ready" | "offline" } = {},
) {
  mockLangGraphAPI(page);
  await page.route("**/api/knowledge/scope", (route) =>
    fulfill(route, {
      scope: scope(options.enabled ?? true),
      data_plane: dataPlane(options.status ?? "ready"),
    }),
  );
  await page.route("**/api/knowledge/documents", (route) =>
    fulfill(route, { documents: [] }),
  );
  await page.route("**/api/knowledge/directories", (route) =>
    fulfill(route, { directories: [] }),
  );
}

function graph() {
  return {
    nodes: [
      {
        id: "Grid controller",
        label: "Grid controller",
        entity_type: "SYSTEM",
        description: "Grid controller control system",
        file_path: "station-manual.md",
      },
      {
        id: "Battery cluster",
        label: "Battery cluster",
        entity_type: "EQUIPMENT",
        description: "Battery grouping",
        file_path: "storage-guide.md",
      },
      {
        id: "Energy storage",
        label: "Energy storage",
        entity_type: "EQUIPMENT",
        description: "Battery energy storage system",
        file_path: "storage-guide.md",
      },
      {
        id: "Charging station",
        label: "Charging station",
        entity_type: "SITE",
        description: "EV charging station",
        file_path: "station-manual.md",
      },
    ],
    edges: [
      {
        id: "controller-storage",
        source: "Grid controller",
        target: "Energy storage",
        relation_type: "COORDINATES",
        description: "Coordinates charge and discharge",
        keywords: "dispatch, storage",
        weight: 1,
        file_path: "station-manual.md",
      },
      {
        id: "controller-charging",
        source: "Grid controller",
        target: "Charging station",
        relation_type: "CONTROLS",
        description: "Controls charging load",
        keywords: "charging, load",
        weight: 0.9,
        file_path: "station-manual.md",
      },
    ],
    is_truncated: false,
    total_labels: 4,
    components: 2,
  };
}

test("loads the global graph, focuses popular groups, and searches without reloading", async ({
  page,
}) => {
  await mockKnowledgeWorkspace(page);
  let globalRequests = 0;
  await page.route("**/api/knowledge/graph/labels?**", (route) =>
    fulfill(route, { labels: ["Grid controller", "Battery cluster"] }),
  );
  await page.route("**/api/knowledge/graph/global?**", (route) => {
    globalRequests += 1;
    return fulfill(route, graph());
  });
  await page.route("**/api/knowledge/graph/search?**", (route) =>
    fulfill(route, { labels: ["Energy storage"] }),
  );

  await page.goto("/workspace/knowledge");
  await page.getByRole("tab", { name: "Knowledge graph" }).click();

  await expect(page.getByTestId("knowledge-graph-panel")).toContainText(
    "4 nodes / 2 relations",
  );
  await expect(page.getByTestId("knowledge-graph-entity-select")).toHaveCount(
    0,
  );
  const canvas = page.getByTestId("knowledge-graph-canvas");
  await expect(canvas).toBeVisible();
  await expect
    .poll(() =>
      canvas.evaluate((element) => {
        const context = (element as HTMLCanvasElement).getContext("2d");
        if (!context) return 0;
        const data = context.getImageData(
          0,
          0,
          Math.min(400, context.canvas.width),
          Math.min(300, context.canvas.height),
        ).data;
        const colors = new Set<string>();
        for (let index = 0; index < data.length; index += 64)
          colors.add(`${data[index]}:${data[index + 1]}:${data[index + 2]}`);
        return colors.size;
      }),
    )
    .toBeGreaterThan(3);

  const requestsBeforeFocus = globalRequests;
  await page.getByRole("button", { name: "Battery cluster" }).click();
  await expect(page.getByTestId("knowledge-graph-show-all")).toBeVisible();
  expect(globalRequests).toBe(requestsBeforeFocus);

  await page.getByTestId("knowledge-graph-search").fill("Energy");
  await page
    .getByTestId("knowledge-graph-search-results")
    .getByRole("button", { name: "Energy storage" })
    .click();
  await expect(page.getByTestId("knowledge-graph-node-detail")).toContainText(
    "Energy storage",
  );
  expect(globalRequests).toBe(requestsBeforeFocus);
});

test("submits a retrieval mode and renders returned LightRAG evidence", async ({
  page,
}) => {
  await mockKnowledgeWorkspace(page);
  let requestBody: unknown = null;
  await page.route("**/api/knowledge/retrieval", (route) => {
    requestBody = route.request().postDataJSON();
    return fulfill(route, {
      entities: [
        {
          entity_name: "Grid controller",
          entity_type: "SYSTEM",
          description: "Coordinates station power",
          file_path: "station-manual.md",
          reference_id: "1",
        },
      ],
      relationships: [
        {
          src_id: "Grid controller",
          tgt_id: "Energy storage",
          description: "Dispatches battery charge and discharge",
          keywords: "dispatch, peak shaving",
          weight: 0.95,
          file_path: "station-manual.md",
          reference_id: "1",
        },
      ],
      chunks: [
        {
          chunk_id: "chunk-1",
          content:
            "The controller schedules battery discharge during the peak tariff window.",
          file_path: "station-manual.md",
          reference_id: "1",
        },
      ],
      references: [{ reference_id: "1", file_path: "station-manual.md" }],
      metadata: {
        query_mode: "hybrid",
        keywords: {
          high_level: ["station dispatch"],
          low_level: ["peak tariff", "battery discharge"],
        },
        processing_info: {
          total_entities_found: 1,
          total_relations_found: 1,
          entities_after_truncation: 1,
          relations_after_truncation: 1,
          final_chunks_count: 1,
        },
      },
    });
  });

  await page.goto("/workspace/knowledge");
  await page.getByRole("tab", { name: "Retrieval test" }).click();
  await page
    .getByTestId("knowledge-retrieval-query")
    .fill("How should the station dispatch stored energy during peak tariffs?");
  await page.getByTestId("knowledge-retrieval-run").click();

  const results = page.getByTestId("knowledge-retrieval-results");
  await expect(results).toContainText("Retrieval evidence");
  await expect(results).toContainText("station dispatch");
  await expect(results).toContainText(
    "The controller schedules battery discharge during the peak tariff window.",
  );
  await expect(results).toContainText("station-manual.md");
  expect(requestBody).toEqual({
    query: "How should the station dispatch stored energy during peak tariffs?",
    mode: "hybrid",
  });
});

test("keeps graph and retrieval inactive while the data plane is unavailable", async ({
  page,
}) => {
  await mockKnowledgeWorkspace(page, { status: "offline" });
  let liveRequests = 0;
  await page.route("**/api/knowledge/graph**", (route) => {
    liveRequests += 1;
    return fulfill(route, { labels: [] });
  });
  await page.route("**/api/knowledge/retrieval", (route) => {
    liveRequests += 1;
    return fulfill(route, {}, 503);
  });

  await page.goto("/workspace/knowledge");
  const unavailable = page.getByTestId("knowledge-workbench-unavailable");

  await page.getByRole("tab", { name: "Knowledge graph" }).click();
  await expect(unavailable).toContainText("Knowledge base is offline");
  await expect(page.getByTestId("knowledge-graph-panel")).toHaveCount(0);

  await page.getByRole("tab", { name: "Retrieval test" }).click();
  await expect(unavailable).toContainText("Contact your administrator");
  await expect(page.getByTestId("knowledge-retrieval-panel")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-retrieval-run")).toHaveCount(0);
  expect(liveRequests).toBe(0);
});
