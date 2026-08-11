import { expect, test } from "@rstest/core";

import {
  buildKnowledgeDirectoryTree,
  filterKnowledgeDocumentsByDirectory,
  getKnowledgeDirectoryBreadcrumbs,
} from "@/core/knowledge/tree";
import type {
  KnowledgeDirectory,
  KnowledgeDocument,
} from "@/core/knowledge/types";

const directories: KnowledgeDirectory[] = [
  {
    id: "child",
    parent_id: "root",
    name: "规程",
    document_count: 1,
    child_count: 0,
    created_at: "2026-07-14T08:01:00Z",
    updated_at: "2026-07-14T08:01:00Z",
  },
  {
    id: "orphan",
    parent_id: "missing",
    name: "待整理",
    document_count: 0,
    child_count: 0,
    created_at: "2026-07-14T08:02:00Z",
    updated_at: "2026-07-14T08:02:00Z",
  },
  {
    id: "root",
    parent_id: null,
    name: "工程资料",
    document_count: 0,
    child_count: 1,
    created_at: "2026-07-14T08:00:00Z",
    updated_at: "2026-07-14T08:00:00Z",
  },
];

function remoteDocument(directoryId: string | null): KnowledgeDocument {
  return {
    id: `remote-${directoryId ?? "root"}`,
    directory_id: directoryId,
    original_filename: "远端资料.pdf",
    content_type: "application/pdf",
    size_bytes: null,
    content_length: 128,
    status: "ready",
    lightrag_tracking_id: "track-remote",
    failure_code: null,
    failure_reason: null,
    ingestion_job_id: null,
    created_at: "2026-07-14T08:00:00Z",
    updated_at: "2026-07-14T08:00:00Z",
    completed_at: null,
    source: "remote",
    original_available: false,
    progress: null,
    ingestion: null,
  };
}

test("builds a stable tree from unordered directories and keeps orphans visible", () => {
  const tree = buildKnowledgeDirectoryTree(directories);

  expect(tree.map((node) => node.id)).toEqual(["root", "orphan"]);
  expect(tree[0]?.children.map((node) => node.id)).toEqual(["child"]);
});

test("returns root-to-leaf breadcrumbs for the active directory", () => {
  expect(
    getKnowledgeDirectoryBreadcrumbs(directories, "child").map(
      (directory) => directory.name,
    ),
  ).toEqual(["工程资料", "规程"]);
  expect(getKnowledgeDirectoryBreadcrumbs(directories, null)).toEqual([]);
});

test("filters only documents directly assigned to the selected directory", () => {
  const documents = [remoteDocument(null), remoteDocument("child")];

  expect(filterKnowledgeDocumentsByDirectory(documents, null)).toEqual([
    documents[0],
  ]);
  expect(filterKnowledgeDocumentsByDirectory(documents, "child")).toEqual([
    documents[1],
  ]);
});
