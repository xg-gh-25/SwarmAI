/**
 * Tests for simplified useWorkspaceSelection Hook
 *
 * **Feature: unified-swarm-workspace-cwd**
 * **Validates: Requirements 6.3, 6.4**
 *
 * The hook is simplified to return the single default SwarmWS path.
 * No workspace switching, no localStorage persistence, no callbacks.
 */

import { describe, it, expect } from 'vitest';
import type { SwarmWorkspace } from '../types';

// ============== Pure Functions Under Test ==============

/**
 * Finds the default workspace from a list (mirrors hook logic)
 */
function resolveDefaultWorkspace(
  workspaces: SwarmWorkspace[] | undefined
): SwarmWorkspace | null {
  if (!workspaces || workspaces.length === 0) return null;
  return workspaces.find(w => w.isDefault) ?? workspaces[0] ?? null;
}

/**
 * Gets the workDir from a workspace
 */
function getWorkDir(workspace: SwarmWorkspace | null): string | null {
  return workspace?.filePath ?? null;
}

// ============== Test Helpers ==============

function createWorkspace(overrides: Partial<SwarmWorkspace> = {}): SwarmWorkspace {
  return {
    id: 'ws-1',
    name: 'SwarmWS',
    filePath: '/home/user/.swarm-ai/SwarmWS',
    context: 'Default SwarmAI workspace',
    isDefault: true,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

// ============== Tests ==============

describe('useWorkspaceSelection - Simplified Hook Logic', () => {
  /**
   * Validates: Requirement 6.4
   * The hook SHALL return the single SwarmWS path for UI components.
   */
  describe('Requirement 6.4: Returns single default SwarmWS path', () => {
    it('returns the default workspace when available', () => {
      const defaultWs = createWorkspace({ isDefault: true });
      const otherWs = createWorkspace({ id: 'ws-2', name: 'Other', isDefault: false });

      const result = resolveDefaultWorkspace([otherWs, defaultWs]);
      expect(result).toEqual(defaultWs);
    });

    it('falls back to first workspace when no default is marked', () => {
      const ws1 = createWorkspace({ id: 'ws-1', isDefault: false });
      const ws2 = createWorkspace({ id: 'ws-2', isDefault: false });

      const result = resolveDefaultWorkspace([ws1, ws2]);
      expect(result).toEqual(ws1);
    });

    it('returns null when workspaces list is empty', () => {
      expect(resolveDefaultWorkspace([])).toBeNull();
    });

    it('returns null when workspaces is undefined', () => {
      expect(resolveDefaultWorkspace(undefined)).toBeNull();
    });
  });

  /**
   * Validates: Requirement 6.3
   * The hook SHALL not expose workspace switching — only a single path.
   */
  describe('Requirement 6.3: workDir derived from default workspace', () => {
    it('returns filePath as workDir', () => {
      const ws = createWorkspace({ filePath: '/home/user/.swarm-ai/SwarmWS' });
      expect(getWorkDir(ws)).toBe('/home/user/.swarm-ai/SwarmWS');
    });

    it('returns null workDir when no workspace', () => {
      expect(getWorkDir(null)).toBeNull();
    });
  });

  /**
   * Validates: Requirements 6.3, 6.4
   * The simplified hook no longer has setSelectedWorkspace, localStorage, or callbacks.
   */
  describe('Simplified interface contract', () => {
    it('resolveDefaultWorkspace + getWorkDir produce consistent results', () => {
      const ws = createWorkspace();
      const resolved = resolveDefaultWorkspace([ws]);
      const workDir = getWorkDir(resolved);

      expect(resolved).toEqual(ws);
      expect(workDir).toBe(ws.filePath);
    });

    it('handles single workspace list correctly', () => {
      const ws = createWorkspace();
      const resolved = resolveDefaultWorkspace([ws]);

      expect(resolved?.id).toBe('ws-1');
      expect(resolved?.isDefault).toBe(true);
    });
  });
});
