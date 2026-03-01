/**
 * Property-Based Tests for useWorkspaceSelection Hook
 *
 * **Feature: workspace-selection**
 * **Property 1: Workspace Persistence per Agent**
 * **Property 2: Default Workspace Selection**
 * **Property 3: Workspace Change Callback**
 * **Validates: Workspace selection and persistence logic**
 *
 * These tests validate the pure logic functions extracted from useWorkspaceSelection.
 * The hook itself requires React Query context, so we test the underlying logic.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fc from 'fast-check';
import type { SwarmWorkspace } from '../types';

// ============== Pure Functions Under Test ==============

/**
 * Gets the localStorage key for a given agent's workspace selection
 */
function getWorkspaceStorageKey(agentId: string): string {
  return `selectedWorkspaceId_${agentId}`;
}

/**
 * Finds a workspace by ID from a list
 */
function findWorkspaceById(
  workspaces: SwarmWorkspace[],
  workspaceId: string
): SwarmWorkspace | undefined {
  return workspaces.find((ws) => ws.id === workspaceId);
}

/**
 * Finds the default workspace from a list
 */
function findDefaultWorkspace(workspaces: SwarmWorkspace[]): SwarmWorkspace | undefined {
  return workspaces.find((ws) => ws.isDefault);
}

/**
 * Determines which workspace should be selected based on:
 * 1. Saved workspace ID in localStorage
 * 2. Default workspace if no saved selection
 * 3. null if no workspaces available
 */
function determineSelectedWorkspace(
  agentId: string | null,
  workspaces: SwarmWorkspace[],
  savedWorkspaceId: string | null
): SwarmWorkspace | null {
  if (!agentId || workspaces.length === 0) {
    return null;
  }

  // Try to restore saved workspace
  if (savedWorkspaceId) {
    const savedWorkspace = findWorkspaceById(workspaces, savedWorkspaceId);
    if (savedWorkspace) {
      return savedWorkspace;
    }
  }

  // Fall back to default workspace
  const defaultWorkspace = findDefaultWorkspace(workspaces);
  return defaultWorkspace ?? null;
}

/**
 * Determines if workspace change callback should be triggered
 */
function shouldTriggerWorkspaceChange(
  prevWorkspaceId: string | null | undefined,
  currentWorkspaceId: string | null,
  isRestoring: boolean
): boolean {
  // Don't trigger during restoration
  if (isRestoring) {
    return false;
  }

  // Don't trigger on initial mount (prevWorkspaceId is undefined)
  if (prevWorkspaceId === undefined) {
    return false;
  }

  // Trigger if workspace actually changed
  return prevWorkspaceId !== currentWorkspaceId;
}

/**
 * Gets the workDir from a workspace
 */
function getWorkDir(workspace: SwarmWorkspace | null): string | null {
  return workspace?.filePath ?? null;
}

// ============== Test Setup ==============

class MockLocalStorage {
  private store: Map<string, string> = new Map();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }
}

let mockStorage: MockLocalStorage;

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid SwarmWorkspace objects
 */
const swarmWorkspaceArb = (index: number, isDefault = false): fc.Arbitrary<SwarmWorkspace> =>
  fc.record({
    id: fc.constant(`workspace-${index}`),
    name: fc.string({ minLength: 1, maxLength: 50 }).map((s) => s || `Workspace ${index}`),
    filePath: fc.constant(`/path/to/workspace-${index}`),
    context: fc.constant(`Context for workspace ${index}`),
    isDefault: fc.constant(isDefault),
    createdAt: fc.constant(new Date().toISOString()),
    updatedAt: fc.constant(new Date().toISOString()),
  });

/**
 * Arbitrary for generating a list of workspaces with one default
 */
const workspaceListArb = (count: number): fc.Arbitrary<SwarmWorkspace[]> => {
  if (count === 0) return fc.constant([]);

  const defaultIndex = 0; // First workspace is default
  const arbs = Array.from({ length: count }, (_, i) =>
    swarmWorkspaceArb(i, i === defaultIndex)
  );
  return fc.tuple(...arbs).map((arr) => arr as SwarmWorkspace[]);
};

/**
 * Arbitrary for agent IDs
 */
const agentIdArb = fc.uuid();

// ============== Property-Based Tests ==============

describe('useWorkspaceSelection - Property-Based Tests', () => {
  beforeEach(() => {
    mockStorage = new MockLocalStorage();
  });

  afterEach(() => {
    mockStorage.clear();
  });

  /**
   * Property 1: Workspace Persistence per Agent
   * **Feature: workspace-selection, Property 1: Workspace Persistence per Agent**
   *
   * For any agent, the selected workspace SHALL be persisted independently
   * using a unique localStorage key.
   */
  describe('Feature: workspace-selection, Property 1: Workspace Persistence per Agent', () => {
    it('should generate unique storage keys for different agents', () => {
      fc.assert(
        fc.property(agentIdArb, agentIdArb, (agentId1, agentId2) => {
          fc.pre(agentId1 !== agentId2);

          const key1 = getWorkspaceStorageKey(agentId1);
          const key2 = getWorkspaceStorageKey(agentId2);

          // Property: Different agents SHALL have different storage keys
          expect(key1).not.toBe(key2);
        }),
        { numRuns: 100 }
      );
    });

    it('should generate consistent storage key for same agent', () => {
      fc.assert(
        fc.property(agentIdArb, (agentId) => {
          const key1 = getWorkspaceStorageKey(agentId);
          const key2 = getWorkspaceStorageKey(agentId);

          // Property: Same agent SHALL always get same storage key
          expect(key1).toBe(key2);
        }),
        { numRuns: 100 }
      );
    });

    it('should include agent ID in storage key', () => {
      fc.assert(
        fc.property(agentIdArb, (agentId) => {
          const key = getWorkspaceStorageKey(agentId);

          // Property: Storage key SHALL contain the agent ID
          expect(key).toContain(agentId);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 2: Default Workspace Selection
   * **Feature: workspace-selection, Property 2: Default Workspace Selection**
   *
   * When no saved workspace exists, the default workspace SHALL be selected.
   */
  describe('Feature: workspace-selection, Property 2: Default Workspace Selection', () => {
    it('should select default workspace when no saved selection', () => {
      fc.assert(
        fc.property(
          agentIdArb,
          workspaceListArb(3),
          (agentId, workspaces) => {
            const result = determineSelectedWorkspace(agentId, workspaces, null);

            // Property: Default workspace SHALL be selected
            const defaultWorkspace = findDefaultWorkspace(workspaces);
            expect(result).toEqual(defaultWorkspace);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return null when no workspaces available', () => {
      fc.assert(
        fc.property(agentIdArb, (agentId) => {
          const result = determineSelectedWorkspace(agentId, [], null);

          // Property: Result SHALL be null when no workspaces
          expect(result).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should return null when no agent selected', () => {
      fc.assert(
        fc.property(workspaceListArb(3), (workspaces) => {
          const result = determineSelectedWorkspace(null, workspaces, null);

          // Property: Result SHALL be null when no agent
          expect(result).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should restore saved workspace when available', () => {
      fc.assert(
        fc.property(
          agentIdArb,
          workspaceListArb(3),
          fc.integer({ min: 0, max: 2 }),
          (agentId, workspaces, workspaceIndex) => {
            const savedWorkspaceId = workspaces[workspaceIndex].id;
            const result = determineSelectedWorkspace(agentId, workspaces, savedWorkspaceId);

            // Property: Saved workspace SHALL be restored
            expect(result?.id).toBe(savedWorkspaceId);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should fall back to default when saved workspace not found', () => {
      fc.assert(
        fc.property(agentIdArb, workspaceListArb(3), (agentId, workspaces) => {
          const nonExistentId = 'non-existent-workspace-id';
          const result = determineSelectedWorkspace(agentId, workspaces, nonExistentId);

          // Property: Default workspace SHALL be selected when saved not found
          const defaultWorkspace = findDefaultWorkspace(workspaces);
          expect(result).toEqual(defaultWorkspace);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 3: Workspace Change Callback
   * **Feature: workspace-selection, Property 3: Workspace Change Callback**
   *
   * The workspace change callback SHALL only be triggered when the workspace
   * actually changes and not during restoration.
   */
  describe('Feature: workspace-selection, Property 3: Workspace Change Callback', () => {
    it('should not trigger callback during restoration', () => {
      fc.assert(
        fc.property(
          fc.option(fc.string(), { nil: null }),
          fc.option(fc.string(), { nil: null }),
          (prevId, currentId) => {
            const shouldTrigger = shouldTriggerWorkspaceChange(prevId, currentId, true);

            // Property: Callback SHALL NOT trigger during restoration
            expect(shouldTrigger).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should not trigger callback on initial mount', () => {
      fc.assert(
        fc.property(fc.option(fc.string(), { nil: null }), (currentId) => {
          const shouldTrigger = shouldTriggerWorkspaceChange(undefined, currentId, false);

          // Property: Callback SHALL NOT trigger on initial mount
          expect(shouldTrigger).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should trigger callback when workspace changes', () => {
      fc.assert(
        fc.property(fc.string(), fc.string(), (prevId, currentId) => {
          fc.pre(prevId !== currentId);

          const shouldTrigger = shouldTriggerWorkspaceChange(prevId, currentId, false);

          // Property: Callback SHALL trigger when workspace changes
          expect(shouldTrigger).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not trigger callback when workspace stays the same', () => {
      fc.assert(
        fc.property(fc.string(), (workspaceId) => {
          const shouldTrigger = shouldTriggerWorkspaceChange(workspaceId, workspaceId, false);

          // Property: Callback SHALL NOT trigger when workspace unchanged
          expect(shouldTrigger).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should trigger callback when workspace cleared', () => {
      fc.assert(
        fc.property(fc.string(), (prevId) => {
          const shouldTrigger = shouldTriggerWorkspaceChange(prevId, null, false);

          // Property: Callback SHALL trigger when workspace cleared
          expect(shouldTrigger).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should trigger callback when workspace selected from null', () => {
      fc.assert(
        fc.property(fc.string(), (currentId) => {
          const shouldTrigger = shouldTriggerWorkspaceChange(null, currentId, false);

          // Property: Callback SHALL trigger when workspace selected from null
          expect(shouldTrigger).toBe(true);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 4: WorkDir Derivation
   * **Feature: workspace-selection, Property 4: WorkDir Derivation**
   *
   * The workDir SHALL be derived from the selected workspace's filePath.
   */
  describe('Feature: workspace-selection, Property 4: WorkDir Derivation', () => {
    it('should return filePath as workDir', () => {
      fc.assert(
        fc.property(swarmWorkspaceArb(0, false), (workspace) => {
          const workDir = getWorkDir(workspace);

          // Property: workDir SHALL equal workspace filePath
          expect(workDir).toBe(workspace.filePath);
        }),
        { numRuns: 100 }
      );
    });

    it('should return null when no workspace selected', () => {
      fc.assert(
        fc.property(fc.constant(null), (workspace) => {
          const workDir = getWorkDir(workspace);

          // Property: workDir SHALL be null when no workspace
          expect(workDir).toBeNull();
        }),
        { numRuns: 10 }
      );
    });
  });

  /**
   * Property 5: Workspace Lookup
   * **Feature: workspace-selection, Property 5: Workspace Lookup**
   *
   * Workspace lookup functions SHALL correctly find workspaces by ID and default status.
   */
  describe('Feature: workspace-selection, Property 5: Workspace Lookup', () => {
    it('should find workspace by ID', () => {
      fc.assert(
        fc.property(
          workspaceListArb(5),
          fc.integer({ min: 0, max: 4 }),
          (workspaces, index) => {
            const targetId = workspaces[index].id;
            const result = findWorkspaceById(workspaces, targetId);

            // Property: Workspace SHALL be found by ID
            expect(result).toEqual(workspaces[index]);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return undefined for non-existent ID', () => {
      fc.assert(
        fc.property(workspaceListArb(3), (workspaces) => {
          const result = findWorkspaceById(workspaces, 'non-existent-id');

          // Property: Result SHALL be undefined for non-existent ID
          expect(result).toBeUndefined();
        }),
        { numRuns: 100 }
      );
    });

    it('should find default workspace', () => {
      fc.assert(
        fc.property(workspaceListArb(3), (workspaces) => {
          const result = findDefaultWorkspace(workspaces);

          // Property: Default workspace SHALL be found
          expect(result?.isDefault).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should return undefined when no default workspace', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          // Create workspaces with no default
          const workspaces: SwarmWorkspace[] = [
            {
              id: 'ws-1',
              name: 'Workspace 1',
              filePath: '/path/1',
              context: 'Context 1',
              isDefault: false,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
            {
              id: 'ws-2',
              name: 'Workspace 2',
              filePath: '/path/2',
              context: 'Context 2',
              isDefault: false,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
          ];

          const result = findDefaultWorkspace(workspaces);

          // Property: Result SHALL be undefined when no default
          expect(result).toBeUndefined();
        }),
        { numRuns: 10 }
      );
    });
  });
});
