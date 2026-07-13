import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  createKnowledgeScope,
  fetchKnowledgeBaseFeature,
  fetchKnowledgeDocuments,
  fetchKnowledgeScope,
  KnowledgeDocumentRequestError,
  KnowledgeScopeRequestError,
  uploadKnowledgeDocument,
  updateKnowledgeScope,
} from "@/core/knowledge/api";

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

const readyDataPlane = {
  enabled: true,
  status: "ready",
  reason: "LightRAG is ready.",
  diagnostics: {
    workspace_mode: "single",
    expected_tag: "v1.5.2-4-gab86f430",
    expected_commit: "ab86f4303aafb2e66543ce3e0c735e8b698141ab",
    expected_core_version: "1.5.2",
    expected_api_version: "0308",
    observed_core_version: "1.5.2",
    observed_api_version: "0308",
    service_status: "healthy",
  },
};

const scope = {
  id: "ks-stable",
  name: "产品知识",
  description: "统一产品资料",
  enabled: true,
  available_for_retrieval: true,
  document_stats: { total: 0, pending: 0, indexing: 0, ready: 0, failed: 0 },
  created_at: "2026-07-13T01:00:00Z",
  updated_at: "2026-07-13T01:00:00Z",
};

function document(
  status: "pending" | "indexing" | "ready" | "failed" = "pending",
) {
  return {
    id: `doc-${status}`,
    original_filename: `${status}.md`,
    content_type: "text/markdown",
    size_bytes: 42,
    status,
    lightrag_tracking_id: status === "pending" ? null : `track-${status}`,
    failure_code: status === "failed" ? "index_failed" : null,
    failure_reason: status === "failed" ? "索引失败，请检查文档格式。" : null,
    ingestion_job_id: `job-${status}`,
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:01:00Z",
    completed_at:
      status === "ready" || status === "failed" ? "2026-07-13T01:01:00Z" : null,
  };
}

describe("Knowledge Scope API", () => {
  test("reads the singleton envelope, including the empty state", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { scope: null, data_plane: readyDataPlane }),
    );

    await expect(fetchKnowledgeScope()).resolves.toEqual({
      scope: null,
      data_plane: readyDataPlane,
    });
    expect(mockedFetch).toHaveBeenCalledWith("/api/knowledge/scope");
  });

  test("creates and updates without exposing a client-selected scope id", async () => {
    mockedFetch
      .mockResolvedValueOnce(
        jsonResponse(201, { scope, data_plane: readyDataPlane }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          scope: { ...scope, enabled: false, available_for_retrieval: false },
          data_plane: readyDataPlane,
        }),
      );

    await createKnowledgeScope({
      name: "产品知识",
      description: "统一产品资料",
      enabled: true,
    });
    await updateKnowledgeScope({ enabled: false });

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/api/knowledge/scope",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "产品知识",
          description: "统一产品资料",
          enabled: true,
        }),
      }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/knowledge/scope",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ enabled: false }),
      }),
    );
  });

  test("preserves stable diagnostic errors for recovery UI", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(503, {
        detail: {
          code: "knowledge_data_plane_unavailable",
          message: "LightRAG is offline.",
          status: "offline",
        },
      }),
    );

    const error = await createKnowledgeScope({
      name: "产品知识",
      description: "",
      enabled: true,
    }).catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(KnowledgeScopeRequestError);
    expect(error).toMatchObject({
      status: 503,
      code: "knowledge_data_plane_unavailable",
      message: "LightRAG is offline.",
    });
  });

  test("preserves a gateway string detail for actionable recovery", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(503, {
        detail: "Knowledge Scope persistence is not available",
      }),
    );

    const error = await fetchKnowledgeScope().catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(KnowledgeScopeRequestError);
    expect(error).toMatchObject({
      status: 503,
      message: "Knowledge Scope persistence is not available",
    });
  });

  test.each([
    {},
    { scope: null, data_plane: null },
    { scope: { ...scope, document_stats: null }, data_plane: readyDataPlane },
  ])("rejects a malformed singleton envelope", async (payload) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

    await expect(fetchKnowledgeScope()).rejects.toThrow(
      "Invalid Knowledge Scope response",
    );
  });
});

describe("Knowledge documents API", () => {
  test("loads all four document states and forwards cancellation", async () => {
    const documents = [
      document("pending"),
      document("indexing"),
      document("ready"),
      document("failed"),
    ];
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { documents }));
    const controller = new AbortController();

    await expect(fetchKnowledgeDocuments(controller.signal)).resolves.toEqual({
      documents,
    });
    expect(mockedFetch).toHaveBeenCalledWith("/api/knowledge/documents", {
      signal: controller.signal,
    });
  });

  test.each([
    {},
    { documents: null },
    { documents: [{ ...document(), status: "mystery" }] },
    { documents: [{ ...document(), lightrag_tracking_id: 42 }] },
    { documents: [{ ...document(), ingestion_job_id: null }] },
  ])("rejects a malformed documents response", async (payload) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

    await expect(fetchKnowledgeDocuments()).rejects.toThrow(
      "Invalid knowledge documents response",
    );
  });

  test("uploads one file with a stable idempotency key", async () => {
    const accepted = { document: document("pending"), deduplicated: false };
    mockedFetch.mockResolvedValueOnce(jsonResponse(202, accepted));
    const file = new File(["# 产品手册"], "产品手册.md", {
      type: "text/markdown",
    });

    await expect(
      uploadKnowledgeDocument(file, "upload-idempotency-1"),
    ).resolves.toEqual(accepted);

    const [url, init] = mockedFetch.mock.calls[0] ?? [];
    expect(url).toBe("/api/knowledge/documents");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("Idempotency-Key")).toBe("upload-idempotency-1");
    expect(headers.has("Content-Type")).toBe(false);
    const body = init?.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("file")).toBe(file);
  });

  test("accepts an idempotent replay without creating another document", async () => {
    const accepted = { document: document("indexing"), deduplicated: true };
    mockedFetch.mockResolvedValueOnce(jsonResponse(202, accepted));

    await expect(
      uploadKnowledgeDocument(
        new File(["same"], "same.md", { type: "text/markdown" }),
        "replayed-key",
      ),
    ).resolves.toEqual(accepted);
  });

  test("preserves a stable upload error for actionable UI", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(503, {
        detail: {
          code: "knowledge_data_plane_unavailable",
          message: "LightRAG is offline.",
        },
      }),
    );

    const error = await uploadKnowledgeDocument(
      new File(["offline"], "offline.md"),
      "offline-key",
    ).catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(KnowledgeDocumentRequestError);
    expect(error).toMatchObject({
      status: 503,
      code: "knowledge_data_plane_unavailable",
      message: "LightRAG is offline.",
    });
  });
});
