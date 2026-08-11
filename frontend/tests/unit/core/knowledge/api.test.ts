import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  createKnowledgeDirectory,
  deleteKnowledgeDocument,
  deleteKnowledgeDirectory,
  fetchKnowledgeDirectories,
  fetchKnowledgeGlobalGraph,
  fetchKnowledgeGraphLabels,
  searchKnowledgeGraph,
  createKnowledgeScope,
  fetchKnowledgeBaseFeature,
  fetchKnowledgeDocuments,
  fetchKnowledgeScope,
  KnowledgeDocumentRequestError,
  KnowledgeScopeRequestError,
  retryKnowledgeDocument,
  moveKnowledgeDocument,
  renameKnowledgeDirectory,
  retrieveKnowledge,
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
    directory_id: null,
    original_filename: `${status}.md`,
    content_type: "text/markdown",
    size_bytes: 42,
    content_length: null,
    status,
    lightrag_tracking_id: status === "pending" ? null : `track-${status}`,
    failure_code: status === "failed" ? "index_failed" : null,
    failure_reason: status === "failed" ? "索引失败，请检查文档格式。" : null,
    ingestion_job_id: `job-${status}`,
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:01:00Z",
    completed_at:
      status === "ready" || status === "failed" ? "2026-07-13T01:01:00Z" : null,
    progress: {
      stage:
        status === "ready"
          ? "processed"
          : status === "failed"
            ? "failed"
            : status === "indexing"
              ? "processing"
              : "pending",
      chunks_count: status === "pending" ? null : 5,
      stage_updated_at: "2026-07-13T01:01:00Z",
    },
    source: "managed",
    original_available: true,
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
      last_error_message: status === "failed" ? "LightRAG 响应超时。" : null,
      manual_retry_count: 0,
      retry_allowed: status === "failed",
    },
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

  test("accepts a LightRAG metadata-only document without a fake ingestion job", async () => {
    const remote = {
      id: "remote-existing",
      directory_id: null,
      original_filename: "AI-V1.1.pptx",
      content_type:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      size_bytes: null,
      content_length: 4096,
      status: "ready",
      lightrag_tracking_id: "track-existing",
      failure_code: null,
      failure_reason: null,
      ingestion_job_id: null,
      created_at: "2026-07-10T08:00:00Z",
      updated_at: "2026-07-10T08:05:00Z",
      completed_at: null,
      source: "remote",
      original_available: false,
      ingestion: null,
      progress: {
        stage: "processed",
        chunks_count: 12,
        stage_updated_at: "2026-07-10T08:05:00Z",
      },
    };
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { documents: [remote] }),
    );

    await expect(fetchKnowledgeDocuments()).resolves.toEqual({
      documents: [remote],
    });
  });

  test.each([
    {},
    { documents: null },
    { documents: [{ ...document(), status: "mystery" }] },
    { documents: [{ ...document(), lightrag_tracking_id: 42 }] },
    { documents: [{ ...document(), ingestion_job_id: null }] },
    { documents: [{ ...document(), ingestion: null }] },
    { documents: [{ ...document(), progress: { stage: "mystery" } }] },
    {
      documents: [
        {
          ...document(),
          progress: { ...document().progress, chunks_count: -1 },
        },
      ],
    },
    { documents: [{ ...document(), source: "remote" }] },
    {
      documents: [
        {
          ...document(),
          ingestion: { ...document().ingestion, status: "lost" },
        },
      ],
    },
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
      uploadKnowledgeDocument(file, "upload-idempotency-1", "dir-guides"),
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
    expect(body.get("directory_id")).toBe("dir-guides");
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

  test("retries one failed document with a stable idempotency key", async () => {
    const accepted = { document: document("pending"), deduplicated: false };
    mockedFetch.mockResolvedValueOnce(jsonResponse(202, accepted));

    await expect(
      retryKnowledgeDocument("doc/encoded", "retry-idempotency-1"),
    ).resolves.toEqual(accepted);

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/documents/doc%2Fencoded/retry",
      {
        method: "POST",
        headers: { "Idempotency-Key": "retry-idempotency-1" },
      },
    );
  });

  test("deletes one encoded document without sending a request body", async () => {
    mockedFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(
      deleteKnowledgeDocument("doc/encoded"),
    ).resolves.toBeUndefined();

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/documents/doc%2Fencoded",
      { method: "DELETE" },
    );
  });

  test("preserves a stable deletion error for the confirmation dialog", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(409, {
        detail: {
          code: "knowledge_document_delete_conflict",
          message: "The document is still being processed.",
        },
      }),
    );

    const error = await deleteKnowledgeDocument("doc-stable").catch(
      (cause: unknown) => cause,
    );

    expect(error).toBeInstanceOf(KnowledgeDocumentRequestError);
    expect(error).toMatchObject({
      status: 409,
      code: "knowledge_document_delete_conflict",
      message: "The document is still being processed.",
    });
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

describe("Knowledge directories API", () => {
  const directory = {
    id: "dir-guides",
    parent_id: null,
    name: "产品手册",
    document_count: 1,
    child_count: 0,
    created_at: "2026-07-14T08:00:00Z",
    updated_at: "2026-07-14T08:00:00Z",
  };

  test("lists, creates, renames and deletes directories", async () => {
    mockedFetch
      .mockResolvedValueOnce(jsonResponse(200, { directories: [directory] }))
      .mockResolvedValueOnce(jsonResponse(201, directory))
      .mockResolvedValueOnce(
        jsonResponse(200, { ...directory, name: "最新手册" }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(fetchKnowledgeDirectories()).resolves.toEqual({
      directories: [directory],
    });
    await createKnowledgeDirectory({ name: "产品手册", parent_id: null });
    await renameKnowledgeDirectory("dir-guides", "最新手册");
    await deleteKnowledgeDirectory("dir-guides");

    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/knowledge/directories",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "产品手册", parent_id: null }),
      },
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      3,
      "/api/knowledge/directories/dir-guides",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "最新手册" }),
      },
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      4,
      "/api/knowledge/directories/dir-guides",
      { method: "DELETE" },
    );
  });

  test("moves a document into a directory", async () => {
    const moved = { ...document("ready"), directory_id: "dir-guides" };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, moved));

    await expect(
      moveKnowledgeDocument("doc/ready", "dir-guides"),
    ).resolves.toEqual(moved);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/documents/doc%2Fready",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ directory_id: "dir-guides" }),
      },
    );
  });
});

describe("Knowledge graph API", () => {
  test("loads popular labels with a bounded limit and forwards cancellation", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { labels: ["能源系统", "光储充场站"] }),
    );
    const controller = new AbortController();

    await expect(
      fetchKnowledgeGraphLabels(controller.signal, 12),
    ).resolves.toEqual({ labels: ["能源系统", "光储充场站"] });
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/graph/labels?limit=12",
      { signal: controller.signal },
    );
  });

  test.each([{}, { labels: null }, { labels: ["能源系统", 42] }])(
    "rejects a malformed graph-label response",
    async (payload) => {
      mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

      await expect(fetchKnowledgeGraphLabels()).rejects.toThrow(
        "Invalid knowledge graph labels response",
      );
    },
  );

  test.each([0, 51, 1.5])(
    "rejects an invalid graph-label limit %s",
    (limit) => {
      expect(() => fetchKnowledgeGraphLabels(undefined, limit)).toThrow(
        "Knowledge graph label limit must be an integer between 1 and 50",
      );
      expect(mockedFetch).not.toHaveBeenCalled();
    },
  );

  test("loads the normalized global graph and forwards cancellation", async () => {
    const graph = {
      nodes: [
        {
          id: "能源系统",
          label: "能源系统",
          entity_type: "system",
          description: "园区能源系统",
          file_path: "产品手册.pdf",
        },
      ],
      edges: [
        {
          id: "能源系统::包含::储能站",
          source: "能源系统",
          target: "储能站",
          relation_type: "包含",
          description: "能源系统包含储能站",
          keywords: "能源,储能",
          weight: 0.9,
          file_path: "产品手册.pdf",
        },
      ],
      is_truncated: false,
      total_labels: 12,
      components: 2,
    };
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, graph));
    const controller = new AbortController();

    await expect(fetchKnowledgeGlobalGraph(controller.signal)).resolves.toEqual(
      graph,
    );
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/graph/global?max_nodes=5000",
      { signal: controller.signal },
    );
  });

  test.each([
    {},
    { nodes: [], edges: [], is_truncated: "no" },
    {
      nodes: [
        {
          id: "node",
          label: "Node",
          entity_type: "system",
          description: "Description",
          file_path: 42,
        },
      ],
      edges: [],
      is_truncated: false,
    },
    {
      nodes: [],
      edges: [
        {
          id: "edge",
          source: "a",
          target: "b",
          relation_type: "related",
          description: "Description",
          keywords: "related",
          weight: "heavy",
          file_path: "source.pdf",
        },
      ],
      is_truncated: false,
    },
  ])("rejects a malformed graph response", async (payload) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

    await expect(fetchKnowledgeGlobalGraph()).rejects.toThrow(
      "Invalid knowledge graph response",
    );
  });

  test("searches labels without requesting another graph", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { labels: ["储能站"] }),
    );
    const controller = new AbortController();
    await expect(
      searchKnowledgeGraph("  储能 & 调度 ", controller.signal),
    ).resolves.toEqual({ labels: ["储能站"] });
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/knowledge/graph/search?q=%E5%82%A8%E8%83%BD+%26+%E8%B0%83%E5%BA%A6&limit=20",
      { signal: controller.signal },
    );
  });
});

describe("Knowledge retrieval API", () => {
  const retrieval = {
    entities: [
      {
        entity_name: "储能站",
        entity_type: "facility",
        description: "站内储能设施",
        file_path: "场站手册.pdf",
        reference_id: "1",
      },
    ],
    relationships: [
      {
        src_id: "储能站",
        tgt_id: "充电站",
        description: "协同调度",
        keywords: "协同,调度",
        weight: 1,
        file_path: "场站手册.pdf",
        reference_id: "1",
      },
    ],
    chunks: [
      {
        chunk_id: "chunk-1",
        content: "场站采用光储充协同调度。",
        file_path: "场站手册.pdf",
        reference_id: "1",
      },
    ],
    references: [{ reference_id: "1", file_path: "场站手册.pdf" }],
    metadata: {
      query_mode: "hybrid",
      keywords: {
        high_level: ["协同调度"],
        low_level: ["储能站", "充电站"],
      },
      processing_info: {
        total_entities_found: 3,
        total_relations_found: 2,
        entities_after_truncation: 1,
        relations_after_truncation: 1,
        final_chunks_count: 1,
      },
    },
  };

  test("posts a normalized bounded request and parses structured results", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, retrieval));

    await expect(
      retrieveKnowledge({
        query: "  光储充场站如何协同调度？  ",
        mode: "hybrid",
        top_k: 12,
        chunk_top_k: 8,
        max_total_tokens: 4096,
      }),
    ).resolves.toEqual(retrieval);
    expect(mockedFetch).toHaveBeenCalledWith("/api/knowledge/retrieval", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: "光储充场站如何协同调度？",
        mode: "hybrid",
        top_k: 12,
        chunk_top_k: 8,
        max_total_tokens: 4096,
      }),
    });
  });

  test("omits unset optional limits", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, retrieval));

    await retrieveKnowledge({ query: "场站调度策略", mode: "mix" });

    const [, init] = mockedFetch.mock.calls[0] ?? [];
    expect(init?.body).toBe(
      JSON.stringify({ query: "场站调度策略", mode: "mix" }),
    );
  });

  test("forwards an abort signal to the retrieval request", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, retrieval));
    const controller = new AbortController();

    await retrieveKnowledge(
      { query: "场站调度策略", mode: "mix" },
      controller.signal,
    );

    const [, init] = mockedFetch.mock.calls[0] ?? [];
    expect(init?.signal).toBe(controller.signal);
  });

  test.each([
    { ...retrieval, entities: [{ ...retrieval.entities[0], entity_name: 1 }] },
    {
      ...retrieval,
      relationships: [{ ...retrieval.relationships[0], weight: "1" }],
    },
    { ...retrieval, chunks: [{ ...retrieval.chunks[0], content: null }] },
    {
      ...retrieval,
      references: [{ ...retrieval.references[0], file_path: null }],
    },
    {
      ...retrieval,
      metadata: {
        ...retrieval.metadata,
        processing_info: {
          ...retrieval.metadata.processing_info,
          final_chunks_count: -1,
        },
      },
    },
  ])("rejects a malformed retrieval response", async (payload) => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, payload));

    await expect(
      retrieveKnowledge({ query: "场站调度策略", mode: "mix" }),
    ).rejects.toThrow("Invalid knowledge retrieval response");
  });

  test.each([
    [
      { query: "ab", mode: "mix" },
      "query must contain between 3 and 2000 characters",
    ],
    [
      { query: "场站调度", mode: "mix", top_k: 0 },
      "top_k must be an integer between 1 and 100",
    ],
    [
      { query: "场站调度", mode: "mix", chunk_top_k: 101 },
      "chunk_top_k must be an integer between 1 and 100",
    ],
    [
      { query: "场站调度", mode: "mix", max_total_tokens: 255 },
      "max_total_tokens must be an integer between 256 and 32000",
    ],
  ] as const)("rejects an invalid retrieval request", (input, message) => {
    expect(() => retrieveKnowledge(input)).toThrow(message);
    expect(mockedFetch).not.toHaveBeenCalled();
  });
});
