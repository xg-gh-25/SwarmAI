/**
 * Pure bridge utility: TreeNode → FileTreeItem conversion.
 *
 * This is the single conversion point from the canonical `TreeNode` type
 * (used by the workspace explorer) to the deprecated `FileTreeItem` type
 * (retained for backward compatibility with chat and layout components).
 *
 * Key mapping decisions:
 * - `id` is set to `node.path` (path is the unique identifier in the
 *   single-workspace model, and `LayoutContext.attachFile` deduplicates by id).
 * - `workspaceId` and `workspaceName` are empty strings — the single-workspace
 *   model does not use these fields.
 * - `isSwarmWorkspace` is intentionally omitted (defaults to undefined/falsy);
 *   the SwarmWorkspace warning is handled at the ThreeColumnLayout level.
 * - `children` are recursively mapped via optional chaining.
 */

import type { TreeNode } from '../../types';
import type { FileTreeItem } from './FileTreeNode';

export function toFileTreeItem(node: TreeNode): FileTreeItem {
  return {
    id: node.path,
    name: node.name,
    type: node.type,
    path: node.path,
    workspaceId: '',
    workspaceName: '',
    children: node.children?.map(toFileTreeItem),
  };
}
