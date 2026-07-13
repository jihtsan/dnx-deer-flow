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

    await expect(page.getByTestId("knowledge-status")).toHaveText(
      STATUS_LABELS[status],
    );
    await expect(page.getByTestId("knowledge-empty-state")).toBeVisible();
    await expect(
      page.getByText(`Visible operator reason for ${status}.`),
    ).toBeVisible();
    await expect(page.getByText("v1.5.2-4-gab86f430")).toBeVisible();
    await expect(page.getByText("Single startup workspace")).toBeVisible();
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

test("creates, edits, disables, enables, and survives refresh", async ({
  page,
}) => {
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
    if (request.method() === "PATCH") {
      const body = request.postDataJSON() as Record<string, unknown>;
      storedScope = scope({
        ...storedScope,
        ...body,
        available_for_retrieval:
          typeof body.enabled === "boolean"
            ? body.enabled
            : storedScope?.available_for_retrieval,
        updated_at: "2026-07-13T02:00:00Z",
      });
      return fulfill(route, {
        scope: storedScope,
        data_plane: dataPlane("ready"),
      });
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

  await expect(page.getByTestId("knowledge-scope-card")).toBeVisible();
  await expect(
    page.getByText("Product handbook", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Documents").locator("..").getByText("4"),
  ).toBeVisible();

  await page.getByTestId("knowledge-scope-name").fill("Product knowledge v2");
  await page
    .getByTestId("knowledge-scope-description")
    .fill("Updated description");
  await page.getByTestId("knowledge-save").click();
  await expect(
    page.getByText("Product knowledge v2", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Changes saved.")).toBeVisible();

  await page.getByTestId("knowledge-scope-enabled").click();
  await expect(page.getByText("Disabled", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Knowledge retrieval will treat this scope as unavailable."),
  ).toBeVisible();
  await page.getByTestId("knowledge-scope-enabled").click();
  await expect(page.getByText("Enabled", { exact: true })).toBeVisible();

  await page.reload();
  await expect(
    page.getByText("Product knowledge v2", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-description")).toHaveValue(
    "Updated description",
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
  await expect(page.getByTestId("knowledge-scope-card")).toBeVisible();
  expect(createRequests).toBe(1);
});

test("shows save loading and ignores repeated edit submissions", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let patchRequests = 0;
  let storedScope = scope();
  await page.route("**/api/knowledge/scope", async (route) => {
    if (route.request().method() !== "PATCH") {
      return fulfill(route, {
        scope: storedScope,
        data_plane: dataPlane("ready"),
      });
    }
    patchRequests += 1;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    await new Promise((resolve) => setTimeout(resolve, 250));
    storedScope = scope({ ...storedScope, ...body });
    return fulfill(route, {
      scope: storedScope,
      data_plane: dataPlane("ready"),
    });
  });

  await page.goto("/workspace/knowledge");
  await page
    .getByTestId("knowledge-scope-description")
    .fill("A new description");
  const save = page.getByTestId("knowledge-save");
  await save.dispatchEvent("click");
  await save.dispatchEvent("click");

  await expect(save).toBeDisabled();
  await expect(save).toHaveText(/Saving/);
  await expect(page.getByTestId("knowledge-save-success")).toBeVisible();
  expect(patchRequests).toBe(1);
});

test("surfaces a recoverable save error and retries the same create", async ({
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
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByTestId("knowledge-scope-card")).toBeVisible();
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
