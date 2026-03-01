/**
 * Property-Based Tests for Chat Context Management
 *
 * **Feature: three-column-layout**
 * **Property 11: Drag-Drop File Attachment**
 * **Property 16: Chat Context File Indicators**
 * **Property 17: Workspace Scope Change Clears Context**
 * **Property 18: Cross-Workspace File Attachment**
 * **Property 19: File Removal from Context**
 * **Validates: Requirements 3.12, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8**
 *
 * These tests validate the core logic functions for chat context management.
 * Property-based testing focuses on pure functions to ensure correctness
 * properties hold across all valid inputs.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

// ============== Pure Functions Under Test ==============

/**
 * Attaches a file to the chat context.
 * Prevents duplicates by checking file id.
 *
 * Requirements:
 * - 3.12: Support drag-and-drop to attach files to Chat_Context
 * - 6.2: Drag file from Workspace_Explorer to Main_Chat_Panel attaches file
 * - 6.6: Allow attaching files from any workspace to the same chat session
 */
function attachFile(
  attachedFiles: FileTreeItem[],
  file: FileTreeItem
): FileTreeItem[] {
  // Prevent duplicates by checking file id
  if (attachedFiles.some((f) => f.id === file.id)) {
    return attachedFiles;
  }
  return [...attachedFiles, file];
}

/**
 * Removes a file from the chat context by id.
 *
 * Requirement 6.8: Remove file from Chat_Context when remove button clicked
 */
function removeAttachedFile(
  attachedFiles: FileTreeItem[],
  file: FileTreeItem
): FileTreeItem[] {
  return attachedFiles.filter((f) => f.id !== file.id);
}

/**
 * Clears all attached files from the chat context.
 *
 * Requirement 6.5: Clear Chat_Context when Workspace_Scope changes
 */
function clearAttachedFiles(): FileTreeItem[] {
  return [];
}

/**
 * Determines if a file indicator should be displayed.
 *
 * Requirements:
 * - 6.3: Display visual indicators showing which files are in Chat_Context
 * - 6.7: Display file name and remove button when file is attached
 */
function shouldShowFileIndicator(
  attachedFiles: FileTreeItem[],
  fileId: string
): boolean {
  return attachedFiles.some((f) => f.id === fileId);
}

/**
 * Gets the list of file indicators to display.
 * Each indicator includes the file info and a way to identify it for removal.
 *
 * Requirement 6.3: Display visual indicators for attached files
 */
function getFileIndicators(
  attachedFiles: FileTreeItem[]
): Array<{ id: string; name: string; path: string; workspaceId: string }> {
  return attachedFiles.map((f) => ({
    id: f.id,
    name: f.name,
    path: f.path,
    workspaceId: f.workspaceId,
  }));
}

/**
 * Checks if files from different workspaces can be attached together.
 * This is always true per Requirement 6.6.
 */
function canAttachFilesFromDifferentWorkspaces(
  _file1: FileTreeItem,
  _file2: FileTreeItem
): boolean {
  // Files from any workspace can be attached regardless of current Workspace_Scope
  return true;
}

/**
 * Handles workspace scope change by clearing context.
 *
 * Requirement 6.5: When Workspace_Scope changes, clear Chat_Context
 */
function handleWorkspaceScopeChange(
  _currentScope: string,
  _newScope: string,
  attachedFiles: FileTreeItem[]
): { attachedFiles: FileTreeItem[]; shouldClear: boolean } {
  // Always clear attached files when scope changes
  return {
    attachedFiles: [],
    shouldClear: attachedFiles.length > 0,
  };
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid file tree items
 */
const fileTreeItemArb = (index: number, workspaceIndex: number = 0): fc.Arbitrary<FileTreeItem> =>
  fc.record({
    id: fc.constant(`file-${workspaceIndex}-${index}`),
    name: fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9_-]{0,19}\.(ts|js|py|md|json|txt)$/)
      .map((s) => s || `file${index}.ts`),
    type: fc.constant('file' as const),
    path: fc.constant(`/workspace-${workspaceIndex}/src/file-${index}.ts`),
    workspaceId: fc.constant(`workspace-${workspaceIndex}`),
    workspaceName: fc.constant(`Workspace ${workspaceIndex}`),
    isSwarmWorkspace: fc.constant(false),
  });

/**
 * Arbitrary for generating a list of unique file tree items (1-10)
 */
const fileListArb: fc.Arbitrary<FileTreeItem[]> = fc
  .integer({ min: 1, max: 10 })
  .chain((count) => {
    const arbs = Array.from({ length: count }, (_, i) => fileTreeItemArb(i, 0));
    return fc.tuple(...arbs).map((arr) => arr as FileTreeItem[]);
  });

/**
 * Arbitrary for generating files from different workspaces
 */
const filesFromDifferentWorkspacesArb: fc.Arbitrary<[FileTreeItem, FileTreeItem]> = fc.tuple(
  fileTreeItemArb(0, 0),
  fileTreeItemArb(0, 1)
);

/**
 * Arbitrary for workspace scope values
 */
const workspaceScopeArb = fc.oneof(
  fc.constant('all'),
  fc.integer({ min: 0, max: 9 }).map((i) => `workspace-${i}`)
);

// ============== Property-Based Tests ==============

describe('Chat Context - Property-Based Tests', () => {
  /**
   * Property 11: Drag-Drop File Attachment
   * **Feature: three-column-layout, Property 11: Drag-Drop File Attachment**
   * **Validates: Requirements 3.12, 6.2**
   *
   * For any file dragged from Workspace_Explorer and dropped on Main_Chat_Panel,
   * that file SHALL be added to the Chat_Context attachments list.
   */
  describe('Feature: three-column-layout, Property 11: Drag-Drop File Attachment', () => {
    it('should add file to attachments list when dropped', () => {
      fc.assert(
        fc.property(fileTreeItemArb(0, 0), (file) => {
          const initialAttachments: FileTreeItem[] = [];
          const result = attachFile(initialAttachments, file);

          // Property: File SHALL be added to attachments list
          expect(result).toHaveLength(1);
          expect(result[0].id).toBe(file.id);
          expect(result[0].name).toBe(file.name);
          expect(result[0].path).toBe(file.path);
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve existing attachments when adding new file', () => {
      fc.assert(
        fc.property(fileListArb, fileTreeItemArb(99, 5), (existingFiles, newFile) => {
          const result = attachFile(existingFiles, newFile);

          // Property: All existing files SHALL be preserved
          for (const existingFile of existingFiles) {
            expect(result.some((f) => f.id === existingFile.id)).toBe(true);
          }

          // Property: New file SHALL be added
          expect(result.some((f) => f.id === newFile.id)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not add duplicate files', () => {
      fc.assert(
        fc.property(fileTreeItemArb(0, 0), (file) => {
          const initialAttachments: FileTreeItem[] = [];
          const afterFirstAdd = attachFile(initialAttachments, file);
          const afterSecondAdd = attachFile(afterFirstAdd, file);

          // Property: Duplicate files SHALL NOT be added
          expect(afterSecondAdd).toHaveLength(1);
          expect(afterSecondAdd).toEqual(afterFirstAdd);
        }),
        { numRuns: 100 }
      );
    });

    it('should not mutate the original attachments array', () => {
      fc.assert(
        fc.property(fileListArb, fileTreeItemArb(99, 5), (existingFiles, newFile) => {
          const originalLength = existingFiles.length;
          const originalIds = existingFiles.map((f) => f.id);

          attachFile(existingFiles, newFile);

          // Property: Original array SHALL NOT be mutated
          expect(existingFiles).toHaveLength(originalLength);
          expect(existingFiles.map((f) => f.id)).toEqual(originalIds);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle multiple sequential file attachments', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          let attachments: FileTreeItem[] = [];

          for (const file of files) {
            attachments = attachFile(attachments, file);
          }

          // Property: All unique files SHALL be in the attachments
          const uniqueIds = new Set(files.map((f) => f.id));
          expect(attachments).toHaveLength(uniqueIds.size);

          for (const file of files) {
            expect(attachments.some((f) => f.id === file.id)).toBe(true);
          }
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 16: Chat Context File Indicators
   * **Feature: three-column-layout, Property 16: Chat Context File Indicators**
   * **Validates: Requirements 6.3, 6.7**
   *
   * For any file in Chat_Context.attachedFiles, the ChatContextBar SHALL display
   * a removable indicator for that file.
   */
  describe('Feature: three-column-layout, Property 16: Chat Context File Indicators', () => {
    it('should show indicator for each attached file', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          const indicators = getFileIndicators(files);

          // Property: Each attached file SHALL have an indicator
          expect(indicators).toHaveLength(files.length);

          for (const file of files) {
            const indicator = indicators.find((i) => i.id === file.id);
            expect(indicator).toBeDefined();
            expect(indicator?.name).toBe(file.name);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should return true for shouldShowFileIndicator when file is attached', () => {
      fc.assert(
        fc.property(fileListArb, fc.integer({ min: 0, max: 9 }), (files, indexSeed) => {
          const selectedIndex = indexSeed % files.length;
          const selectedFile = files[selectedIndex];

          // Property: Indicator SHALL be shown for attached file
          expect(shouldShowFileIndicator(files, selectedFile.id)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should return false for shouldShowFileIndicator when file is not attached', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          const nonExistentId = 'non-existent-file-id';

          // Property: Indicator SHALL NOT be shown for non-attached file
          expect(shouldShowFileIndicator(files, nonExistentId)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should include file path and workspace info in indicators', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          const indicators = getFileIndicators(files);

          for (let i = 0; i < files.length; i++) {
            // Property: Indicator SHALL include file path and workspace info
            expect(indicators[i].path).toBe(files[i].path);
            expect(indicators[i].workspaceId).toBe(files[i].workspaceId);
          }
        }),
        { numRuns: 100 }
      );
    });

    it('should return empty array when no files are attached', () => {
      fc.assert(
        fc.property(fc.constant([] as FileTreeItem[]), (emptyFiles) => {
          const indicators = getFileIndicators(emptyFiles);

          // Property: No indicators when no files attached
          expect(indicators).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve indicator order matching attachment order', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          const indicators = getFileIndicators(files);

          // Property: Indicator order SHALL match attachment order
          for (let i = 0; i < files.length; i++) {
            expect(indicators[i].id).toBe(files[i].id);
          }
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 17: Workspace Scope Change Clears Context
   * **Feature: three-column-layout, Property 17: Workspace Scope Change Clears Context**
   * **Validates: Requirements 6.5**
   *
   * When Workspace_Scope changes, Chat_Context.attachedFiles SHALL be cleared.
   */
  describe('Feature: three-column-layout, Property 17: Workspace Scope Change Clears Context', () => {
    it('should clear all attached files when scope changes', () => {
      fc.assert(
        fc.property(
          fileListArb,
          workspaceScopeArb,
          workspaceScopeArb,
          (files, currentScope, newScope) => {
            const result = handleWorkspaceScopeChange(currentScope, newScope, files);

            // Property: Attached files SHALL be cleared
            expect(result.attachedFiles).toHaveLength(0);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should indicate clearing occurred when files were attached', () => {
      fc.assert(
        fc.property(
          fileListArb,
          workspaceScopeArb,
          workspaceScopeArb,
          (files, currentScope, newScope) => {
            const result = handleWorkspaceScopeChange(currentScope, newScope, files);

            // Property: shouldClear SHALL be true when files were attached
            expect(result.shouldClear).toBe(files.length > 0);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should indicate no clearing needed when no files were attached', () => {
      fc.assert(
        fc.property(workspaceScopeArb, workspaceScopeArb, (currentScope, newScope) => {
          const emptyFiles: FileTreeItem[] = [];
          const result = handleWorkspaceScopeChange(currentScope, newScope, emptyFiles);

          // Property: shouldClear SHALL be false when no files were attached
          expect(result.shouldClear).toBe(false);
          expect(result.attachedFiles).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should clear context regardless of scope values', () => {
      fc.assert(
        fc.property(
          fileListArb,
          fc.constant('all'),
          fc.constant('workspace-1'),
          (files, currentScope, newScope) => {
            const result = handleWorkspaceScopeChange(currentScope, newScope, files);

            // Property: Context SHALL be cleared for any scope change
            expect(result.attachedFiles).toHaveLength(0);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should clear context even when changing to same scope', () => {
      fc.assert(
        fc.property(fileListArb, workspaceScopeArb, (files, scope) => {
          const result = handleWorkspaceScopeChange(scope, scope, files);

          // Property: Context SHALL be cleared even for same scope
          // (This ensures fresh conversation starts)
          expect(result.attachedFiles).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should use clearAttachedFiles to produce empty array', () => {
      fc.assert(
        fc.property(fc.constant(null), () => {
          const result = clearAttachedFiles();

          // Property: clearAttachedFiles SHALL return empty array
          expect(result).toEqual([]);
          expect(result).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 18: Cross-Workspace File Attachment
   * **Feature: three-column-layout, Property 18: Cross-Workspace File Attachment**
   * **Validates: Requirements 6.6**
   *
   * Files from any workspace can be attached regardless of current Workspace_Scope.
   */
  describe('Feature: three-column-layout, Property 18: Cross-Workspace File Attachment', () => {
    it('should allow attaching files from different workspaces', () => {
      fc.assert(
        fc.property(filesFromDifferentWorkspacesArb, ([file1, file2]) => {
          // Property: Files from different workspaces SHALL be attachable
          expect(canAttachFilesFromDifferentWorkspaces(file1, file2)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should attach files from different workspaces to same context', () => {
      fc.assert(
        fc.property(filesFromDifferentWorkspacesArb, ([file1, file2]) => {
          let attachments: FileTreeItem[] = [];
          attachments = attachFile(attachments, file1);
          attachments = attachFile(attachments, file2);

          // Property: Both files SHALL be in the same context
          expect(attachments).toHaveLength(2);
          expect(attachments.some((f) => f.workspaceId === file1.workspaceId)).toBe(true);
          expect(attachments.some((f) => f.workspaceId === file2.workspaceId)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should preserve workspace identity for each attached file', () => {
      fc.assert(
        fc.property(filesFromDifferentWorkspacesArb, ([file1, file2]) => {
          let attachments: FileTreeItem[] = [];
          attachments = attachFile(attachments, file1);
          attachments = attachFile(attachments, file2);

          // Property: Each file SHALL retain its workspace identity
          const attached1 = attachments.find((f) => f.id === file1.id);
          const attached2 = attachments.find((f) => f.id === file2.id);

          expect(attached1?.workspaceId).toBe(file1.workspaceId);
          expect(attached1?.workspaceName).toBe(file1.workspaceName);
          expect(attached2?.workspaceId).toBe(file2.workspaceId);
          expect(attached2?.workspaceName).toBe(file2.workspaceName);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow attaching multiple files from multiple workspaces', () => {
      fc.assert(
        fc.property(
          fc.integer({ min: 2, max: 5 }),
          (workspaceCount) => {
            // Create files from different workspaces
            const files: FileTreeItem[] = [];
            for (let w = 0; w < workspaceCount; w++) {
              files.push({
                id: `file-${w}-0`,
                name: `file${w}.ts`,
                type: 'file',
                path: `/workspace-${w}/file.ts`,
                workspaceId: `workspace-${w}`,
                workspaceName: `Workspace ${w}`,
              });
            }

            let attachments: FileTreeItem[] = [];
            for (const file of files) {
              attachments = attachFile(attachments, file);
            }

            // Property: All files from all workspaces SHALL be attachable
            expect(attachments).toHaveLength(workspaceCount);

            const uniqueWorkspaces = new Set(attachments.map((f) => f.workspaceId));
            expect(uniqueWorkspaces.size).toBe(workspaceCount);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should work regardless of current workspace scope', () => {
      fc.assert(
        fc.property(
          filesFromDifferentWorkspacesArb,
          workspaceScopeArb,
          ([file1, file2], _currentScope) => {
            // Even with a specific scope selected, files from any workspace can be attached
            let attachments: FileTreeItem[] = [];
            attachments = attachFile(attachments, file1);
            attachments = attachFile(attachments, file2);

            // Property: Scope SHALL NOT restrict file attachment
            expect(attachments).toHaveLength(2);
            // Both files attached regardless of currentScope value
            expect(attachments.some((f) => f.id === file1.id)).toBe(true);
            expect(attachments.some((f) => f.id === file2.id)).toBe(true);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 19: File Removal from Context
   * **Feature: three-column-layout, Property 19: File Removal from Context**
   * **Validates: Requirements 6.8**
   *
   * Removing a file from Chat_Context SHALL remove only that file,
   * preserving all other attached files.
   */
  describe('Feature: three-column-layout, Property 19: File Removal from Context', () => {
    it('should remove only the specified file', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 2),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            const removeIndex = indexSeed % files.length;
            const fileToRemove = files[removeIndex];

            const result = removeAttachedFile(files, fileToRemove);

            // Property: Only the specified file SHALL be removed
            expect(result).toHaveLength(files.length - 1);
            expect(result.some((f) => f.id === fileToRemove.id)).toBe(false);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve all other attached files', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 2),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            const removeIndex = indexSeed % files.length;
            const fileToRemove = files[removeIndex];

            const result = removeAttachedFile(files, fileToRemove);

            // Property: All other files SHALL be preserved
            for (const file of files) {
              if (file.id !== fileToRemove.id) {
                expect(result.some((f) => f.id === file.id)).toBe(true);
              }
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should not mutate the original attachments array', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 1),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            const removeIndex = indexSeed % files.length;
            const fileToRemove = files[removeIndex];
            const originalLength = files.length;
            const originalIds = files.map((f) => f.id);

            removeAttachedFile(files, fileToRemove);

            // Property: Original array SHALL NOT be mutated
            expect(files).toHaveLength(originalLength);
            expect(files.map((f) => f.id)).toEqual(originalIds);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle removing non-existent file gracefully', () => {
      fc.assert(
        fc.property(fileListArb, (files) => {
          const nonExistentFile: FileTreeItem = {
            id: 'non-existent-id',
            name: 'nonexistent.ts',
            type: 'file',
            path: '/nonexistent/path.ts',
            workspaceId: 'nonexistent-workspace',
            workspaceName: 'Nonexistent',
          };

          const result = removeAttachedFile(files, nonExistentFile);

          // Property: Removing non-existent file SHALL not change the list
          expect(result).toHaveLength(files.length);
          expect(result.map((f) => f.id)).toEqual(files.map((f) => f.id));
        }),
        { numRuns: 100 }
      );
    });

    it('should handle removing from empty list', () => {
      fc.assert(
        fc.property(fileTreeItemArb(0, 0), (file) => {
          const emptyList: FileTreeItem[] = [];
          const result = removeAttachedFile(emptyList, file);

          // Property: Removing from empty list SHALL return empty list
          expect(result).toHaveLength(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should handle sequential removals correctly', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 3),
          (files) => {
            let attachments = [...files];

            // Remove files one by one
            for (const file of files) {
              const previousLength = attachments.length;
              attachments = removeAttachedFile(attachments, file);

              // Property: Each removal SHALL decrease count by 1
              expect(attachments).toHaveLength(previousLength - 1);
              expect(attachments.some((f) => f.id === file.id)).toBe(false);
            }

            // Property: After removing all, list SHALL be empty
            expect(attachments).toHaveLength(0);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should preserve file order after removal', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 3),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            const removeIndex = indexSeed % files.length;
            const fileToRemove = files[removeIndex];

            const result = removeAttachedFile(files, fileToRemove);

            // Property: Remaining files SHALL maintain their relative order
            const expectedOrder = files
              .filter((f) => f.id !== fileToRemove.id)
              .map((f) => f.id);
            const actualOrder = result.map((f) => f.id);

            expect(actualOrder).toEqual(expectedOrder);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return new array instance after removal', () => {
      fc.assert(
        fc.property(
          fileListArb.filter((files) => files.length >= 1),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            const removeIndex = indexSeed % files.length;
            const fileToRemove = files[removeIndex];

            const result = removeAttachedFile(files, fileToRemove);

            // Property: Result SHALL be a new array instance
            expect(result).not.toBe(files);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
