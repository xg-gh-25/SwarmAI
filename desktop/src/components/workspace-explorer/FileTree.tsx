/**
 * @deprecated This module is deprecated. The FileTree component has been
 * removed and replaced by VirtualizedTree in the new explorer
 * (Cadence 3 — swarmws-explorer-ux).
 *
 * Only the re-export of `FileTreeItem` is retained here for backward
 * compatibility with consumers that import from this path
 * (e.g., ChatInput.tsx, ChatInput.test.tsx).
 *
 * Consumers should migrate to importing `FileTreeItem` directly from
 * `./FileTreeNode` or to the new `TreeNode` type from `../../types/index.ts`.
 */

export type { FileTreeItem } from './FileTreeNode';
