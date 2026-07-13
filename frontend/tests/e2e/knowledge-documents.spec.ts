import { expect, test, type Page, type Route } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

type KnowledgeStatus =
  | "unconfigured"
  | "disabled"
  | "offline"
  | "incompatible"
  | "ready";
type DocumentStatus = "pending" | "indexing" | "ready" | "failed";

function dataPlane(status: KnowledgeStatus = "ready") {
  const ready = status === "ready";
  return {
    enabled: status !== "disabled" && status !== "unconfigured",
    status,
    reason: ready ? "LightRAG is ready." : `LightRAG is ${status}.`,
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
  };
}

function scope(enabled = true) {
  return {
    id: "ks-stable",
    name: "Product knowledge",
    description: "Shared product documentation",
    enabled,
    available_for_retrieval: enabled,
    document_stats: { total: 1, pending: 1, indexing: 0, ready: 0, failed: 0 },
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:00:00Z",
  };
}

function document(
  status: DocumentStatus,
  overrides: Record<string, unknown> = {},
) {
  return {
    id: "doc-stable",
    original_filename: "product-handbook.md",
    content_type: "text/markdown",
    size_bytes: 42,
    status,
    lightrag_tracking_id: status === "pending" ? null : "track-stable",
    failure_code: status === "failed" ? "index_failed" : null,
    failure_reason:
      status === "failed" ? "The document could not be indexed." : null,
    ingestion_job_id: "job-stable",
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:01:00Z",
    completed_at:
      status === "ready" || status === "failed" ? "2026-07-13T01:01:00Z" : null,
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

async function mockScope(
  page: Page,
  options: { enabled?: boolean; status?: KnowledgeStatus } = {},
) {
  await page.route("**/api/knowledge/scope", (route) =>
    fulfill(route, {
      scope: scope(options.enabled ?? true),
      data_plane: dataPlane(options.status ?? "ready"),
    }),
  );
}

test("selects and uploads a document once, then shows tracking information", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  let uploadRequests = 0;
  let idempotencyKey = "";
  let documents: ReturnType<typeof document>[] = [];
  await page.route("**/api/knowledge/documents", async (route) => {
    if (route.request().method() === "GET") {
      return fulfill(route, { documents });
    }
    uploadRequests += 1;
    idempotencyKey = route.request().headers()["idempotency-key"] ?? "";
    await new Promise((resolve) => setTimeout(resolve, 250));
    const accepted = document("pending", {
      lightrag_tracking_id: "track-uploaded",
    });
    documents = [accepted];
    return fulfill(route, { document: accepted, deduplicated: false }, 202);
  });

  await page.goto("/workspace/knowledge");
  await page.getByTestId("knowledge-document-input").setInputFiles({
    name: "product-handbook.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Product handbook"),
  });
  await expect(page.getByTestId("knowledge-selected-file")).toContainText(
    "product-handbook.md",
  );
  const upload = page.getByTestId("knowledge-upload");
  await upload.dispatchEvent("click");
  await upload.dispatchEvent("click");

  await expect(upload).toBeDisabled();
  await expect(upload).toHaveText(/Uploading/);
  await expect(page.getByTestId("knowledge-upload-success")).toBeVisible();
  await expect(page.getByTestId("knowledge-document-doc-stable")).toContainText(
    "track-uploaded",
  );
  expect(uploadRequests).toBe(1);
  expect(idempotencyKey).not.toBe("");
});

test("reuses the idempotency key when a failed upload is retried", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  const idempotencyKeys: string[] = [];
  let uploadAttempts = 0;
  await page.route("**/api/knowledge/documents", (route) => {
    if (route.request().method() === "GET") {
      return fulfill(route, { documents: [] });
    }
    uploadAttempts += 1;
    idempotencyKeys.push(route.request().headers()["idempotency-key"] ?? "");
    if (uploadAttempts === 1) {
      return fulfill(
        route,
        { detail: { code: "temporarily_unavailable", message: "Try again." } },
        503,
      );
    }
    return fulfill(
      route,
      { document: document("pending"), deduplicated: true },
      202,
    );
  });

  await page.goto("/workspace/knowledge");
  await page.getByTestId("knowledge-document-input").setInputFiles({
    name: "product-handbook.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Product handbook"),
  });
  await page.getByTestId("knowledge-upload").click();
  await expect(page.getByTestId("knowledge-upload-error")).toContainText(
    "Try again.",
  );
  await page.getByTestId("knowledge-upload").click();
  await expect(page.getByTestId("knowledge-upload-success")).toBeVisible();

  expect(uploadAttempts).toBe(2);
  expect(idempotencyKeys[0]).not.toBe("");
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
});

test("polls pending and indexing documents, stops at ready, and restores after refresh", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", (route) => {
    listRequests += 1;
    const status: DocumentStatus =
      listRequests === 1
        ? "pending"
        : listRequests === 2
          ? "indexing"
          : "ready";
    return fulfill(route, { documents: [document(status)] });
  });

  await page.goto("/workspace/knowledge");
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Accepted, waiting for indexing");
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Indexing", { timeout: 8_000 });
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Ready", { timeout: 8_000 });

  const terminalRequestCount = listRequests;
  await page.waitForTimeout(2_500);
  expect(listRequests).toBe(terminalRequestCount);

  await page.reload();
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Ready");
});

test("shows a failed reason and stops polling", async ({ page }) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", (route) => {
    listRequests += 1;
    return fulfill(route, {
      documents: [document(listRequests === 1 ? "pending" : "failed")],
    });
  });

  await page.goto("/workspace/knowledge");
  await expect(
    page.getByText("The document could not be indexed."),
  ).toBeVisible({
    timeout: 8_000,
  });
  const terminalRequestCount = listRequests;
  await page.waitForTimeout(2_500);
  expect(listRequests).toBe(terminalRequestCount);
});

test("stops active document polling when the page unmounts", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", (route) => {
    listRequests += 1;
    return fulfill(route, { documents: [document("pending")] });
  });

  await page.goto("/workspace/knowledge");
  await expect.poll(() => listRequests, { timeout: 8_000 }).toBeGreaterThan(1);
  await page.goto("/workspace/chats/new");
  await page.waitForTimeout(100);
  const afterUnmount = listRequests;
  await page.waitForTimeout(2_500);
  expect(listRequests).toBe(afterUnmount);
});

for (const scenario of [
  {
    name: "feature disabled",
    enabled: true,
    status: "disabled" as const,
    reason: "Knowledge-base uploads are disabled by configuration.",
  },
  {
    name: "scope disabled",
    enabled: false,
    status: "ready" as const,
    reason: "Enable the Knowledge Scope before uploading documents.",
  },
  {
    name: "LightRAG offline",
    enabled: true,
    status: "offline" as const,
    reason: "LightRAG must be ready before uploading documents.",
  },
]) {
  test(`disables upload when ${scenario.name}`, async ({ page }) => {
    mockLangGraphAPI(page);
    await mockScope(page, {
      enabled: scenario.enabled,
      status: scenario.status,
    });
    await page.route("**/api/knowledge/documents", (route) =>
      fulfill(route, { documents: [] }),
    );

    await page.goto("/workspace/knowledge");
    await expect(page.getByTestId("knowledge-upload")).toBeDisabled();
    await expect(page.getByTestId("knowledge-upload-unavailable")).toHaveText(
      scenario.reason,
    );
  });
}

test("shows document loading, error, retry, and empty states", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", async (route) => {
    listRequests += 1;
    if (listRequests === 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      return fulfill(
        route,
        { detail: { code: "temporarily_unavailable", message: "Try again." } },
        503,
      );
    }
    return fulfill(route, { documents: [] });
  });

  await page.goto("/workspace/knowledge");
  await expect(page.getByTestId("knowledge-documents-loading")).toBeVisible();
  await expect(page.getByTestId("knowledge-documents-error")).toContainText(
    "Try again.",
  );
  await page.getByTestId("knowledge-documents-retry").click();
  await expect(page.getByTestId("knowledge-documents-empty")).toBeVisible();
});

test("renders the document workflow in Chinese", async ({ page, context }) => {
  await context.addCookies([
    { name: "locale", value: "zh-CN", url: "http://localhost:3000" },
  ]);
  mockLangGraphAPI(page);
  await mockScope(page);
  await page.route("**/api/knowledge/documents", (route) =>
    fulfill(route, { documents: [document("pending")] }),
  );

  await page.goto("/workspace/knowledge");
  await expect(page.getByText("上传文档", { exact: true })).toBeVisible();
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("已接收，等待索引");
});
