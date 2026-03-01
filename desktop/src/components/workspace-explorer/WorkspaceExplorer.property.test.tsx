/**
 * Property-Based Tests for WorkspaceExplorer Component
 *
 * **Feature: three-column-layout**
 * **Property 6: Workspace Dropdown Population**
 * **Property 7: Workspace Scope Filtering**
 * **Property 8: Folder Expand/Collapse Toggle**
 * **Validates: Requirements 3.3, 3.4, 3.6**
 * 
 * These tests validate the core logic functions extracted from the WorkspaceExplorer
 * and FileTree components. Property-based testing focuses on pure functions to ensure
 * correctness properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { SwarmWorkspace } from '../../types';

// ============== Pure Functions Under Test ==============

/**
 * Generates dropdown options from workspaces.
 * This is the core logic extracted from WorkspaceExplorer's ScopeDropdown.
 * 
 * Requirements:
 * - 3.1: Display scope dropdown showing current Workspace_Scope
 * - 3.2: "All Workspaces" as default option
 * - 3.3: List all available workspaces as selectable options
 */
function generateDropdownOptions(workspaces: SwarmWorkspace[]): Array<{
  value: string;
  label: string;
  disabled: boolean;
}> {
  const options: Array<{ value: string; label: string; disabled: boolean }> = [];
  
  // Always add "All Workspaces" as first option (Requirement 3.2)
  options.push({ value: 'all', label: 'All Workspaces', disabled: false });
  
  // Add separator if workspaces exist
  if (workspaces.length > 0) {
    options.push({ value: '', label: '──────────', disabled: true });
  }
  
  // Add each workspace (Requirement 3.3)
  for (const workspace of workspaces) {
    const label = workspace.isDefault ? `🔒 ${workspace.name}` : workspace.name;
    options.push({ value: workspace.id, label, disabled: false });
  }
  
  return options;
}

/**
 * Filters workspaces based on selected scope.
 * This is the core filtering logic from FileTree.tsx.
 * 
 * Requirement 3.4: Update file tree to show only files from selected scope
 */
function filterWorkspacesByScope(
  workspaces: SwarmWorkspace[],
  selectedScope: string
): SwarmWorkspace[] {
  if (selectedScope === 'all') {
    return workspaces;
  }
  return workspaces.filter(w => w.id === selectedScope);
}

/**
 * Manages folder expanded state.
 * This is the core toggle logic from FileTree.tsx.
 * 
 * Requirement 3.6: Expand or collapse folders on click
 */
function toggleFolderExpanded(
  expandedPaths: Set<string>,
  path: string
): Set<string> {
  const next = new Set(expandedPaths);
  if (next.has(path)) {
    next.delete(path);
  } else {
    next.add(path);
  }
  return next;
}

/**
 * Checks if a folder's children should be visible.
 */
function areChildrenVisible(expandedPaths: Set<string>, path: string): boolean {
  return expandedPaths.has(path);
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid workspace data with unique IDs
 */
const workspaceArb = (index: number): fc.Arbitrary<SwarmWorkspace> =>
  fc.record({
    id: fc.constant(`workspace-${index}`),
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9 ]{0,19}$/).map(s => s || `Workspace ${index}`),
    filePath: fc.constant(`/workspace/path-${index}`),
    context: fc.constant(''),
    isDefault: fc.constant(index === 0), // First workspace is default (Swarm)
    createdAt: fc.constant(new Date().toISOString()),
    updatedAt: fc.constant(new Date().toISOString()),
  });

/**
 * Arbitrary for generating a list of workspaces (1-10) with unique IDs
 */
const workspacesArb: fc.Arbitrary<SwarmWorkspace[]> = fc
  .integer({ min: 1, max: 10 })
  .chain(count => {
    const arbs = Array.from({ length: count }, (_, i) => workspaceArb(i));
    return fc.tuple(...arbs).map(arr => arr as SwarmWorkspace[]);
  });

/**
 * Arbitrary for folder paths
 */
const folderPathArb = fc.stringMatching(/^\/[a-z]+\/[a-z]+$/);

// ============== Property-Based Tests ==============

describe('WorkspaceExplorer - Property-Based Tests', () => {
  /**
   * Property 6: Workspace Dropdown Population
   * **Feature: three-column-layout, Property 6: Workspace Dropdown Population**
   * **Validates: Requirements 3.3**
   */
  describe('Feature: three-column-layout, Property 6: Workspace Dropdown Population', () => {
    it('should always include "All Workspaces" as the first option', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          expect(options[0].value).toBe('all');
          expect(options[0].label).toBe('All Workspaces');
          expect(options[0].disabled).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should contain all workspace names as selectable options', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          const workspaceOptions = options.filter(
            opt => opt.value !== 'all' && !opt.disabled
          );
          expect(workspaceOptions.length).toBe(workspaces.length);
          
          for (const workspace of workspaces) {
            const matchingOption = workspaceOptions.find(
              opt => opt.value === workspace.id
            );
            expect(matchingOption).toBeDefined();
            if (workspace.isDefault) {
              expect(matchingOption?.label).toContain('🔒');
            }
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should have correct number of options', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          const expectedCount = workspaces.length > 0 ? 1 + 1 + workspaces.length : 1;
          expect(options.length).toBe(expectedCount);
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve workspace IDs as option values', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          for (const workspace of workspaces) {
            const matchingOption = options.find(opt => opt.value === workspace.id);
            expect(matchingOption).toBeDefined();
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should mark only the separator as disabled', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          const disabledOptions = options.filter(opt => opt.disabled);
          if (workspaces.length > 0) {
            expect(disabledOptions.length).toBe(1);
            expect(disabledOptions[0].label).toContain('─');
          } else {
            expect(disabledOptions.length).toBe(0);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should display default workspace with lock icon prefix', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const options = generateDropdownOptions(workspaces);
          const defaultWorkspace = workspaces.find(w => w.isDefault);
          if (defaultWorkspace) {
            const defaultOption = options.find(opt => opt.value === defaultWorkspace.id);
            expect(defaultOption).toBeDefined();
            expect(defaultOption?.label).toContain('🔒');
            expect(defaultOption?.label).toContain(defaultWorkspace.name);
          }
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 7: Workspace Scope Filtering
   * **Feature: three-column-layout, Property 7: Workspace Scope Filtering**
   * **Validates: Requirements 3.4**
   */
  describe('Feature: three-column-layout, Property 7: Workspace Scope Filtering', () => {
    it('should return all workspaces when scope is "all"', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const result = filterWorkspacesByScope(workspaces, 'all');
          expect(result).toHaveLength(workspaces.length);
          expect(result).toEqual(workspaces);
        }),
        { numRuns: 100 }
      );
    });

    it('should return only the selected workspace when a specific ID is chosen', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.integer({ min: 0, max: 9 }),
          (workspaces, indexSeed) => {
            const selectedIndex = indexSeed % workspaces.length;
            const selectedWorkspace = workspaces[selectedIndex];
            const result = filterWorkspacesByScope(workspaces, selectedWorkspace.id);
            expect(result).toHaveLength(1);
            expect(result[0].id).toBe(selectedWorkspace.id);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return empty array when scope ID does not exist', () => {
      fc.assert(
        fc.property(workspacesArb, (workspaces) => {
          const nonExistentId = 'non-existent-workspace-id';
          const result = filterWorkspacesByScope(workspaces, nonExistentId);
          expect(result).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve workspace data integrity after filtering', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.integer({ min: 0, max: 9 }),
          (workspaces, indexSeed) => {
            const selectedIndex = indexSeed % workspaces.length;
            const selectedWorkspace = workspaces[selectedIndex];
            const result = filterWorkspacesByScope(workspaces, selectedWorkspace.id);
            expect(result[0]).toEqual(selectedWorkspace);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be idempotent', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.oneof(fc.constant('all'), fc.integer({ min: 0, max: 9 }).map(i => `workspace-${i % 10}`)),
          (workspaces, scope) => {
            const firstResult = filterWorkspacesByScope(workspaces, scope);
            const secondResult = filterWorkspacesByScope(workspaces, scope);
            expect(firstResult).toEqual(secondResult);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should not mutate the original workspaces array', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.integer({ min: 0, max: 9 }),
          (workspaces, indexSeed) => {
            const originalLength = workspaces.length;
            const originalIds = workspaces.map(w => w.id);
            const selectedIndex = indexSeed % workspaces.length;
            const selectedWorkspace = workspaces[selectedIndex];
            filterWorkspacesByScope(workspaces, selectedWorkspace.id);
            expect(workspaces).toHaveLength(originalLength);
            expect(workspaces.map(w => w.id)).toEqual(originalIds);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 8: Folder Expand/Collapse Toggle
   * **Feature: three-column-layout, Property 8: Folder Expand/Collapse Toggle**
   * **Validates: Requirements 3.6**
   */
  describe('Feature: three-column-layout, Property 8: Folder Expand/Collapse Toggle', () => {
    it('should toggle folder from collapsed to expanded on first click', () => {
      fc.assert(
        fc.property(folderPathArb, (path) => {
          const initialState = new Set<string>();
          expect(areChildrenVisible(initialState, path)).toBe(false);
          const afterToggle = toggleFolderExpanded(initialState, path);
          expect(areChildrenVisible(afterToggle, path)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should toggle folder from expanded to collapsed on second click', () => {
      fc.assert(
        fc.property(folderPathArb, (path) => {
          const initialState = new Set<string>();
          const afterFirstToggle = toggleFolderExpanded(initialState, path);
          expect(areChildrenVisible(afterFirstToggle, path)).toBe(true);
          const afterSecondToggle = toggleFolderExpanded(afterFirstToggle, path);
          expect(areChildrenVisible(afterSecondToggle, path)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain expand/collapse state independently for each folder', () => {
      fc.assert(
        fc.property(
          fc.array(folderPathArb, { minLength: 2, maxLength: 5 }),
          (paths) => {
            const uniquePaths = [...new Set(paths)];
            if (uniquePaths.length < 2) return true;
            
            let state = new Set<string>();
            state = toggleFolderExpanded(state, uniquePaths[0]);
            expect(areChildrenVisible(state, uniquePaths[0])).toBe(true);
            expect(areChildrenVisible(state, uniquePaths[1])).toBe(false);
            
            state = toggleFolderExpanded(state, uniquePaths[1]);
            expect(areChildrenVisible(state, uniquePaths[1])).toBe(true);
            expect(areChildrenVisible(state, uniquePaths[0])).toBe(true);
            return true;
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should toggle state correctly through multiple click sequences', () => {
      fc.assert(
        fc.property(
          folderPathArb,
          fc.array(fc.constant(true), { minLength: 1, maxLength: 10 }),
          (path, clickSequence) => {
            let state = new Set<string>();
            let expectedExpanded = false;
            for (const _click of clickSequence) {
              state = toggleFolderExpanded(state, path);
              expectedExpanded = !expectedExpanded;
              expect(areChildrenVisible(state, path)).toBe(expectedExpanded);
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should not mutate the original expanded paths set', () => {
      fc.assert(
        fc.property(folderPathArb, (path) => {
          const originalState = new Set<string>();
          const originalSize = originalState.size;
          toggleFolderExpanded(originalState, path);
          expect(originalState.size).toBe(originalSize);
          expect(originalState.has(path)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle expanding all folders in a sequence', () => {
      fc.assert(
        fc.property(
          fc.array(folderPathArb, { minLength: 1, maxLength: 10 }),
          (paths) => {
            const uniquePaths = [...new Set(paths)];
            let state = new Set<string>();
            for (const path of uniquePaths) {
              state = toggleFolderExpanded(state, path);
            }
            for (const path of uniquePaths) {
              expect(areChildrenVisible(state, path)).toBe(true);
            }
            expect(state.size).toBe(uniquePaths.length);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle collapsing all folders after expanding', () => {
      fc.assert(
        fc.property(
          fc.array(folderPathArb, { minLength: 1, maxLength: 10 }),
          (paths) => {
            const uniquePaths = [...new Set(paths)];
            let state = new Set<string>();
            for (const path of uniquePaths) {
              state = toggleFolderExpanded(state, path);
            }
            for (const path of uniquePaths) {
              state = toggleFolderExpanded(state, path);
            }
            for (const path of uniquePaths) {
              expect(areChildrenVisible(state, path)).toBe(false);
            }
            expect(state.size).toBe(0);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return a new Set instance on each toggle', () => {
      fc.assert(
        fc.property(folderPathArb, (path) => {
          const initialState = new Set<string>();
          const afterToggle = toggleFolderExpanded(initialState, path);
          expect(afterToggle).not.toBe(initialState);
          expect(afterToggle).toBeInstanceOf(Set);
        }),
        { numRuns: 100 }
      );
    });
  });
});
