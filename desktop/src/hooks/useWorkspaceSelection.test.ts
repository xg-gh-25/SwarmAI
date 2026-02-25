/**
 * Tests for simplified useWorkspaceSelection Hook (singleton model)
 *
 * **Feature: swarmws-foundation**
 * **Validates: Requirements 1.1, 1.6, 27.2**
 *
 * The hook now returns a hardcoded singleton SwarmWS workspace.
 * No workspace switching, no service dependency, no localStorage.
 */

import { describe, it, expect } from 'vitest';
import { useWorkspaceSelection } from './useWorkspaceSelection';

describe('useWorkspaceSelection - Singleton Model', () => {
  it('returns a selectedWorkspace with id "swarmws"', () => {
    const { selectedWorkspace } = useWorkspaceSelection();
    expect(selectedWorkspace.id).toBe('swarmws');
    expect(selectedWorkspace.name).toBe('SwarmWS');
    expect(selectedWorkspace.isDefault).toBe(true);
  });

  it('returns workDir as null when filePath is empty', () => {
    const { workDir } = useWorkspaceSelection();
    // filePath is empty string until the new service is wired
    expect(workDir).toBeNull();
  });

  it('always returns the same singleton workspace', () => {
    const first = useWorkspaceSelection();
    const second = useWorkspaceSelection();
    expect(first.selectedWorkspace.id).toBe(second.selectedWorkspace.id);
    expect(first.selectedWorkspace.name).toBe(second.selectedWorkspace.name);
  });
});
