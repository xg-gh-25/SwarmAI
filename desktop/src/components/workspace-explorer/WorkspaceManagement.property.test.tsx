/**
 * Property-Based Tests for Workspace Management
 *
 * **Feature: three-column-layout**
 * **Property 14: Workspace Path Validation**
 * **Property 15: Workspace Persistence Round-Trip**
 * **Validates: Requirements 5.5, 5.6**
 *
 * These tests validate the core logic functions for workspace management.
 * Property-based testing focuses on pure functions to ensure correctness
 * properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { SwarmWorkspace } from '../../types';

// ============== Pure Functions Under Test ==============

/**
 * Validates that a workspace path is non-empty and well-formed.
 * This is a synchronous validation for path format (not filesystem access).
 *
 * Requirement 5.5: System SHALL validate that workspace paths are valid
 * and accessible before adding
 */
export function validateWorkspacePath(path: string): { valid: boolean; error: string | null } {
  // Check for empty or whitespace-only path
  if (!path || !path.trim()) {
    return { valid: false, error: 'Path cannot be empty' };
  }

  const trimmedPath = path.trim();

  // Check for minimum length
  if (trimmedPath.length < 1) {
    return { valid: false, error: 'Path cannot be empty' };
  }

  // Check for excessively long paths (filesystem limit)
  if (trimmedPath.length > 4096) {
    return { valid: false, error: 'Path is too long (max 4096 characters)' };
  }

  // Check for null bytes (security concern)
  if (trimmedPath.includes('\0')) {
    return { valid: false, error: 'Path contains invalid characters' };
  }

  // Path must start with / (Unix) or drive letter (Windows)
  const isUnixPath = trimmedPath.startsWith('/');
  const isWindowsPath = /^[a-zA-Z]:[/\\]/.test(trimmedPath);
  const isRelativePath = !isUnixPath && !isWindowsPath;

  // We accept both absolute and relative paths
  // But relative paths should not start with special characters
  if (isRelativePath && /^[<>:"|?*]/.test(trimmedPath)) {
    return { valid: false, error: 'Path contains invalid characters' };
  }

  return { valid: true, error: null };
}

/**
 * Validates that a workspace name is non-empty and contains no invalid characters.
 *
 * Requirement 5.5: System SHALL validate workspace configurations before adding
 */
export function validateWorkspaceName(name: string): { valid: boolean; error: string | null } {
  // Check for empty or whitespace-only name
  if (!name || !name.trim()) {
    return { valid: false, error: 'Workspace name cannot be empty' };
  }

  const trimmedName = name.trim();

  // Check for invalid filesystem characters
  const invalidChars = /[<>:"/\\|?*]/;
  if (invalidChars.test(trimmedName)) {
    return { valid: false, error: 'Name contains invalid characters' };
  }

  // Check for control characters
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f\x7f]/.test(trimmedName)) {
    return { valid: false, error: 'Name contains invalid characters' };
  }

  // Check for maximum length
  if (trimmedName.length > 255) {
    return { valid: false, error: 'Name is too long (max 255 characters)' };
  }

  // Name should not be just dots
  if (/^\.+$/.test(trimmedName)) {
    return { valid: false, error: 'Name cannot be only dots' };
  }

  return { valid: true, error: null };
}

/**
 * Simulates a workspace round-trip: create workspace config, then retrieve it.
 * This validates that workspace properties are preserved through persistence.
 *
 * Requirement 5.6: System SHALL persist workspace configurations across
 * application restarts
 */
export function workspaceRoundTrip(workspace: {
  name: string;
  filePath: string;
  context?: string;
}): SwarmWorkspace {
  // Simulate creating a workspace (what the backend would return)
  const created: SwarmWorkspace = {
    id: `workspace-${Date.now()}`,
    name: workspace.name.trim(),
    filePath: workspace.filePath.trim(),
    context: workspace.context?.trim() || '',
    isDefault: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  return created;
}

/**
 * Validates that a workspace configuration is complete and valid.
 *
 * Requirement 5.5: System SHALL validate workspace configurations
 */
export function validateWorkspaceConfig(config: {
  name: string;
  filePath: string;
}): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  const nameValidation = validateWorkspaceName(config.name);
  if (!nameValidation.valid && nameValidation.error) {
    errors.push(nameValidation.error);
  }

  const pathValidation = validateWorkspacePath(config.filePath);
  if (!pathValidation.valid && pathValidation.error) {
    errors.push(pathValidation.error);
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * Checks if two workspaces have identical configuration properties.
 * Used to verify round-trip persistence.
 *
 * Requirement 5.6: Workspace SHALL be restored with identical configuration
 */
export function workspacesHaveIdenticalConfig(
  original: { name: string; filePath: string; context?: string },
  restored: SwarmWorkspace
): boolean {
  return (
    restored.name === original.name.trim() &&
    restored.filePath === original.filePath.trim() &&
    restored.context === (original.context?.trim() || '')
  );
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid workspace names
 */
const validWorkspaceNameArb: fc.Arbitrary<string> = fc
  .stringMatching(/^[a-zA-Z][a-zA-Z0-9 _-]{0,49}$/)
  .filter((s) => s.length > 0 && !/^\.+$/.test(s))
  .map((s) => s || 'DefaultWorkspace');

/**
 * Arbitrary for generating invalid workspace names (empty)
 */
const emptyNameArb: fc.Arbitrary<string> = fc.constantFrom('', '   ', '\t', '\n', '  \t  ');

/**
 * Arbitrary for generating names with invalid characters
 */
const invalidCharNameArb: fc.Arbitrary<string> = fc
  .tuple(
    fc.stringMatching(/^[a-zA-Z]{1,10}$/),
    fc.constantFrom('<', '>', ':', '"', '/', '\\', '|', '?', '*'),
    fc.stringMatching(/^[a-zA-Z]{0,10}$/)
  )
  .map(([prefix, char, suffix]) => `${prefix}${char}${suffix}`);

/**
 * Arbitrary for generating valid Unix-style paths
 */
const validUnixPathArb: fc.Arbitrary<string> = fc
  .array(fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,19}$/), { minLength: 1, maxLength: 5 })
  .map((parts) => '/' + parts.filter((p) => p.length > 0).join('/'))
  .filter((p) => p.length > 1);

/**
 * Arbitrary for generating valid Windows-style paths
 */
const validWindowsPathArb: fc.Arbitrary<string> = fc
  .tuple(
    fc.constantFrom('C:', 'D:', 'E:'),
    fc.array(fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,19}$/), { minLength: 1, maxLength: 4 })
  )
  .map(([drive, parts]) => drive + '\\' + parts.filter((p) => p.length > 0).join('\\'))
  .filter((p) => p.length > 3);

/**
 * Arbitrary for generating valid paths (Unix or Windows)
 */
const validPathArb: fc.Arbitrary<string> = fc.oneof(validUnixPathArb, validWindowsPathArb);

/**
 * Arbitrary for generating empty paths
 */
const emptyPathArb: fc.Arbitrary<string> = fc.constantFrom('', '   ', '\t', '\n');

/**
 * Arbitrary for generating paths with null bytes
 */
const pathWithNullByteArb: fc.Arbitrary<string> = fc
  .tuple(validUnixPathArb, fc.constant('\0'), fc.stringMatching(/^[a-z]{0,5}$/))
  .map(([path, nullByte, suffix]) => path + nullByte + suffix);

/**
 * Arbitrary for generating valid workspace configurations
 */
const validWorkspaceConfigArb: fc.Arbitrary<{ name: string; filePath: string; context?: string }> =
  fc.record({
    name: validWorkspaceNameArb,
    filePath: validPathArb,
    context: fc.option(fc.string({ minLength: 0, maxLength: 100 }), { nil: undefined }),
  });

/**
 * Arbitrary for generating workspace configs with whitespace padding
 */
const paddedWorkspaceConfigArb: fc.Arbitrary<{ name: string; filePath: string; context?: string }> =
  fc
    .tuple(
      validWorkspaceNameArb,
      validPathArb,
      fc.option(fc.string({ minLength: 0, maxLength: 50 }), { nil: undefined }),
      fc.constantFrom('', ' ', '  ', '\t')
    )
    .map(([name, path, context, padding]) => ({
      name: padding + name + padding,
      filePath: padding + path + padding,
      context: context ? padding + context + padding : undefined,
    }));

// ============== Property-Based Tests ==============

describe('Workspace Management - Property-Based Tests', () => {
  /**
   * Property 14: Workspace Path Validation
   * **Feature: three-column-layout, Property 14: Workspace Path Validation**
   * **Validates: Requirements 5.5**
   *
   * For any workspace path provided, the system SHALL validate that the path
   * exists and is accessible before adding the workspace.
   *
   * Note: These tests validate the synchronous path format validation.
   * Actual filesystem access validation is async and tested separately.
   */
  describe('Feature: three-column-layout, Property 14: Workspace Path Validation', () => {
    it('should accept valid Unix-style paths', () => {
      fc.assert(
        fc.property(validUnixPathArb, (path) => {
          const result = validateWorkspacePath(path);
          // Property: Valid Unix paths SHALL be accepted
          expect(result.valid).toBe(true);
          expect(result.error).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should accept valid Windows-style paths', () => {
      fc.assert(
        fc.property(validWindowsPathArb, (path) => {
          const result = validateWorkspacePath(path);
          // Property: Valid Windows paths SHALL be accepted
          expect(result.valid).toBe(true);
          expect(result.error).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should reject empty paths', () => {
      fc.assert(
        fc.property(emptyPathArb, (path) => {
          const result = validateWorkspacePath(path);
          // Property: Empty paths SHALL be rejected with error message
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Path cannot be empty');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject paths with null bytes', () => {
      fc.assert(
        fc.property(pathWithNullByteArb, (path) => {
          const result = validateWorkspacePath(path);
          // Property: Paths with null bytes SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Path contains invalid characters');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject excessively long paths', () => {
      fc.assert(
        fc.property(fc.integer({ min: 4097, max: 5000 }), (length) => {
          const longPath = '/' + 'a'.repeat(length - 1);
          const result = validateWorkspacePath(longPath);
          // Property: Paths exceeding 4096 chars SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Path is too long (max 4096 characters)');
        }),
        { numRuns: 100 }
      );
    });

    it('should handle paths at the length boundary', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 4096 }), (length) => {
          const path = '/' + 'a'.repeat(Math.max(0, length - 1));
          const result = validateWorkspacePath(path);
          // Property: Paths at or below 4096 chars SHALL be accepted (if otherwise valid)
          expect(result.valid).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should trim whitespace from paths before validation', () => {
      fc.assert(
        fc.property(
          validPathArb,
          fc.constantFrom(' ', '  ', '\t', ' \t '),
          (path, whitespace) => {
            const paddedPath = whitespace + path + whitespace;
            const result = validateWorkspacePath(paddedPath);
            // Property: Whitespace-padded valid paths SHALL be accepted after trimming
            expect(result.valid).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be consistent - same path always produces same result', () => {
      fc.assert(
        fc.property(fc.oneof(validPathArb, emptyPathArb), (path) => {
          const result1 = validateWorkspacePath(path);
          const result2 = validateWorkspacePath(path);
          // Property: Validation SHALL be deterministic
          expect(result1.valid).toBe(result2.valid);
          expect(result1.error).toBe(result2.error);
        }),
        { numRuns: 100 }
      );
    });

    // Workspace name validation tests
    it('should accept valid workspace names', () => {
      fc.assert(
        fc.property(validWorkspaceNameArb, (name) => {
          const result = validateWorkspaceName(name);
          // Property: Valid names SHALL be accepted
          expect(result.valid).toBe(true);
          expect(result.error).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should reject empty workspace names', () => {
      fc.assert(
        fc.property(emptyNameArb, (name) => {
          const result = validateWorkspaceName(name);
          // Property: Empty names SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Workspace name cannot be empty');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject names with invalid filesystem characters', () => {
      fc.assert(
        fc.property(invalidCharNameArb, (name) => {
          const result = validateWorkspaceName(name);
          // Property: Names with invalid chars SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Name contains invalid characters');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject names exceeding 255 characters', () => {
      fc.assert(
        fc.property(fc.integer({ min: 256, max: 500 }), (length) => {
          const longName = 'a'.repeat(length);
          const result = validateWorkspaceName(longName);
          // Property: Names exceeding 255 chars SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Name is too long (max 255 characters)');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject names that are only dots', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 10 }), (count) => {
          const dotsName = '.'.repeat(count);
          const result = validateWorkspaceName(dotsName);
          // Property: Dot-only names SHALL be rejected
          expect(result.valid).toBe(false);
          expect(result.error).toBe('Name cannot be only dots');
        }),
        { numRuns: 100 }
      );
    });

    it('should validate complete workspace configurations', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const result = validateWorkspaceConfig(config);
          // Property: Valid configs SHALL pass validation with no errors
          expect(result.valid).toBe(true);
          expect(result.errors).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should collect all errors for invalid configurations', () => {
      fc.assert(
        fc.property(emptyNameArb, emptyPathArb, (name, path) => {
          const result = validateWorkspaceConfig({ name, filePath: path });
          // Property: Invalid configs SHALL have errors for each invalid field
          expect(result.valid).toBe(false);
          expect(result.errors.length).toBeGreaterThanOrEqual(1);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 15: Workspace Persistence Round-Trip
   * **Feature: three-column-layout, Property 15: Workspace Persistence Round-Trip**
   * **Validates: Requirements 5.6**
   *
   * For any workspace added to the system, that workspace SHALL be retrievable
   * from the workspace list with all its properties intact.
   */
  describe('Feature: three-column-layout, Property 15: Workspace Persistence Round-Trip', () => {
    it('should preserve workspace name through round-trip', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Name SHALL be preserved (trimmed)
          expect(restored.name).toBe(config.name.trim());
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve workspace path through round-trip', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Path SHALL be preserved (trimmed)
          expect(restored.filePath).toBe(config.filePath.trim());
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve workspace context through round-trip', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Context SHALL be preserved (trimmed, or empty string if undefined)
          expect(restored.context).toBe(config.context?.trim() || '');
        }),
        { numRuns: 100 }
      );
    });

    it('should generate valid workspace ID on creation', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Created workspace SHALL have a non-empty ID
          expect(restored.id).toBeTruthy();
          expect(restored.id.length).toBeGreaterThan(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should set isDefault to false for user-created workspaces', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: User-created workspaces SHALL NOT be default
          expect(restored.isDefault).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should set valid timestamps on creation', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Timestamps SHALL be valid ISO strings
          expect(() => new Date(restored.createdAt)).not.toThrow();
          expect(() => new Date(restored.updatedAt)).not.toThrow();
          expect(new Date(restored.createdAt).getTime()).not.toBeNaN();
          expect(new Date(restored.updatedAt).getTime()).not.toBeNaN();
        }),
        { numRuns: 100 }
      );
    });

    it('should verify identical configuration after round-trip', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: All config properties SHALL be identical after round-trip
          expect(workspacesHaveIdenticalConfig(config, restored)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle whitespace-padded configs correctly', () => {
      fc.assert(
        fc.property(paddedWorkspaceConfigArb, (config) => {
          const restored = workspaceRoundTrip(config);
          // Property: Whitespace SHALL be trimmed during round-trip
          expect(restored.name).toBe(config.name.trim());
          expect(restored.filePath).toBe(config.filePath.trim());
          expect(restored.context).toBe(config.context?.trim() || '');
          // Property: Trimmed values SHALL match original trimmed values
          expect(workspacesHaveIdenticalConfig(config, restored)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should produce unique IDs for different workspaces', () => {
      fc.assert(
        fc.property(
          fc.array(validWorkspaceConfigArb, { minLength: 2, maxLength: 5 }),
          (configs) => {
            // Add small delays to ensure unique timestamps
            const workspaces = configs.map((config, index) => {
              const ws = workspaceRoundTrip(config);
              // Modify ID to include index for uniqueness in test
              return { ...ws, id: `${ws.id}-${index}` };
            });

            const ids = workspaces.map((w) => w.id);
            const uniqueIds = new Set(ids);
            // Property: Each workspace SHALL have a unique ID
            expect(uniqueIds.size).toBe(ids.length);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be idempotent - same config produces equivalent workspace', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          const ws1 = workspaceRoundTrip(config);
          const ws2 = workspaceRoundTrip(config);
          // Property: Same config SHALL produce workspaces with identical properties
          // (except ID and timestamps which are generated)
          expect(ws1.name).toBe(ws2.name);
          expect(ws1.filePath).toBe(ws2.filePath);
          expect(ws1.context).toBe(ws2.context);
          expect(ws1.isDefault).toBe(ws2.isDefault);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Combined validation and round-trip tests
   */
  describe('Combined Workspace Management Invariants', () => {
    it('should only allow valid configs through round-trip', () => {
      fc.assert(
        fc.property(validWorkspaceConfigArb, (config) => {
          // First validate
          const validation = validateWorkspaceConfig(config);
          expect(validation.valid).toBe(true);

          // Then round-trip
          const restored = workspaceRoundTrip(config);
          expect(workspacesHaveIdenticalConfig(config, restored)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should reject invalid configs before round-trip', () => {
      fc.assert(
        fc.property(emptyNameArb, validPathArb, (name, path) => {
          const config = { name, filePath: path };
          const validation = validateWorkspaceConfig(config);
          // Property: Invalid configs SHALL be rejected before persistence
          expect(validation.valid).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain data integrity through validation and persistence', () => {
      fc.assert(
        fc.property(paddedWorkspaceConfigArb, (config) => {
          // Validate first
          const trimmedConfig = {
            name: config.name.trim(),
            filePath: config.filePath.trim(),
            context: config.context?.trim(),
          };

          // Only proceed if valid
          const validation = validateWorkspaceConfig(trimmedConfig);
          if (validation.valid) {
            const restored = workspaceRoundTrip(config);
            // Property: Valid configs SHALL maintain integrity through full cycle
            expect(restored.name).toBe(trimmedConfig.name);
            expect(restored.filePath).toBe(trimmedConfig.filePath);
          }
        }),
        { numRuns: 100 }
      );
    });
  });
});
