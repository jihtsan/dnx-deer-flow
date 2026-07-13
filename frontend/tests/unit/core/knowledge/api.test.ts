import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { fetchKnowledgeBaseFeature } from "@/core/knowledge/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("fetchKnowledgeBaseFeature", () => {
  test.each([
    "unconfigured",
    "disabled",
    "offline",
    "incompatible",
    "ready",
  ] as const)("accepts the %s feature state", async (status) => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        agents_api: { enabled: true },
        knowledge_base: {
          enabled: status !== "disabled",
          status,
          reason: `LightRAG is ${status}.`,
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
        },
      }),
    );

    await expect(fetchKnowledgeBaseFeature()).resolves.toMatchObject({
      enabled: status !== "disabled",
      status,
      reason: `LightRAG is ${status}.`,
      diagnostics: {
        workspace_mode: "single",
        expected_tag: "v1.5.2-4-gab86f430",
        expected_commit: "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
      },
    });
  });

  test("rejects an unavailable feature endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response("", { status: 503, statusText: "Service Unavailable" }),
    );

    await expect(fetchKnowledgeBaseFeature()).rejects.toThrow(
      "Failed to load knowledge-base status: Service Unavailable",
    );
  });

  test.each([
    {},
    { knowledge_base: null },
    {
      knowledge_base: {
        enabled: true,
        status: "mystery",
        reason: "Unknown",
        diagnostics: {},
      },
    },
    {
      knowledge_base: {
        enabled: true,
        status: "ready",
        reason: "Ready",
        diagnostics: {
          workspace_mode: "per-request",
          expected_tag: "v1.5.2-4-gab86f430",
          expected_commit: "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
          expected_core_version: "1.5.2",
          expected_api_version: "0308",
        },
      },
    },
  ])("rejects a malformed feature payload", async (payload) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

    await expect(fetchKnowledgeBaseFeature()).rejects.toThrow(
      "Invalid knowledge-base feature response",
    );
  });
});
