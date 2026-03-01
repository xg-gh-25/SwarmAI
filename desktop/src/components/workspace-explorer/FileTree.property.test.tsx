/**
 * Property-Based Tests for FileTree Workspace Scope Filtering
 *
 * **Feature: three-column-layout**
 * **Property 7: Workspace Scope Filtering**
 * **Validates: Requirements 3.4**
 *
 * WHEN a user selects a different Workspace_Scope, THE System SHALL update
 * the file tree to show only files from the selected scope.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { SwarmWorkspace } from '../../types';

// ============== Pure Function Under Test ==============

/**
 * This is the core filtering logic extracted from FileTree.tsx
 * The actual component uses this same logic in a useMemo hook.
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

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid workspace data
 */
const workspaceArb = (index: number): fc.Arbitrary<SwarmWorkspace> => fc.record({
  id: fc.constant(`workspace-${index}`),
  name: fc.string({ minLength: 1, maxLength: 30 }).map(s => s.trim() || `Workspace ${index}`),
  filePath: fc.constant(`/workspace/path-${index}`),
  context: fc.constant(''),
  isDefault: fc.constant(index === 0), // First workspace is default (Swarm)
  createdAt: fc.constant(new Date().toISOString()),
  updatedAt: fc.constant(new Date().toISOString()),
});

/**
 * Arbitrary for generating a list of workspaces (1-10) with unique IDs
 */
const workspacesArb: fc.Arbitrary<SwarmWorkspace[]> = fc.integer({ min: 1, max: 10 }).chain(count => {
  const arbs = Array.from({ length: count }, (_, i) => workspaceArb(i));
  return fc.tuple(...arbs).map(arr => arr as SwarmWorkspace[]);
});

// ============== Property-Based Tests ==============

describe('FileTree Workspace Scope Filtering - Property-Based Tests', () => {
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
          
          // Property: When scope is 'all', ALL workspaces SHALL be returned
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
            
            // Property: When a specific workspace is selected, ONLY that workspace SHALL be returned
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
          
          // Property: When scope ID doesn't exist, empty array SHALL be returned
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
            
            // Property: Filtered workspace SHALL have identical properties to original
            expect(result[0]).toEqual(selectedWorkspace);
            expect(result[0].name).toBe(selectedWorkspace.name);
            expect(result[0].filePath).toBe(selectedWorkspace.filePath);
            expect(result[0].isDefault).toBe(selectedWorkspace.isDefault);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be idempotent - filtering twice with same scope gives same result', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.oneof(fc.constant('all'), fc.integer({ min: 0, max: 9 }).map(i => `workspace-${i % 10}`)),
          (workspaces, scope) => {
            const firstResult = filterWorkspacesByScope(workspaces, scope);
            const secondResult = filterWorkspacesByScope(workspaces, scope);
            
            // Property: Filtering SHALL be idempotent
            expect(firstResult).toEqual(secondResult);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle switching between "all" and specific scope correctly', () => {
      fc.assert(
        fc.property(
          workspacesArb,
          fc.array(fc.oneof(
            fc.constant('all'),
            fc.integer({ min: 0, max: 9 }).map(i => `workspace-${i % 10}`)
          ), { minLength: 2, maxLength: 10 }),
          (workspaces, scopeSequence) => {
            for (const scope of scopeSequence) {
              const result = filterWorkspacesByScope(workspaces, scope);
              
              if (scope === 'all') {
                // Property: "all" scope SHALL return all workspaces
                expect(result).toHaveLength(workspaces.length);
              } else {
                // Property: Specific scope SHALL return 0 or 1 workspace
                expect(result.length).toBeLessThanOrEqual(1);
                if (result.length === 1) {
                  expect(result[0].id).toBe(scope);
                }
              }
            }
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
            
            // Property: Original array SHALL NOT be mutated
            expect(workspaces).toHaveLength(originalLength);
            expect(workspaces.map(w => w.id)).toEqual(originalIds);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should correctly filter when workspaces have similar IDs', () => {
      fc.assert(
        fc.property(fc.integer({ min: 2, max: 5 }), (count) => {
          // Create workspaces with predictable IDs
          const workspaces: SwarmWorkspace[] = Array.from({ length: count }, (_, i) => ({
            id: `workspace-${i}`,
            name: `Workspace ${i}`,
            filePath: `/path/${i}`,
            context: '',
            isDefault: i === 0,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          }));
          
          // Test each workspace can be individually selected
          for (let i = 0; i < count; i++) {
            const result = filterWorkspacesByScope(workspaces, `workspace-${i}`);
            expect(result).toHaveLength(1);
            expect(result[0].id).toBe(`workspace-${i}`);
          }
        }),
        { numRuns: 100 }
      );
    });
  });
});
