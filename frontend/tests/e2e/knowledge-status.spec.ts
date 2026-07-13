import { expect, test, type Page } from "@playwright/test";

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

function mockKnowledgeStatus(page: Page, status: KnowledgeStatus) {
  const ready = status === "ready";
  return page.route("**/api/features", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agents_api: { enabled: true },
        knowledge_base: {
          enabled: ready,
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
          // Unknown backend fields must never become visible diagnostics.
          endpoint: "https://secret-lightrag.internal",
          api_key: "do-not-render",
        },
      }),
    }),
  );
}

for (const status of Object.keys(STATUS_LABELS) as KnowledgeStatus[]) {
  test(`keeps the knowledge entry visible and renders ${status}`, async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await mockKnowledgeStatus(page, status);

    await page.goto("/workspace/chats/new");
    const sidebar = page.locator("[data-sidebar='sidebar']");
    const knowledgeLink = sidebar.locator("a[href='/workspace/knowledge']");
    await expect(knowledgeLink).toBeVisible({ timeout: 15_000 });
    await knowledgeLink.click();

    await page.waitForURL("**/workspace/knowledge");
    await expect(
      page.getByRole("heading", { name: "Knowledge base" }),
    ).toBeVisible();
    await expect(page.getByTestId("knowledge-status")).toHaveText(
      STATUS_LABELS[status],
    );
    await expect(
      page.getByText(`Visible operator reason for ${status}.`),
    ).toBeVisible();
    await expect(page.getByText("v1.5.2-4-gab86f430")).toBeVisible();
    await expect(page.getByText("Single startup workspace")).toBeVisible();
    await expect(
      page.getByText("https://secret-lightrag.internal"),
    ).toHaveCount(0);
    await expect(page.getByText("do-not-render")).toHaveCount(0);
  });
}

test("shows a retryable error without removing the knowledge entry", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/features", (route) =>
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
