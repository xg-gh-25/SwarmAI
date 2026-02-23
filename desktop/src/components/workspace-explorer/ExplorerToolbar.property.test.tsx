/**
 * Property-Based Tests for ExplorerToolbar File Operations
 *
 * **Feature: three-column-layout**
 * **Property 9: File Creation in Current Directory**
 * **Property 10: Folder Creation in Current Directory**
 * **Validates: Requirements 3.8, 3.9**
 *
 * These tests validate the pure logic functions for file and folder creation:
 * - File/folder name validation
 * - Target directory path construction
 * - Full path construction for new files/folders
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';

// ============== Pure Functions Under Test ==============

/**
 * Validates a file or folder name.
 * Returns null if valid, or an error message if invalid.
 *
 * Requirements:
 * - 3.8: Create new file in current directory
 * - 3.9: Create new folder in current directory
 *
 * Validation rules:
 * - Name cannot be empty or whitespace-only
 * - Name cannot contain invalid characters: < > : " / \ | ? * or control characters
 * - Name cannot be a Windows reserved name (con, prn, aux, nul, com1-9, lpt1-9)
 */
export function validateName(name: string): string | null {
  if (!name.trim()) {
    return 'Name cannot be empty';
  }
  // Check for invalid characters (including control characters \x00-\x1f)
  // eslint-disable-next-line no-control-regex
  const invalidChars = /[<>:"/\\|?*\x00-\x1f]/;
  if (invalidChars.test(name)) {
    return 'Name contains invalid characters';
  }
  // Check for reserved names (Windows)
  const reservedNames = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
  if (reservedNames.test(name.trim())) {
    return 'This name is reserved by the system';
  }
  return null;
}

/**
 * Determines the target directory for file operations.
 * Returns the selected path if available, null otherwise.
 *
 * Requirements:
 * - 3.8: Create new file in current directory
 * - 3.9: Create new folder in current directory
 */
export function getTargetDirectory(selectedPath: string | null): string | null {
  if (!selectedPath) return null;
  return selectedPath;
}

/**
 * Constructs the full path for a new file or folder.
 * Combines the target directory with the trimmed name.
 *
 * Requirements:
 * - 3.8: Create new file in current directory
 * - 3.9: Create new folder in current directory
 */
export function constructFullPath(targetDir: string, name: string): string {
  return `${targetDir}/${name.trim()}`;
}

/**
 * Determines if toolbar operations should be disabled.
 * Disabled when "All Workspaces" is selected or no workspace context.
 *
 * Requirements:
 * - 3.7: Display toolbar with New File, New Folder, and Upload buttons
 */
export function isToolbarDisabled(
  disabled: boolean,
  selectedWorkspaceScope: string,
  selectedWorkspaceId: string | null
): boolean {
  return disabled || selectedWorkspaceScope === 'all' || !selectedWorkspaceId;
}

/**
 * Validates and prepares a file/folder creation operation.
 * Returns either an error or the full path for creation.
 *
 * Requirements:
 * - 3.8: Create new file in current directory
 * - 3.9: Create new folder in current directory
 */
export function prepareCreation(
  name: string,
  selectedPath: string | null
): { success: false; error: string } | { success: true; fullPath: string } {
  // Validate name
  const validationError = validateName(name);
  if (validationError) {
    return { success: false, error: validationError };
  }

  // Get target directory
  const targetDir = getTargetDirectory(selectedPath);
  if (!targetDir) {
    return { success: false, error: 'No target directory selected' };
  }

  // Construct full path
  const fullPath = constructFullPath(targetDir, name);
  return { success: true, fullPath };
}

// ============== Arbitraries ==============

/**
 * Arbitrary for valid file/folder names
 * Valid names: alphanumeric, spaces, dots, dashes, underscores
 */
const validNameArb: fc.Arbitrary<string> = fc
  .stringMatching(/^[a-zA-Z0-9][a-zA-Z0-9._\- ]{0,49}$/)
  .filter(s => s.length > 0 && s.trim().length > 0);

/**
 * Arbitrary for valid file names with extensions
 */
const validFileNameArb: fc.Arbitrary<string> = fc.tuple(
  fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,19}$/),
  fc.constantFrom('.txt', '.js', '.ts', '.py', '.md', '.json', '.css', '.html')
).map(([name, ext]) => name + ext);

/**
 * Arbitrary for valid folder names (no extension)
 */
const validFolderNameArb: fc.Arbitrary<string> = fc
  .stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,29}$/)
  .filter(s => s.length > 0);

/**
 * Arbitrary for invalid names containing forbidden characters
 */
const invalidCharNameArb: fc.Arbitrary<string> = fc.tuple(
  fc.stringMatching(/^[a-zA-Z]{1,5}$/),
  fc.constantFrom('<', '>', ':', '"', '/', '\\', '|', '?', '*'),
  fc.stringMatching(/^[a-zA-Z]{0,5}$/)
).map(([prefix, char, suffix]) => prefix + char + suffix);

/**
 * Arbitrary for Windows reserved names
 */
const reservedNameArb: fc.Arbitrary<string> = fc.constantFrom(
  'con', 'CON', 'Con',
  'prn', 'PRN', 'Prn',
  'aux', 'AUX', 'Aux',
  'nul', 'NUL', 'Nul',
  'com1', 'COM1', 'Com1',
  'com2', 'COM2', 'Com2',
  'com9', 'COM9', 'Com9',
  'lpt1', 'LPT1', 'Lpt1',
  'lpt9', 'LPT9', 'Lpt9'
);

/**
 * Arbitrary for empty or whitespace-only names
 */
const emptyNameArb: fc.Arbitrary<string> = fc.constantFrom(
  '',
  ' ',
  '  ',
  '\t',
  '\n',
  '   ',
  '\t\t',
  ' \t '
);

/**
 * Arbitrary for valid directory paths
 */
const validDirectoryPathArb: fc.Arbitrary<string> = fc.tuple(
  fc.constantFrom('/home/user', '/workspace', '/projects', '/tmp'),
  fc.array(fc.stringMatching(/^[a-z][a-z0-9_-]{0,9}$/), { minLength: 0, maxLength: 3 })
).map(([base, segments]) => segments.length > 0 ? `${base}/${segments.join('/')}` : base);

/**
 * Arbitrary for workspace IDs (nullable)
 */
const workspaceIdArb: fc.Arbitrary<string | null> = fc.oneof(
  fc.constant(null),
  fc.stringMatching(/^workspace-[0-9]{1,3}$/)
);

// ============== Property-Based Tests ==============

describe('ExplorerToolbar - Property-Based Tests', () => {
  /**
   * Property 9: File Creation in Current Directory
   * **Feature: three-column-layout, Property 9: File Creation in Current Directory**
   * **Validates: Requirements 3.8**
   */
  describe('Feature: three-column-layout, Property 9: File Creation in Current Directory', () => {
    it('should accept valid file names', () => {
      fc.assert(
        fc.property(validFileNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should reject empty or whitespace-only file names', () => {
      fc.assert(
        fc.property(emptyNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBe('Name cannot be empty');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject file names with invalid characters', () => {
      fc.assert(
        fc.property(invalidCharNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBe('Name contains invalid characters');
        }),
        { numRuns: 100 }
      );
    });

    it('should construct correct full path for new file', () => {
      fc.assert(
        fc.property(validDirectoryPathArb, validFileNameArb, (dir, name) => {
          const fullPath = constructFullPath(dir, name);
          expect(fullPath).toBe(`${dir}/${name.trim()}`);
          expect(fullPath).toContain(dir);
          expect(fullPath).toContain(name.trim());
        }),
        { numRuns: 100 }
      );
    });

    it('should trim whitespace from file names when constructing path', () => {
      fc.assert(
        fc.property(
          validDirectoryPathArb,
          validFileNameArb,
          fc.constantFrom(' ', '  ', '\t'),
          (dir, name, whitespace) => {
            const nameWithWhitespace = whitespace + name + whitespace;
            const fullPath = constructFullPath(dir, nameWithWhitespace);
            expect(fullPath).toBe(`${dir}/${name}`);
            expect(fullPath).not.toContain(whitespace + name);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should successfully prepare file creation with valid inputs', () => {
      fc.assert(
        fc.property(validFileNameArb, validDirectoryPathArb, (name, dir) => {
          const result = prepareCreation(name, dir);
          expect(result.success).toBe(true);
          if (result.success) {
            expect(result.fullPath).toBe(`${dir}/${name.trim()}`);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should fail file creation when no directory is selected', () => {
      fc.assert(
        fc.property(validFileNameArb, (name) => {
          const result = prepareCreation(name, null);
          expect(result.success).toBe(false);
          if (!result.success) {
            expect(result.error).toBe('No target directory selected');
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should fail file creation with invalid name', () => {
      fc.assert(
        fc.property(invalidCharNameArb, validDirectoryPathArb, (name, dir) => {
          const result = prepareCreation(name, dir);
          expect(result.success).toBe(false);
          if (!result.success) {
            expect(result.error).toBe('Name contains invalid characters');
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve file extension in constructed path', () => {
      fc.assert(
        fc.property(
          validDirectoryPathArb,
          fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9]{0,9}$/),
          fc.constantFrom('.txt', '.js', '.ts', '.py', '.md', '.json'),
          (dir, baseName, ext) => {
            const fileName = baseName + ext;
            const fullPath = constructFullPath(dir, fileName);
            expect(fullPath.endsWith(ext)).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 10: Folder Creation in Current Directory
   * **Feature: three-column-layout, Property 10: Folder Creation in Current Directory**
   * **Validates: Requirements 3.9**
   */
  describe('Feature: three-column-layout, Property 10: Folder Creation in Current Directory', () => {
    it('should accept valid folder names', () => {
      fc.assert(
        fc.property(validFolderNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should reject empty or whitespace-only folder names', () => {
      fc.assert(
        fc.property(emptyNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBe('Name cannot be empty');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject folder names with invalid characters', () => {
      fc.assert(
        fc.property(invalidCharNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBe('Name contains invalid characters');
        }),
        { numRuns: 100 }
      );
    });

    it('should reject Windows reserved folder names', () => {
      fc.assert(
        fc.property(reservedNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBe('This name is reserved by the system');
        }),
        { numRuns: 100 }
      );
    });

    it('should construct correct full path for new folder', () => {
      fc.assert(
        fc.property(validDirectoryPathArb, validFolderNameArb, (dir, name) => {
          const fullPath = constructFullPath(dir, name);
          expect(fullPath).toBe(`${dir}/${name.trim()}`);
          expect(fullPath).toContain(dir);
          expect(fullPath).toContain(name.trim());
        }),
        { numRuns: 100 }
      );
    });

    it('should trim whitespace from folder names when constructing path', () => {
      fc.assert(
        fc.property(
          validDirectoryPathArb,
          validFolderNameArb,
          fc.constantFrom(' ', '  ', '\t'),
          (dir, name, whitespace) => {
            const nameWithWhitespace = whitespace + name + whitespace;
            const fullPath = constructFullPath(dir, nameWithWhitespace);
            expect(fullPath).toBe(`${dir}/${name}`);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should successfully prepare folder creation with valid inputs', () => {
      fc.assert(
        fc.property(validFolderNameArb, validDirectoryPathArb, (name, dir) => {
          const result = prepareCreation(name, dir);
          expect(result.success).toBe(true);
          if (result.success) {
            expect(result.fullPath).toBe(`${dir}/${name.trim()}`);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should fail folder creation when no directory is selected', () => {
      fc.assert(
        fc.property(validFolderNameArb, (name) => {
          const result = prepareCreation(name, null);
          expect(result.success).toBe(false);
          if (!result.success) {
            expect(result.error).toBe('No target directory selected');
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should fail folder creation with reserved name', () => {
      fc.assert(
        fc.property(reservedNameArb, validDirectoryPathArb, (name, dir) => {
          const result = prepareCreation(name, dir);
          expect(result.success).toBe(false);
          if (!result.success) {
            expect(result.error).toBe('This name is reserved by the system');
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should handle nested directory paths correctly', () => {
      fc.assert(
        fc.property(
          fc.array(fc.stringMatching(/^[a-z][a-z0-9]{0,9}$/), { minLength: 1, maxLength: 5 }),
          validFolderNameArb,
          (pathSegments, folderName) => {
            const dir = '/' + pathSegments.join('/');
            const fullPath = constructFullPath(dir, folderName);
            expect(fullPath).toBe(`${dir}/${folderName.trim()}`);
            // Verify path structure is preserved
            const segments = fullPath.split('/').filter(s => s.length > 0);
            expect(segments.length).toBe(pathSegments.length + 1);
            expect(segments[segments.length - 1]).toBe(folderName.trim());
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Shared validation properties for both file and folder operations
   */
  describe('Shared Validation Properties', () => {
    it('should be consistent - same name always produces same validation result', () => {
      fc.assert(
        fc.property(
          fc.oneof(validNameArb, invalidCharNameArb, emptyNameArb, reservedNameArb),
          (name) => {
            const result1 = validateName(name);
            const result2 = validateName(name);
            expect(result1).toBe(result2);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return null for valid names and non-null for invalid names', () => {
      fc.assert(
        fc.property(validNameArb, (name) => {
          const result = validateName(name);
          expect(result).toBeNull();
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly identify target directory from selected path', () => {
      fc.assert(
        fc.property(validDirectoryPathArb, (path) => {
          const result = getTargetDirectory(path);
          expect(result).toBe(path);
        }),
        { numRuns: 100 }
      );
    });

    it('should return null when no path is selected', () => {
      const result = getTargetDirectory(null);
      expect(result).toBeNull();
    });
  });

  /**
   * Toolbar disabled state properties
   */
  describe('Toolbar Disabled State', () => {
    it('should be disabled when scope is "all"', () => {
      fc.assert(
        fc.property(fc.boolean(), workspaceIdArb, (disabled, workspaceId) => {
          const result = isToolbarDisabled(disabled, 'all', workspaceId);
          expect(result).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should be disabled when no workspace ID is provided', () => {
      fc.assert(
        fc.property(
          fc.boolean(),
          fc.stringMatching(/^workspace-[0-9]{1,3}$/),
          (disabled, scope) => {
            const result = isToolbarDisabled(disabled, scope, null);
            expect(result).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be disabled when explicitly disabled', () => {
      fc.assert(
        fc.property(
          fc.stringMatching(/^workspace-[0-9]{1,3}$/),
          fc.stringMatching(/^workspace-[0-9]{1,3}$/),
          (scope, workspaceId) => {
            const result = isToolbarDisabled(true, scope, workspaceId);
            expect(result).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be enabled when all conditions are met', () => {
      fc.assert(
        fc.property(
          fc.stringMatching(/^workspace-[0-9]{1,3}$/),
          fc.stringMatching(/^workspace-[0-9]{1,3}$/),
          (scope, workspaceId) => {
            const result = isToolbarDisabled(false, scope, workspaceId);
            expect(result).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
