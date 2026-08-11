import type { KnowledgeDirectory, KnowledgeDocument } from "./types";

export interface KnowledgeDirectoryNode extends KnowledgeDirectory {
  children: KnowledgeDirectoryNode[];
}

function byName(
  left: Pick<KnowledgeDirectory, "name" | "id">,
  right: Pick<KnowledgeDirectory, "name" | "id">,
): number {
  return left.name.localeCompare(right.name) || left.id.localeCompare(right.id);
}

export function buildKnowledgeDirectoryTree(
  directories: KnowledgeDirectory[],
): KnowledgeDirectoryNode[] {
  const nodes = new Map<string, KnowledgeDirectoryNode>(
    directories.map((directory) => [
      directory.id,
      { ...directory, children: [] },
    ]),
  );
  const roots: KnowledgeDirectoryNode[] = [];
  for (const directory of directories) {
    const node = nodes.get(directory.id);
    if (node === undefined) continue;
    const parent = directory.parent_id
      ? nodes.get(directory.parent_id)
      : undefined;
    if (parent === undefined || parent.id === node.id) {
      roots.push(node);
    } else {
      parent.children.push(node);
    }
  }
  for (const node of nodes.values()) node.children.sort(byName);
  return roots.sort(byName);
}

export function getKnowledgeDirectoryBreadcrumbs(
  directories: KnowledgeDirectory[],
  directoryId: string | null,
): KnowledgeDirectory[] {
  if (directoryId === null) return [];
  const byId = new Map(
    directories.map((directory) => [directory.id, directory]),
  );
  const breadcrumbs: KnowledgeDirectory[] = [];
  const visited = new Set<string>();
  let current = byId.get(directoryId);
  while (current !== undefined && !visited.has(current.id)) {
    visited.add(current.id);
    breadcrumbs.unshift(current);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return breadcrumbs;
}

export function filterKnowledgeDocumentsByDirectory(
  documents: KnowledgeDocument[],
  directoryId: string | null,
): KnowledgeDocument[] {
  return documents.filter((document) => document.directory_id === directoryId);
}

export function flattenKnowledgeDirectories(
  directories: KnowledgeDirectory[],
): Array<{ directory: KnowledgeDirectory; depth: number }> {
  const flattened: Array<{ directory: KnowledgeDirectory; depth: number }> = [];
  const visit = (nodes: KnowledgeDirectoryNode[], depth: number) => {
    for (const node of nodes) {
      flattened.push({ directory: node, depth });
      visit(node.children, depth + 1);
    }
  };
  visit(buildKnowledgeDirectoryTree(directories), 0);
  return flattened;
}
