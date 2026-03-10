/**
 * @deprecated This module is deprecated. The FileTreeNode component has been
 * removed and replaced by TreeNodeRow in the new virtualized explorer
 * (Cadence 3 — swarmws-explorer-ux).
 *
 * Only the `FileTreeItem` interface is retained here for backward
 * compatibility with consumers outside the workspace-explorer directory
 * (e.g., ChatDropZone, AttachedFileChips, LayoutContext, ChatInput).
 *
 * Consumers should migrate to the new `TreeNode` type from
 * `../../types/index.ts` when feasible.
 */

/**
 * FileTreeItem interface — represents a file or directory in the legacy tree.
 *
 * Retained for backward compatibility with chat and layout components.
 */
import type { GitStatus } from '../../types';

export interface FileTreeItem {
  id: string;
  name: string;
  type: 'file' | 'directory';
  path: string;
  workspaceId: string;
  workspaceName: string;
  children?: FileTreeItem[];
  isSwarmWorkspace?: boolean;
  gitStatus?: GitStatus;
}
