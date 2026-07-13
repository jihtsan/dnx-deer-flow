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
    ingestion: {
      status:
        status === "ready"
          ? "succeeded"
          : status === "failed"
            ? "dead"
            : status === "indexing"
              ? "leased"
              : "pending",
      attempt_count: status === "pending" ? 0 : 1,
      max_attempts: 5,
      last_attempt_at: status === "pending" ? null : "2026-07-13T01:01:00Z",
      next_attempt_at: status === "pending" ? "2026-07-13T01:00:00Z" : null,
      last_error_code: status === "failed" ? "lightrag_timeout" : null,
      last_error_message:
        status === "failed" ? "The knowledge service timed out." : null,
      manual_retry_count: 0,
      retry_allowed: status === "failed",
    },
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

async function mockWorkspaceAPI(page: Page) {
  mockLangGraphAPI(page);
  await page.waitForTimeout(0);
}

test("selects and uploads a document once, then shows tracking information", async ({
  page,
}) => {
  await mockWorkspaceAPI(page);
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
  await mockWorkspaceAPI(page);
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
  await mockWorkspaceAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", (route) => {
    listRequests += 1;
    const status: DocumentStatus =
      listRequests <= 2 ? "pending" : listRequests <= 4 ? "indexing" : "ready";
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
  await mockWorkspaceAPI(page);
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
    page
      .getByTestId("knowledge-document-doc-stable")
      .getByText("The knowledge service timed out."),
  ).toBeVisible({ timeout: 8_000 });
  const terminalRequestCount = listRequests;
  await page.waitForTimeout(2_500);
  expect(listRequests).toBe(terminalRequestCount);
});

test("shows retry_wait diagnostics and the next automatic retry time", async ({
  page,
}) => {
  await mockWorkspaceAPI(page);
  await mockScope(page);
  const retrying = document("pending", {
    ingestion: {
      ...document("pending").ingestion,
      status: "retry_wait",
      attempt_count: 2,
      last_attempt_at: "2026-07-13T01:02:00Z",
      next_attempt_at: "2026-07-13T01:05:00Z",
      last_error_code: "lightrag_rate_limited",
      last_error_message: "The knowledge service is rate limited.",
    },
  });
  await page.route("**/api/knowledge/documents", (route) =>
    fulfill(route, { documents: [retrying] }),
  );

  await page.goto("/workspace/knowledge");
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Waiting to retry");
  await expect(
    page.getByTestId("knowledge-document-row-diagnostics-doc-stable"),
  ).toContainText("lightrag_rate_limited");
  await expect(
    page.getByTestId("knowledge-document-row-diagnostics-doc-stable"),
  ).toContainText("The knowledge service is rate limited.");
  await expect(page.getByTestId("knowledge-document-attempts")).toHaveText(
    "2/5",
  );
  await expect(
    page.getByTestId("knowledge-document-next-retry"),
  ).not.toHaveText("Not available");
  await expect(page.getByTestId("knowledge-document-retry-wait")).toBeVisible();
});

test("guards duplicate manual retries, resumes polling, and restores ready after refresh", async ({
  page,
}) => {
  await mockWorkspaceAPI(page);
  await mockScope(page);
  let retryRequests = 0;
  let active = false;
  let ready = false;
  let retryKey = "";
  await page.route("**/api/knowledge/documents**", async (route) => {
    const request = route.request();
    if (request.url().endsWith("/retry")) {
      retryRequests += 1;
      retryKey = request.headers()["idempotency-key"] ?? "";
      await new Promise((resolve) => setTimeout(resolve, 250));
      active = true;
      return fulfill(
        route,
        {
          document: document("pending", {
            ingestion: {
              ...document("pending").ingestion,
              manual_retry_count: 1,
            },
          }),
          deduplicated: false,
        },
        202,
      );
    }
    if (active && !ready) {
      ready = true;
      return fulfill(route, {
        documents: [
          document("indexing", {
            ingestion: {
              ...document("indexing").ingestion,
              attempt_count: 1,
              manual_retry_count: 1,
            },
          }),
        ],
      });
    }
    return fulfill(route, {
      documents: [
        ready
          ? document("ready", {
              ingestion: {
                ...document("ready").ingestion,
                manual_retry_count: 1,
              },
            })
          : document("failed"),
      ],
    });
  });

  await page.goto("/workspace/knowledge");
  const retry = page.getByTestId("knowledge-document-retry-doc-stable");
  await retry.dispatchEvent("click");
  await retry.dispatchEvent("click");
  await expect(retry).toBeDisabled();
  await expect(retry).toHaveText("Retrying…");
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Ready", { timeout: 8_000 });
  expect(retryRequests).toBe(1);
  expect(retryKey).not.toBe("");

  await page.reload();
  await expect(
    page.getByTestId("knowledge-document-status-doc-stable"),
  ).toHaveText("Ready");
});

test("reuses the manual retry idempotency key after a network failure", async ({
  page,
}) => {
  await mockWorkspaceAPI(page);
  await mockScope(page);
  const retryKeys: string[] = [];
  let retryRequests = 0;
  await page.route("**/api/knowledge/documents**", (route) => {
    if (!route.request().url().endsWith("/retry")) {
      return fulfill(route, {
        documents: [document(retryRequests >= 2 ? "pending" : "failed")],
      });
    }
    retryRequests += 1;
    retryKeys.push(route.request().headers()["idempotency-key"] ?? "");
    if (retryRequests === 1) {
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
  const retry = page.getByTestId("knowledge-document-retry-doc-stable");
  await retry.click();
  await expect(
    page.getByTestId("knowledge-document-retry-error"),
  ).toContainText("Try again.");
  await retry.click();
  await expect(retry).toBeHidden();

  expect(retryRequests).toBe(2);
  expect(retryKeys[0]).not.toBe("");
  expect(retryKeys[1]).toBe(retryKeys[0]);
});

test("stops active document polling when the page unmounts", async ({
  page,
}) => {
  await mockWorkspaceAPI(page);
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
    await mockWorkspaceAPI(page);
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
  await mockWorkspaceAPI(page);
  await mockScope(page);
  let listRequests = 0;
  await page.route("**/api/knowledge/documents", async (route) => {
    listRequests += 1;
    if (listRequests <= 2) {
      await new Promise((resolve) => setTimeout(resolve, 1_500));
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
    { name: "locale", value: "zh-CN", domain: "localhost", path: "/" },
  ]);
  await mockWorkspaceAPI(page);
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
