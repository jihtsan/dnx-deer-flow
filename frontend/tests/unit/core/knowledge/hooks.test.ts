import { expect, test } from "@rstest/core";

import {
  getKnowledgeDocumentsRefetchInterval,
  KNOWLEDGE_DOCUMENT_POLL_INTERVAL_MS,
  upsertKnowledgeDocument,
} from "@/core/knowledge/hooks";
import type {
  KnowledgeDocument,
  KnowledgeDocumentStatus,
} from "@/core/knowledge/types";

function document(
  status: KnowledgeDocumentStatus,
  id = `doc-${status}`,
): KnowledgeDocument {
  return {
    id,
    original_filename: `${status}.md`,
    content_type: "text/markdown",
    size_bytes: 42,
    status,
    lightrag_tracking_id: status === "pending" ? null : `track-${status}`,
    failure_code: status === "failed" ? "index_failed" : null,
    failure_reason: status === "failed" ? "索引失败" : null,
    ingestion_job_id: `job-${status}`,
    created_at: "2026-07-13T01:00:00Z",
    updated_at: "2026-07-13T01:01:00Z",
    completed_at:
      status === "ready" || status === "failed" ? "2026-07-13T01:01:00Z" : null,
  };
}

test.each(["pending", "indexing"] as const)(
  "polls while a document is %s",
  (status) => {
    expect(
      getKnowledgeDocumentsRefetchInterval({ documents: [document(status)] }),
    ).toBe(KNOWLEDGE_DOCUMENT_POLL_INTERVAL_MS);
  },
);

test.each(["ready", "failed"] as const)(
  "stops polling after every document reaches %s",
  (status) => {
    expect(
      getKnowledgeDocumentsRefetchInterval({ documents: [document(status)] }),
    ).toBe(false);
  },
);

test("does not poll an empty or not-yet-loaded list", () => {
  expect(getKnowledgeDocumentsRefetchInterval(undefined)).toBe(false);
  expect(getKnowledgeDocumentsRefetchInterval({ documents: [] })).toBe(false);
});

test("places an accepted upload in the list immediately and replaces replays", () => {
  const pending = document("pending", "doc-stable");
  const indexing = document("indexing", "doc-stable");
  const ready = document("ready", "doc-ready");

  const initial = upsertKnowledgeDocument(undefined, pending);
  expect(initial.documents).toEqual([pending]);

  const replayed = upsertKnowledgeDocument(
    { documents: [pending, ready] },
    indexing,
  );
  expect(replayed.documents).toEqual([indexing, ready]);
});
