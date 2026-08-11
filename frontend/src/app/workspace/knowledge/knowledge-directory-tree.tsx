"use client";

import {
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildKnowledgeDirectoryTree,
  type KnowledgeDirectoryNode,
} from "@/core/knowledge/tree";
import type {
  KnowledgeDirectory,
  KnowledgeDocument,
} from "@/core/knowledge/types";
import { cn } from "@/lib/utils";

type EditState =
  | { mode: "create"; parentId: string | null }
  | { mode: "rename"; directory: KnowledgeDirectory }
  | null;

function statusDotClass(status: KnowledgeDocument["status"]): string {
  if (status === "ready") return "bg-emerald-500";
  if (status === "failed") return "bg-destructive";
  if (status === "indexing")
    return "bg-amber-500 animate-pulse motion-reduce:animate-none";
  return "bg-muted-foreground/50";
}

function FileRow({
  document,
  depth,
  active,
  statusLabel,
  onSelect,
}: {
  document: KnowledgeDocument;
  depth: number;
  active: boolean;
  statusLabel: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="treeitem"
      aria-selected={active}
      data-testid={`knowledge-document-row-${document.id}`}
      aria-current={active}
      title={`${document.original_filename} · ${statusLabel}`}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2 rounded-md py-1.5 pr-2.5 text-left text-[12.5px] transition-colors duration-150",
        active ? "bg-accent font-medium" : "hover:bg-accent/60",
      )}
      style={{ paddingLeft: `${depth * 16 + 30}px` }}
    >
      <FileTextIcon className="text-muted-foreground size-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">
        {document.original_filename}
      </span>
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          statusDotClass(document.status),
        )}
        aria-hidden
      />
    </button>
  );
}

function DirectoryRow({
  node,
  depth,
  documents,
  expanded,
  selectedDocumentId,
  statusLabel,
  onToggle,
  onSelectDirectory,
  onSelectDocument,
  onCreate,
  onRename,
  onDelete,
}: {
  node: KnowledgeDirectoryNode;
  depth: number;
  documents: KnowledgeDocument[];
  expanded: Set<string>;
  selectedDocumentId: string | null;
  statusLabel: (document: KnowledgeDocument) => string;
  onToggle: (id: string) => void;
  onSelectDirectory: (id: string) => void;
  onSelectDocument: (id: string) => void;
  onCreate: (parentId: string) => void;
  onRename: (directory: KnowledgeDirectory) => void;
  onDelete: (directoryId: string) => void;
}) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.documents;
  const isOpen = expanded.has(node.id);
  const files = documents.filter(
    (document) => document.directory_id === node.id,
  );
  return (
    <div role="treeitem" aria-selected={false} aria-expanded={isOpen}>
      <div
        className="group hover:bg-accent/60 flex items-center gap-1.5 rounded-md py-1.5 pr-2 transition-colors duration-150"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <button
          type="button"
          data-testid={`knowledge-directory-${node.id}`}
          onClick={() => {
            onToggle(node.id);
            onSelectDirectory(node.id);
          }}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-[13px]"
        >
          <ChevronRightIcon
            className={cn(
              "text-muted-foreground size-3 shrink-0 transition-transform motion-reduce:transition-none",
              isOpen && "rotate-90",
            )}
          />
          <FolderIcon className="size-3.5 shrink-0 text-amber-600 dark:text-amber-500" />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:bg-border/80 hover:text-foreground focus-visible:bg-border/80 focus-visible:text-foreground size-7 opacity-70 transition-[color,background-color,opacity] duration-150 group-hover:opacity-100 focus-visible:opacity-100"
          title={copy.createChildDirectory}
          aria-label={copy.createChildDirectory}
          onClick={() => onCreate(node.id)}
        >
          <PlusIcon className="size-3.5" />
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="text-muted-foreground hover:bg-border/80 hover:text-foreground focus-visible:bg-border/80 focus-visible:text-foreground size-7 opacity-70 transition-[color,background-color,opacity] duration-150 group-hover:opacity-100 focus-visible:opacity-100"
              title={node.name}
              aria-label={node.name}
            >
              <MoreHorizontalIcon className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onSelect={() => onRename(node)}>
              <PencilIcon />
              {copy.renameDirectory}
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => onDelete(node.id)}
            >
              <Trash2Icon />
              {copy.deleteDirectory}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <span className="text-muted-foreground shrink-0 text-[11px]">
          {node.document_count}
        </span>
      </div>
      {isOpen ? (
        <div
          role="group"
          className="animate-in fade-in-0 slide-in-from-top-1 duration-150 motion-reduce:animate-none"
        >
          {node.children.map((child) => (
            <DirectoryRow
              key={child.id}
              node={child}
              depth={depth + 1}
              documents={documents}
              expanded={expanded}
              selectedDocumentId={selectedDocumentId}
              statusLabel={statusLabel}
              onToggle={onToggle}
              onSelectDirectory={onSelectDirectory}
              onSelectDocument={onSelectDocument}
              onCreate={onCreate}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
          {files.map((document) => (
            <FileRow
              key={document.id}
              document={document}
              depth={depth + 1}
              active={selectedDocumentId === document.id}
              statusLabel={statusLabel(document)}
              onSelect={() => onSelectDocument(document.id)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function KnowledgeDirectoryTree({
  rootLabel,
  directories,
  documents,
  selectedDirectoryId,
  selectedDocumentId,
  pending,
  statusLabel,
  onSelectDirectory,
  onSelectDocument,
  onCreate,
  onRename,
  onDelete,
}: {
  rootLabel: string;
  directories: KnowledgeDirectory[];
  documents: KnowledgeDocument[];
  selectedDirectoryId: string | null;
  selectedDocumentId: string | null;
  pending: boolean;
  statusLabel: (document: KnowledgeDocument) => string;
  onSelectDirectory: (directoryId: string | null) => void;
  onSelectDocument: (documentId: string) => void;
  onCreate: (parentId: string | null, name: string) => Promise<void>;
  onRename: (directoryId: string, name: string) => Promise<void>;
  onDelete: (directoryId: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const copy = t.knowledgeBase.documents;
  const [edit, setEdit] = useState<EditState>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const seededExpandedRef = useRef(false);
  const tree = buildKnowledgeDirectoryTree(directories);
  const unfiled = documents.filter(
    (document) => (document.directory_id ?? null) === null,
  );

  useEffect(() => {
    if (seededExpandedRef.current || directories.length === 0) return;
    seededExpandedRef.current = true;
    setExpanded(new Set(directories.map((directory) => directory.id)));
  }, [directories]);

  const toggle = (id: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const openCreate = (parentId: string | null) => {
    setName("");
    setError(null);
    setEdit({ mode: "create", parentId });
    if (parentId !== null) {
      setExpanded((current) => new Set(current).add(parentId));
    }
  };
  const openRename = (directory: KnowledgeDirectory) => {
    setName(directory.name);
    setError(null);
    setEdit({ mode: "rename", directory });
  };
  const submit = async () => {
    if (edit === null || !name.trim()) return;
    setError(null);
    try {
      if (edit.mode === "create") await onCreate(edit.parentId, name);
      else await onRename(edit.directory.id, name);
      setEdit(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : copy.directoryErrorTitle,
      );
    }
  };
  const remove = async (directoryId: string) => {
    setError(null);
    try {
      await onDelete(directoryId);
      if (selectedDirectoryId === directoryId) onSelectDirectory(null);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : copy.directoryErrorTitle,
      );
    }
  };

  return (
    <div className="p-2" role="tree" aria-label={copy.catalogTitle}>
      <div
        role="treeitem"
        aria-selected={false}
        className="group hover:bg-accent/60 flex items-center gap-2 rounded-md px-2 py-2 transition-colors duration-150"
      >
        <button
          type="button"
          data-testid="knowledge-directory-root"
          aria-current={
            selectedDirectoryId === null && selectedDocumentId === null
          }
          onClick={() => onSelectDirectory(null)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-[13px] font-semibold"
        >
          <FolderIcon className="text-muted-foreground size-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{rootLabel}</span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:bg-border/80 hover:text-foreground focus-visible:bg-border/80 focus-visible:text-foreground size-7 opacity-70 transition-[color,background-color,opacity] duration-150 group-hover:opacity-100 focus-visible:opacity-100"
          title={copy.newDirectory}
          aria-label={copy.newDirectory}
          onClick={() => openCreate(null)}
        >
          <PlusIcon className="size-3.5" />
        </Button>
      </div>

      {unfiled.map((document) => (
        <FileRow
          key={document.id}
          document={document}
          depth={0}
          active={selectedDocumentId === document.id}
          statusLabel={statusLabel(document)}
          onSelect={() => onSelectDocument(document.id)}
        />
      ))}

      {tree.map((node) => (
        <DirectoryRow
          key={node.id}
          node={node}
          depth={0}
          documents={documents}
          expanded={expanded}
          selectedDocumentId={selectedDocumentId}
          statusLabel={statusLabel}
          onToggle={toggle}
          onSelectDirectory={onSelectDirectory}
          onSelectDocument={onSelectDocument}
          onCreate={openCreate}
          onRename={openRename}
          onDelete={(directoryId) => void remove(directoryId)}
        />
      ))}

      {error ? (
        <Alert variant="destructive" className="mt-2">
          <AlertTitle>{copy.directoryErrorTitle}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Dialog
        open={edit !== null}
        onOpenChange={(open) => !open && setEdit(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {edit?.mode === "rename"
                ? copy.renameDirectory
                : copy.createDirectory}
            </DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            data-testid="knowledge-directory-name"
            aria-label={copy.directoryName}
            value={name}
            disabled={pending}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
          />
          {error ? <p className="text-destructive text-sm">{error}</p> : null}
          <DialogFooter>
            <Button
              type="button"
              data-testid="knowledge-directory-submit"
              disabled={pending || !name.trim()}
              onClick={() => void submit()}
            >
              {edit?.mode === "rename"
                ? copy.renameDirectory
                : copy.createDirectory}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
