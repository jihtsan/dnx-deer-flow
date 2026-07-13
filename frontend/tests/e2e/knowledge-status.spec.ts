import { expect, test, type Route } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

type KnowledgeStatus =
  | "unconfigured"
  | "disabled"
  | "offline"
  | "incompatible"
  | "ready";

const STATUS_LABELS: Record<KnowledgeStatus, string> = {
  unconfigured: "Unconfigured",
  disabled: "Disabled",
  offline: "Offline",
  incompatible: "Incompatible",
  ready: "Ready",
};

function dataPlane(status: KnowledgeStatus) {
  const ready = status === "ready";
  return {
    enabled: status !== "disabled" && status !== "unconfigured",
    status,
    reason: `Visible operator reason for ${status}.`,
    diagnostics: {
      workspace_mode: "single",
      expected_tag: "v1.5.2-4-gab86f430",
      expected_commit: "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
      expected_core_version: "1.5.2",
      expected_api_version: "0308",
      observed_core_version: ready ? "1.5.2" : null,
      observed_api_version: ready ? "0308" : null,
      service_status: ready ? "healthy" : null,
    },
    endpoint: "https://secret-lightrag.internal",
    api_key: "do-not-render",
  };
}

function scope(overrides: Record<string, unknown> = {}) {
  return {
    id: "ks-stable",
    name: "Product knowledge",
    description: "Shared product documentation",
    enabled: true,
    available_for_retrieval: true,
    document_stats: { total: 4, pending: 0, indexing: 0, ready: 3, failed: 1 },
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:00:00Z",
    ...overrides,
  };
}

function fulfill(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/knowledge/documents", (route) =>
    fulfill(route, { documents: [] }),
  );
});

for (const status of Object.keys(STATUS_LABELS) as KnowledgeStatus[]) {
  test(`keeps the single-resource entry visible and renders ${status}`, async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.route("**/api/knowledge/scope", (route) =>
      fulfill(route, { scope: null, data_plane: dataPlane(status) }),
    );

    await page.goto("/workspace/chats/new");
    const knowledgeLink = page
      .locator("[data-sidebar='sidebar']")
      .locator("a[href='/workspace/knowledge']");
    await expect(knowledgeLink).toBeVisible({ timeout: 15_000 });
    await knowledgeLink.click();

    const statusBadge = page.getByTestId("knowledge-status");
    await expect(statusBadge).toHaveText(STATUS_LABELS[status]);
    await expect(statusBadge).toHaveAttribute(
      "title",
      `Visible operator reason for ${status}.`,
    );
    await expect(page.getByTestId("knowledge-empty-state")).toBeVisible();
    await expect(page.getByTestId("knowledge-status-card")).toHaveCount(0);
    await expect(page.getByText("v1.5.2-4-gab86f430")).toHaveCount(0);
    await expect(page.getByText("Single startup workspace")).toHaveCount(0);
    await expect(
      page.getByText("https://secret-lightrag.internal"),
    ).toHaveCount(0);
    await expect(page.getByText("do-not-render")).toHaveCount(0);
    if (status !== "ready") {
      await expect(page.getByTestId("knowledge-create")).toBeDisabled();
      await expect(
        page.getByText(
          "Creation is unavailable until the LightRAG data plane is ready.",
        ),
      ).toBeVisible();
    }
  });
}

test("keeps the document workbench without operator-only cards", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/knowledge/scope", (route) =>
    fulfill(route, { scope: scope(), data_plane: dataPlane("ready") }),
  );

  await page.goto("/workspace/knowledge");

  await expect(page.getByTestId("knowledge-documents-card")).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-card")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-status-card")).toHaveCount(0);
  await expect(
    page.getByText("Product knowledge", { exact: true }),
  ).toHaveCount(1);
});

test("creates the singleton scope and survives refresh", async ({ page }) => {
  mockLangGraphAPI(page);
  let storedScope: ReturnType<typeof scope> | null = null;
  await page.route("**/api/knowledge/scope", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as {
        name: string;
        description: string;
      };
      storedScope = scope({ name: body.name, description: body.description });
      return fulfill(
        route,
        { scope: storedScope, data_plane: dataPlane("ready") },
        201,
      );
    }
    return fulfill(route, {
      scope: storedScope,
      data_plane: dataPlane("ready"),
    });
  });

  await page.goto("/workspace/knowledge");
  await page.getByTestId("knowledge-scope-name").fill("Product handbook");
  await page
    .getByTestId("knowledge-scope-description")
    .fill("One source of product truth");
  await page.getByTestId("knowledge-create").click();

  await expect(page.getByTestId("knowledge-documents-card")).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-card")).toHaveCount(0);
  await expect(page.getByText("Product handbook", { exact: true })).toHaveCount(
    1,
  );
  await expect(
    page.getByText("Documents").first().locator("..").getByText("4"),
  ).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("knowledge-documents-card")).toBeVisible();
  await expect(page.getByText("Product handbook", { exact: true })).toHaveCount(
    1,
  );
});

test("shows create loading and ignores repeated create clicks", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let createRequests = 0;
  await page.route("**/api/knowledge/scope", async (route) => {
    if (route.request().method() !== "POST") {
      return fulfill(route, { scope: null, data_plane: dataPlane("ready") });
    }
    createRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 250));
    return fulfill(
      route,
      { scope: scope(), data_plane: dataPlane("ready") },
      201,
    );
  });

  await page.goto("/workspace/knowledge");
  await page.getByTestId("knowledge-scope-name").fill("Product knowledge");
  const button = page.getByTestId("knowledge-create");
  await button.dispatchEvent("click");
  await button.dispatchEvent("click");

  await expect(button).toBeDisabled();
  await expect(button).toHaveText(/Creating/);
  await expect(page.getByTestId("knowledge-documents-card")).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-card")).toHaveCount(0);
  expect(createRequests).toBe(1);
});

test("surfaces a recoverable create error and retries the same create", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let attempts = 0;
  await page.route("**/api/knowledge/scope", (route) => {
    if (route.request().method() !== "POST") {
      return fulfill(route, { scope: null, data_plane: dataPlane("ready") });
    }
    attempts += 1;
    if (attempts === 1) {
      return fulfill(
        route,
        {
          detail: {
            code: "knowledge_data_plane_unavailable",
            message: "LightRAG temporarily went offline.",
            status: "offline",
          },
        },
        503,
      );
    }
    return fulfill(
      route,
      { scope: scope(), data_plane: dataPlane("ready") },
      201,
    );
  });

  await page.goto("/workspace/knowledge");
  await page.getByTestId("knowledge-scope-name").fill("Product knowledge");
  await page.getByTestId("knowledge-create").click();
  await expect(
    page.getByText("LightRAG temporarily went offline."),
  ).toBeVisible();
  await expect(page.getByTestId("knowledge-create-error")).toBeVisible();
  await page.getByRole("button", { name: "Retry create" }).click();
  await expect(page.getByTestId("knowledge-documents-card")).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-card")).toHaveCount(0);
  expect(attempts).toBe(2);
});

test("shows a retryable request error without removing the knowledge entry", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/knowledge/scope", (route) =>
    route.fulfill({ status: 503, body: "" }),
  );

  await page.goto("/workspace/knowledge");

  const sidebar = page.locator("[data-sidebar='sidebar']");
  await expect(sidebar.locator("a[href='/workspace/knowledge']")).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByText("Knowledge-base status is unavailable"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});
