/**
 * Property-Based Tests for Swarm Workspace Protection
 *
 * **Feature: three-column-layout**
 * **Property 12: Swarm Workspace Invariant**
 * **Property 13: Swarm Workspace Edit Protection**
 * **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 10.3**
 *
 * These tests validate the core logic functions for Swarm Workspace protection.
 * Property-based testing focuses on pure functions to ensure correctness
 * properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { FileTreeItem } from './FileTreeNode';
import type { SwarmWorkspace } from '../../types';

// ============== Pure Functions Under Test ==============

/**
 * Checks if a file tree item is in the Swarm Workspace.
 *
 * Requirements:
 * - 4.1: System SHALL always display Swarm_Workspace in the workspace list
 * - 4.3: Display confirmation dialog when editing files in Swarm_Workspace
 */
export function isSwarmWorkspace(item: FileTreeItem): boolean {
  return item.isSwarmWorkspace === true;
}

/**
 * Checks if a workspace is the Swarm Workspace (system default).
 *
 * Requirement 4.1: System SHALL always display Swarm_Workspace
 * Requirement 10.3: System SHALL ensure Swarm_Workspace is always present
 */
export function isSwarmWorkspaceByDefault(workspace: SwarmWorkspace): boolean {
  return workspace.isDefault === true;
}

/**
 * Determines if an item can be deleted.
 * Returns false for Swarm Workspace items.
 *
 * Requirement 4.4: System SHALL prevent deletion of Swarm_Workspace
 */
export function canDeleteItem(item: FileTreeItem): boolean {
  // Swarm Workspace items cannot be deleted
  if (item.isSwarmWorkspace) {
    return false;
  }
  return true;
}

/**
 * Determines if a workspace can be deleted.
 * Returns false for the Swarm Workspace.
 *
 * Requirement 4.4: System SHALL prevent deletion of Swarm_Workspace
 */
export function canDeleteWorkspace(workspace: SwarmWorkspace): boolean {
  // Swarm Workspace (isDefault) cannot be deleted
  if (workspace.isDefault) {
    return false;
  }
  return true;
}

/**
 * Determines if editing an item requires a warning dialog.
 * Returns true for Swarm Workspace items.
 *
 * Requirements:
 * - 4.3: Display confirmation dialog when editing files in Swarm_Workspace
 * - 4.5: Confirmation dialog SHALL require explicit user confirmation
 */
export function requiresEditWarning(item: FileTreeItem): boolean {
  return item.isSwarmWorkspace === true;
}

/**
 * Validates that the Swarm Workspace is present in a list of workspaces.
 *
 * Requirement 4.1: System SHALL always display Swarm_Workspace
 * Requirement 10.3: System SHALL ensure Swarm_Workspace is always present
 */
export function hasSwarmWorkspace(workspaces: SwarmWorkspace[]): boolean {
  return workspaces.some((w) => w.isDefault === true);
}

/**
 * Ensures Swarm Workspace is present in the workspace list.
 * If not present, adds a default Swarm Workspace.
 *
 * Requirement 10.3: System SHALL ensure Swarm_Workspace is always present
 */
export function ensureSwarmWorkspacePresent(
  workspaces: SwarmWorkspace[],
  defaultSwarmWorkspace: SwarmWorkspace
): SwarmWorkspace[] {
  if (hasSwarmWorkspace(workspaces)) {
    return workspaces;
  }
  return [defaultSwarmWorkspace, ...workspaces];
}

/**
 * Filters out the Swarm Workspace from a delete operation.
 * Returns the filtered list and whether the operation was blocked.
 *
 * Requirement 4.4: System SHALL prevent deletion of Swarm_Workspace
 */
export function filterDeletableWorkspaces(
  workspaces: SwarmWorkspace[],
  workspaceIdsToDelete: string[]
): { deletable: string[]; blocked: string[] } {
  const deletable: string[] = [];
  const blocked: string[] = [];

  for (const id of workspaceIdsToDelete) {
    const workspace = workspaces.find((w) => w.id === id);
    if (workspace && workspace.isDefault) {
      blocked.push(id);
    } else if (workspace) {
      deletable.push(id);
    }
  }

  return { deletable, blocked };
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating a Swarm Workspace file tree item
 */
const swarmWorkspaceItemArb: fc.Arbitrary<FileTreeItem> = fc.record({
  id: fc.constant('swarm-workspace-root'),
  name: fc.constant('Swarm Workspace'),
  type: fc.constant('directory' as const),
  path: fc.constant('/swarm-workspace'),
  workspaceId: fc.constant('swarm-workspace'),
  workspaceName: fc.constant('Swarm Workspace'),
  isSwarmWorkspace: fc.constant(true),
});

/**
 * Arbitrary for generating a regular (non-Swarm) file tree item
 */
const regularFileItemArb = (index: number): fc.Arbitrary<FileTreeItem> =>
  fc.record({
    id: fc.constant(`file-${index}`),
    name: fc
      .stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,19}\.(ts|js|py|md|json|txt)$/)
      .map((s) => s || `file${index}.ts`),
    type: fc.constant('file' as const),
    path: fc.constant(`/workspace-${index}/src/file-${index}.ts`),
    workspaceId: fc.constant(`workspace-${index}`),
    workspaceName: fc.constant(`Workspace ${index}`),
    isSwarmWorkspace: fc.constant(false),
  });

/**
 * Arbitrary for generating a regular directory item
 */
const regularDirectoryItemArb = (index: number): fc.Arbitrary<FileTreeItem> =>
  fc.record({
    id: fc.constant(`dir-${index}`),
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,14}$/).map((s) => s || `folder${index}`),
    type: fc.constant('directory' as const),
    path: fc.constant(`/workspace-${index}/folder-${index}`),
    workspaceId: fc.constant(`workspace-${index}`),
    workspaceName: fc.constant(`Workspace ${index}`),
    isSwarmWorkspace: fc.constant(false),
  });

/**
 * Arbitrary for generating a file inside Swarm Workspace
 */
const swarmWorkspaceFileArb = (index: number): fc.Arbitrary<FileTreeItem> =>
  fc.record({
    id: fc.constant(`swarm-file-${index}`),
    name: fc
      .stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,14}\.(json|yaml|md)$/)
      .map((s) => s || `config${index}.json`),
    type: fc.constant('file' as const),
    path: fc.constant(`/swarm-workspace/config-${index}.json`),
    workspaceId: fc.constant('swarm-workspace'),
    workspaceName: fc.constant('Swarm Workspace'),
    isSwarmWorkspace: fc.constant(true),
  });

/**
 * Arbitrary for generating a Swarm Workspace (SwarmWorkspace type)
 */
const swarmWorkspaceArb: fc.Arbitrary<SwarmWorkspace> = fc.record({
  id: fc.constant('swarm-workspace'),
  name: fc.constant('Swarm Workspace'),
  filePath: fc.constant('/swarm-workspace'),
  context: fc.constant(''),
  isDefault: fc.constant(true),
  createdAt: fc.constant(new Date().toISOString()),
  updatedAt: fc.constant(new Date().toISOString()),
});

/**
 * Arbitrary for generating a regular workspace
 */
const regularWorkspaceArb = (index: number): fc.Arbitrary<SwarmWorkspace> =>
  fc.record({
    id: fc.constant(`workspace-${index}`),
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9 ]{0,19}$/).map((s) => s || `Workspace ${index}`),
    filePath: fc.constant(`/workspace/path-${index}`),
    context: fc.constant(''),
    isDefault: fc.constant(false),
    createdAt: fc.constant(new Date().toISOString()),
    updatedAt: fc.constant(new Date().toISOString()),
  });

/**
 * Arbitrary for generating a list of workspaces including Swarm Workspace
 */
const workspacesWithSwarmArb: fc.Arbitrary<SwarmWorkspace[]> = fc
  .integer({ min: 0, max: 5 })
  .chain((count) => {
    const regularArbs = Array.from({ length: count }, (_, i) => regularWorkspaceArb(i));
    return fc.tuple(swarmWorkspaceArb, ...regularArbs).map((arr) => arr as SwarmWorkspace[]);
  });

/**
 * Arbitrary for generating a list of workspaces WITHOUT Swarm Workspace
 */
const workspacesWithoutSwarmArb: fc.Arbitrary<SwarmWorkspace[]> = fc
  .integer({ min: 1, max: 5 })
  .chain((count) => {
    const arbs = Array.from({ length: count }, (_, i) => regularWorkspaceArb(i));
    return fc.tuple(...arbs).map((arr) => arr as SwarmWorkspace[]);
  });

/**
 * Arbitrary for generating mixed file tree items (some Swarm, some regular)
 */
const mixedFileTreeItemsArb: fc.Arbitrary<FileTreeItem[]> = fc
  .integer({ min: 1, max: 5 })
  .chain((regularCount) => {
    const swarmFiles = Array.from({ length: 2 }, (_, i) => swarmWorkspaceFileArb(i));
    const regularFiles = Array.from({ length: regularCount }, (_, i) => regularFileItemArb(i));
    return fc.tuple(...swarmFiles, ...regularFiles).map((arr) => arr as FileTreeItem[]);
  });

// ============== Property-Based Tests ==============

describe('Swarm Workspace Protection - Property-Based Tests', () => {
  /**
   * Property 12: Swarm Workspace Invariant
   * **Feature: three-column-layout, Property 12: Swarm Workspace Invariant**
   * **Validates: Requirements 4.1, 4.4, 10.3**
   *
   * For any application state, the Swarm_Workspace SHALL always be present
   * in the workspace list and SHALL NOT be deletable.
   */
  describe('Feature: three-column-layout, Property 12: Swarm Workspace Invariant', () => {
    it('should always identify Swarm Workspace items correctly', () => {
      fc.assert(
        fc.property(swarmWorkspaceItemArb, (item) => {
          // Property: Swarm Workspace items SHALL be identified as such
          expect(isSwarmWorkspace(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should never identify regular items as Swarm Workspace', () => {
      fc.assert(
        fc.property(regularFileItemArb(0), (item) => {
          // Property: Regular items SHALL NOT be identified as Swarm Workspace
          expect(isSwarmWorkspace(item)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should identify Swarm Workspace by isDefault flag', () => {
      fc.assert(
        fc.property(swarmWorkspaceArb, (workspace) => {
          // Property: Swarm Workspace SHALL be identified by isDefault=true
          expect(isSwarmWorkspaceByDefault(workspace)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not identify regular workspaces as Swarm Workspace', () => {
      fc.assert(
        fc.property(regularWorkspaceArb(0), (workspace) => {
          // Property: Regular workspaces SHALL NOT be identified as Swarm Workspace
          expect(isSwarmWorkspaceByDefault(workspace)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should always have Swarm Workspace present in workspace list', () => {
      fc.assert(
        fc.property(workspacesWithSwarmArb, (workspaces) => {
          // Property: Swarm Workspace SHALL always be present
          expect(hasSwarmWorkspace(workspaces)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should detect when Swarm Workspace is missing', () => {
      fc.assert(
        fc.property(workspacesWithoutSwarmArb, (workspaces) => {
          // Property: Missing Swarm Workspace SHALL be detected
          expect(hasSwarmWorkspace(workspaces)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should add Swarm Workspace when missing', () => {
      fc.assert(
        fc.property(workspacesWithoutSwarmArb, swarmWorkspaceArb, (workspaces, defaultSwarm) => {
          const result = ensureSwarmWorkspacePresent(workspaces, defaultSwarm);

          // Property: After ensuring, Swarm Workspace SHALL be present
          expect(hasSwarmWorkspace(result)).toBe(true);
          // Property: Swarm Workspace SHALL be first in the list
          expect(result[0].isDefault).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not duplicate Swarm Workspace when already present', () => {
      fc.assert(
        fc.property(workspacesWithSwarmArb, swarmWorkspaceArb, (workspaces, defaultSwarm) => {
          const result = ensureSwarmWorkspacePresent(workspaces, defaultSwarm);

          // Property: Swarm Workspace SHALL NOT be duplicated
          const swarmCount = result.filter((w) => w.isDefault).length;
          expect(swarmCount).toBe(1);
          // Property: Original list SHALL be returned unchanged
          expect(result).toEqual(workspaces);
        }),
        { numRuns: 100 }
      );
    });

    it('should prevent deletion of Swarm Workspace items', () => {
      fc.assert(
        fc.property(swarmWorkspaceItemArb, (item) => {
          // Property: Swarm Workspace items SHALL NOT be deletable
          expect(canDeleteItem(item)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should prevent deletion of Swarm Workspace', () => {
      fc.assert(
        fc.property(swarmWorkspaceArb, (workspace) => {
          // Property: Swarm Workspace SHALL NOT be deletable
          expect(canDeleteWorkspace(workspace)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow deletion of regular workspaces', () => {
      fc.assert(
        fc.property(regularWorkspaceArb(0), (workspace) => {
          // Property: Regular workspaces SHALL be deletable
          expect(canDeleteWorkspace(workspace)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow deletion of regular file items', () => {
      fc.assert(
        fc.property(regularFileItemArb(0), (item) => {
          // Property: Regular file items SHALL be deletable
          expect(canDeleteItem(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow deletion of regular directory items', () => {
      fc.assert(
        fc.property(regularDirectoryItemArb(0), (item) => {
          // Property: Regular directory items SHALL be deletable
          expect(canDeleteItem(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should block Swarm Workspace in bulk delete operations', () => {
      fc.assert(
        fc.property(workspacesWithSwarmArb, (workspaces) => {
          const allIds = workspaces.map((w) => w.id);
          const result = filterDeletableWorkspaces(workspaces, allIds);

          // Property: Swarm Workspace SHALL be blocked from deletion
          expect(result.blocked).toContain('swarm-workspace');
          // Property: Regular workspaces SHALL be deletable
          const regularIds = workspaces.filter((w) => !w.isDefault).map((w) => w.id);
          for (const id of regularIds) {
            expect(result.deletable).toContain(id);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should return empty blocked list when Swarm Workspace not in delete list', () => {
      fc.assert(
        fc.property(workspacesWithSwarmArb, (workspaces) => {
          const regularIds = workspaces.filter((w) => !w.isDefault).map((w) => w.id);
          const result = filterDeletableWorkspaces(workspaces, regularIds);

          // Property: No blocked items when Swarm Workspace not targeted
          expect(result.blocked).toHaveLength(0);
          expect(result.deletable).toEqual(regularIds);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 13: Swarm Workspace Edit Protection
   * **Feature: three-column-layout, Property 13: Swarm Workspace Edit Protection**
   * **Validates: Requirements 4.3, 4.5**
   *
   * For any edit attempt on a file within Swarm_Workspace, a confirmation
   * dialog SHALL be displayed before the edit is allowed to proceed.
   */
  describe('Feature: three-column-layout, Property 13: Swarm Workspace Edit Protection', () => {
    it('should require edit warning for Swarm Workspace root', () => {
      fc.assert(
        fc.property(swarmWorkspaceItemArb, (item) => {
          // Property: Swarm Workspace root SHALL require edit warning
          expect(requiresEditWarning(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should require edit warning for files inside Swarm Workspace', () => {
      fc.assert(
        fc.property(swarmWorkspaceFileArb(0), (item) => {
          // Property: Files in Swarm Workspace SHALL require edit warning
          expect(requiresEditWarning(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not require edit warning for regular files', () => {
      fc.assert(
        fc.property(regularFileItemArb(0), (item) => {
          // Property: Regular files SHALL NOT require edit warning
          expect(requiresEditWarning(item)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should not require edit warning for regular directories', () => {
      fc.assert(
        fc.property(regularDirectoryItemArb(0), (item) => {
          // Property: Regular directories SHALL NOT require edit warning
          expect(requiresEditWarning(item)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly identify edit warning requirement in mixed list', () => {
      fc.assert(
        fc.property(mixedFileTreeItemsArb, (items) => {
          for (const item of items) {
            const needsWarning = requiresEditWarning(item);
            const isSwarm = item.isSwarmWorkspace === true;

            // Property: Edit warning requirement SHALL match Swarm Workspace status
            expect(needsWarning).toBe(isSwarm);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should be consistent - same item always produces same warning requirement', () => {
      fc.assert(
        fc.property(
          fc.oneof(swarmWorkspaceItemArb, swarmWorkspaceFileArb(0), regularFileItemArb(0)),
          (item) => {
            const result1 = requiresEditWarning(item);
            const result2 = requiresEditWarning(item);

            // Property: Warning requirement SHALL be consistent
            expect(result1).toBe(result2);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should require warning based on isSwarmWorkspace flag only', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.string({ minLength: 1, maxLength: 20 }),
            name: fc.string({ minLength: 1, maxLength: 20 }),
            type: fc.constantFrom('file' as const, 'directory' as const),
            path: fc.string({ minLength: 1, maxLength: 50 }),
            workspaceId: fc.string({ minLength: 1, maxLength: 20 }),
            workspaceName: fc.string({ minLength: 1, maxLength: 20 }),
            isSwarmWorkspace: fc.boolean(),
          }),
          (item) => {
            const needsWarning = requiresEditWarning(item);

            // Property: Warning SHALL be determined solely by isSwarmWorkspace flag
            expect(needsWarning).toBe(item.isSwarmWorkspace);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle undefined isSwarmWorkspace as false', () => {
      fc.assert(
        fc.property(
          fc.record({
            id: fc.constant('test-file'),
            name: fc.constant('test.ts'),
            type: fc.constant('file' as const),
            path: fc.constant('/test/test.ts'),
            workspaceId: fc.constant('test-workspace'),
            workspaceName: fc.constant('Test Workspace'),
            // isSwarmWorkspace intentionally omitted
          }),
          (item) => {
            // Property: Undefined isSwarmWorkspace SHALL be treated as false
            expect(requiresEditWarning(item as FileTreeItem)).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should require warning for all items with isSwarmWorkspace=true regardless of type', () => {
      fc.assert(
        fc.property(
          fc.constantFrom('file' as const, 'directory' as const),
          fc.string({ minLength: 1, maxLength: 20 }),
          (type, name) => {
            const item: FileTreeItem = {
              id: `swarm-${type}-${name}`,
              name,
              type,
              path: `/swarm-workspace/${name}`,
              workspaceId: 'swarm-workspace',
              workspaceName: 'Swarm Workspace',
              isSwarmWorkspace: true,
            };

            // Property: Both files and directories in Swarm Workspace SHALL require warning
            expect(requiresEditWarning(item)).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Combined invariant tests
   */
  describe('Combined Swarm Workspace Invariants', () => {
    it('should maintain consistency between delete and edit protection', () => {
      fc.assert(
        fc.property(swarmWorkspaceFileArb(0), (item) => {
          // Property: Swarm Workspace items SHALL be both non-deletable AND require edit warning
          expect(canDeleteItem(item)).toBe(false);
          expect(requiresEditWarning(item)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow both delete and edit for regular items', () => {
      fc.assert(
        fc.property(regularFileItemArb(0), (item) => {
          // Property: Regular items SHALL be deletable AND NOT require edit warning
          expect(canDeleteItem(item)).toBe(true);
          expect(requiresEditWarning(item)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly partition items by protection status', () => {
      fc.assert(
        fc.property(mixedFileTreeItemsArb, (items) => {
          const protectedItems = items.filter((i) => i.isSwarmWorkspace);
          const unprotectedItems = items.filter((i) => !i.isSwarmWorkspace);

          // Property: All protected items SHALL have both protections
          for (const item of protectedItems) {
            expect(canDeleteItem(item)).toBe(false);
            expect(requiresEditWarning(item)).toBe(true);
          }

          // Property: All unprotected items SHALL have neither protection
          for (const item of unprotectedItems) {
            expect(canDeleteItem(item)).toBe(true);
            expect(requiresEditWarning(item)).toBe(false);
          }
        }),
        { numRuns: 100 }
      );
    });
  });
});
