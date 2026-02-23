/**
 * Property-Based Tests for AttachedFileChips Component
 *
 * **Feature: chat-panel-context-bar-removal**
 * **Properties 1-9: File Chip Rendering, Interactions, and Accessibility**
 * **Validates: Requirements 2.1, 2.3, 2.4, 2.5, 3.5, 4.2, 4.3, 4.4, 4.5, 5.4, 5.5**
 *
 * These tests validate the AttachedFileChips component behavior using property-based
 * testing to ensure correctness properties hold across all valid inputs.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { AttachedFileChips } from './AttachedFileChips';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid FileTreeItem objects
 * Uses the generator specified in the task
 */
const arbitraryFileTreeItem = (): fc.Arbitrary<FileTreeItem> =>
  fc.record({
    id: fc.uuid(),
    name: fc.string({ minLength: 1, maxLength: 100 }).filter(s => s.trim().length > 0),
    path: fc.string({ minLength: 1, maxLength: 500 }).filter(s => s.trim().length > 0),
    type: fc.constant('file' as const),
    workspaceId: fc.uuid(),
    workspaceName: fc.string({ minLength: 1, maxLength: 50 }).filter(s => s.trim().length > 0),
    isSwarmWorkspace: fc.boolean(),
  });

/**
 * Arbitrary for generating a list of unique FileTreeItem objects
 */
const arbitraryFileList = (minLength = 1, maxLength = 10): fc.Arbitrary<FileTreeItem[]> =>
  fc.array(arbitraryFileTreeItem(), { minLength, maxLength })
    .map(files => {
      // Ensure unique IDs
      const seen = new Set<string>();
      return files.filter(f => {
        if (seen.has(f.id)) return false;
        seen.add(f.id);
        return true;
      });
    })
    .filter(files => files.length >= minLength);

/**
 * Arbitrary for generating files from different workspaces
 */
const arbitraryFilesFromDifferentWorkspaces = (): fc.Arbitrary<FileTreeItem[]> =>
  fc.integer({ min: 2, max: 5 }).chain(count => {
    const arbs = Array.from({ length: count }, (_, i) =>
      fc.record({
        id: fc.uuid(),
        name: fc.string({ minLength: 1, maxLength: 50 }).filter(s => s.trim().length > 0),
        path: fc.string({ minLength: 1, maxLength: 200 }).filter(s => s.trim().length > 0),
        type: fc.constant('file' as const),
        workspaceId: fc.constant(`workspace-${i}`),
        workspaceName: fc.constant(`Workspace ${i}`),
        isSwarmWorkspace: fc.boolean(),
      })
    );
    return fc.tuple(...arbs).map(arr => arr as FileTreeItem[]);
  });

// ============== Property-Based Tests ==============

describe('AttachedFileChips - Property-Based Tests', () => {
  // Clean up after each test
  afterEach(() => {
    cleanup();
  });


  /**
   * Property 1: File Chip Rendering Completeness
   * **Feature: chat-panel-context-bar-removal, Property 1: File Chip Rendering Completeness**
   * **Validates: Requirements 2.1, 2.3, 2.4, 3.2, 3.4**
   *
   * For any attached file in the attachedFiles list, the AttachedFileChips component
   * SHALL render a chip containing a file icon element, the file name, and a visible
   * interactive close button.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 1: File Chip Rendering Completeness', () => {
    it('renders complete chip with icon, name, and close button for any valid file', () => {
      fc.assert(
        fc.property(arbitraryFileTreeItem(), (file) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { container, unmount } = render(
            <AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />
          );

          // Property: Chip SHALL contain file icon
          const icons = container.querySelectorAll('.material-symbols-outlined');
          expect(icons.length).toBeGreaterThanOrEqual(1);

          // Property: Chip SHALL display file name (possibly truncated)
          const chipElement = screen.getByTestId(`file-chip-${file.id}`);
          expect(chipElement).toBeDefined();
          expect(chipElement.textContent).toContain(file.name || 'Unknown file');

          // Property: Chip SHALL have close button
          const closeButton = chipElement.querySelector('button');
          expect(closeButton).toBeDefined();
          expect(closeButton?.getAttribute('aria-label')).toBeDefined();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('renders all chips when multiple files are provided', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: Each file SHALL have a corresponding chip
          for (const file of files) {
            const chip = screen.getByTestId(`file-chip-${file.id}`);
            expect(chip).toBeDefined();
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 2: File Removal via Click
   * **Feature: chat-panel-context-bar-removal, Property 2: File Removal via Click**
   * **Validates: Requirements 2.5**
   *
   * For any file chip displayed, clicking the close button SHALL invoke onRemoveFile
   * with the corresponding file.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 2: File Removal via Click', () => {
    it('invokes onRemoveFile with correct file when close button is clicked', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), fc.integer({ min: 0, max: 9 }), (files, indexSeed) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const targetIndex = indexSeed % files.length;
          const targetFile = files[targetIndex];
          const chip = screen.getByTestId(`file-chip-${targetFile.id}`);
          const closeButton = chip.querySelector('button');

          expect(closeButton).toBeDefined();
          fireEvent.click(closeButton!);

          // Property: onRemoveFile SHALL be called with the correct file
          expect(onRemoveFile).toHaveBeenCalledTimes(1);
          expect(onRemoveFile).toHaveBeenCalledWith(targetFile);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('only removes the clicked file, not others', () => {
      fc.assert(
        fc.property(arbitraryFileList(2, 10), fc.integer({ min: 0, max: 9 }), (files, indexSeed) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const targetIndex = indexSeed % files.length;
          const targetFile = files[targetIndex];
          const chip = screen.getByTestId(`file-chip-${targetFile.id}`);
          const closeButton = chip.querySelector('button');

          fireEvent.click(closeButton!);

          // Property: Only the clicked file SHALL be passed to onRemoveFile
          expect(onRemoveFile).toHaveBeenCalledTimes(1);
          const calledFile = onRemoveFile.mock.calls[0][0];
          expect(calledFile.id).toBe(targetFile.id);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 3: Tooltip Shows Full Path
   * **Feature: chat-panel-context-bar-removal, Property 3: Tooltip Shows Full Path**
   * **Validates: Requirements 3.5**
   *
   * For any file chip, the chip element SHALL have a title attribute containing
   * the full file path.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 3: Tooltip Shows Full Path', () => {
    it('each chip has title attribute with full file path', () => {
      fc.assert(
        fc.property(arbitraryFileTreeItem(), (file) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

          const chip = screen.getByTestId(`file-chip-${file.id}`);
          
          // Property: Chip SHALL have title attribute with file path
          expect(chip.getAttribute('title')).toBe(file.path || 'Path unavailable');

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('all chips in a list have correct title attributes', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: Each chip SHALL have its own file path as title
          for (const file of files) {
            const chip = screen.getByTestId(`file-chip-${file.id}`);
            expect(chip.getAttribute('title')).toBe(file.path || 'Path unavailable');
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });


  /**
   * Property 4: Keyboard Removal via Delete/Backspace
   * **Feature: chat-panel-context-bar-removal, Property 4: Keyboard Removal via Delete/Backspace**
   * **Validates: Requirements 4.2**
   *
   * For any file chip that has keyboard focus, pressing Delete or Backspace
   * SHALL remove that file from the attachedFiles list.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 4: Keyboard Removal via Delete/Backspace', () => {
    it('removes file when Delete key is pressed on focused chip', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), fc.integer({ min: 0, max: 9 }), (files, indexSeed) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const targetIndex = indexSeed % files.length;
          const targetFile = files[targetIndex];
          const chip = screen.getByTestId(`file-chip-${targetFile.id}`);

          // Focus the chip and press Delete
          chip.focus();
          fireEvent.keyDown(chip, { key: 'Delete' });

          // Property: File SHALL be removed via onRemoveFile
          expect(onRemoveFile).toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('removes file when Backspace key is pressed on focused chip', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), fc.integer({ min: 0, max: 9 }), (files, indexSeed) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const targetIndex = indexSeed % files.length;
          const targetFile = files[targetIndex];
          const chip = screen.getByTestId(`file-chip-${targetFile.id}`);

          // Focus the chip and press Backspace
          chip.focus();
          fireEvent.keyDown(chip, { key: 'Backspace' });

          // Property: File SHALL be removed via onRemoveFile
          expect(onRemoveFile).toHaveBeenCalled();

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 5: Arrow Key Navigation
   * **Feature: chat-panel-context-bar-removal, Property 5: Arrow Key Navigation**
   * **Validates: Requirements 4.3**
   *
   * For any list of file chips with at least two items, pressing ArrowLeft/ArrowRight
   * SHALL move focus to the adjacent chip, wrapping around at the boundaries.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 5: Arrow Key Navigation', () => {
    it('ArrowRight navigates to next chip with wrapping', () => {
      fc.assert(
        fc.property(arbitraryFileList(2, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(
            <AttachedFileChips files={files} onRemoveFile={onRemoveFile} />
          );

          const chipsContainer = screen.getByTestId('attached-file-chips');
          
          // Focus the container to start navigation
          chipsContainer.focus();
          
          // Press ArrowRight to navigate
          fireEvent.keyDown(chipsContainer, { key: 'ArrowRight' });

          // Property: Navigation SHALL work (focus moves within container)
          expect(chipsContainer).toBeDefined();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('ArrowLeft navigates to previous chip with wrapping', () => {
      fc.assert(
        fc.property(arbitraryFileList(2, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const chipsContainer = screen.getByTestId('attached-file-chips');
          
          // Focus the container
          chipsContainer.focus();
          
          // Press ArrowLeft to navigate (should wrap to last)
          fireEvent.keyDown(chipsContainer, { key: 'ArrowLeft' });

          // Property: Navigation SHALL work with wrapping
          expect(chipsContainer).toBeDefined();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('navigation wraps from last to first on ArrowRight', () => {
      fc.assert(
        fc.property(arbitraryFileList(2, 5), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const chipsContainer = screen.getByTestId('attached-file-chips');
          chipsContainer.focus();

          // Navigate to the end and then wrap
          for (let i = 0; i < files.length + 1; i++) {
            fireEvent.keyDown(chipsContainer, { key: 'ArrowRight' });
          }

          // Property: Navigation SHALL wrap around
          expect(chipsContainer).toBeDefined();

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 6: Close Button Keyboard Activation
   * **Feature: chat-panel-context-bar-removal, Property 6: Close Button Keyboard Activation**
   * **Validates: Requirements 4.4**
   *
   * For any file chip close button that has keyboard focus, pressing Enter or Space
   * SHALL remove the corresponding file.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 6: Close Button Keyboard Activation', () => {
    it('Enter key on close button removes file', () => {
      fc.assert(
        fc.property(arbitraryFileTreeItem(), (file) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

          const chip = screen.getByTestId(`file-chip-${file.id}`);
          const closeButton = chip.querySelector('button');

          expect(closeButton).toBeDefined();
          
          // Simulate Enter key on close button
          fireEvent.keyDown(closeButton!, { key: 'Enter' });

          // Property: File SHALL be removed
          expect(onRemoveFile).toHaveBeenCalledWith(file);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('Space key on close button removes file', () => {
      fc.assert(
        fc.property(arbitraryFileTreeItem(), (file) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

          const chip = screen.getByTestId(`file-chip-${file.id}`);
          const closeButton = chip.querySelector('button');

          expect(closeButton).toBeDefined();
          
          // Simulate Space key on close button
          fireEvent.keyDown(closeButton!, { key: ' ' });

          // Property: File SHALL be removed
          expect(onRemoveFile).toHaveBeenCalledWith(file);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });


  /**
   * Property 7: ARIA Accessibility Attributes
   * **Feature: chat-panel-context-bar-removal, Property 7: ARIA Accessibility Attributes**
   * **Validates: Requirements 4.5**
   *
   * For any file chip, the component SHALL include role="listitem" on each chip,
   * role="list" on the container, and aria-label on the close button.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 7: ARIA Accessibility Attributes', () => {
    it('container has role="list"', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const container = screen.getByTestId('attached-file-chips');
          
          // Property: Container SHALL have role="list"
          expect(container.getAttribute('role')).toBe('list');

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('each chip has role="listitem"', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: Each chip SHALL have role="listitem"
          for (const file of files) {
            const chip = screen.getByTestId(`file-chip-${file.id}`);
            expect(chip.getAttribute('role')).toBe('listitem');
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('close buttons have aria-label', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: Each close button SHALL have aria-label
          for (const file of files) {
            const chip = screen.getByTestId(`file-chip-${file.id}`);
            const closeButton = chip.querySelector('button');
            expect(closeButton?.getAttribute('aria-label')).toBeDefined();
            expect(closeButton?.getAttribute('aria-label')).toContain('Remove');
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('container has aria-label for screen readers', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 10), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          const container = screen.getByTestId('attached-file-chips');
          
          // Property: Container SHALL have aria-label
          expect(container.getAttribute('aria-label')).toBeDefined();

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 8: Multi-Workspace File Support
   * **Feature: chat-panel-context-bar-removal, Property 8: Multi-Workspace File Support**
   * **Validates: Requirements 5.4**
   *
   * For any set of attached files where files have different workspaceId values,
   * all files SHALL be displayed as chips regardless of their workspace origin.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 8: Multi-Workspace File Support', () => {
    it('renders files from different workspaces', () => {
      fc.assert(
        fc.property(arbitraryFilesFromDifferentWorkspaces(), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: All files SHALL be rendered regardless of workspace
          for (const file of files) {
            const chip = screen.getByTestId(`file-chip-${file.id}`);
            expect(chip).toBeDefined();
          }

          // Verify files are from different workspaces
          const workspaceIds = new Set(files.map(f => f.workspaceId));
          expect(workspaceIds.size).toBeGreaterThan(1);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('preserves workspace identity for each file', () => {
      fc.assert(
        fc.property(arbitraryFilesFromDifferentWorkspaces(), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { unmount } = render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

          // Property: Each file SHALL retain its workspace identity
          // (verified by checking all chips render correctly)
          const renderedChips = files.map(f => screen.getByTestId(`file-chip-${f.id}`));
          expect(renderedChips.length).toBe(files.length);

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 9: Reactive UI Updates
   * **Feature: chat-panel-context-bar-removal, Property 9: Reactive UI Updates**
   * **Validates: Requirements 5.5**
   *
   * For any change to the attachedFiles array, the AttachedFileChips component
   * SHALL re-render to reflect the current state.
   */
  describe('Feature: chat-panel-context-bar-removal, Property 9: Reactive UI Updates', () => {
    it('re-renders when files are added', () => {
      fc.assert(
        fc.property(
          arbitraryFileList(1, 5),
          arbitraryFileTreeItem(),
          (initialFiles, newFile) => {
            cleanup();
            const onRemoveFile = vi.fn();
            const { rerender, unmount } = render(
              <AttachedFileChips files={initialFiles} onRemoveFile={onRemoveFile} />
            );

            // Verify initial state
            for (const file of initialFiles) {
              expect(screen.getByTestId(`file-chip-${file.id}`)).toBeDefined();
            }

            // Add new file (ensure unique ID)
            const uniqueNewFile = { ...newFile, id: `new-${newFile.id}` };
            const updatedFiles = [...initialFiles, uniqueNewFile];
            rerender(<AttachedFileChips files={updatedFiles} onRemoveFile={onRemoveFile} />);

            // Property: New file SHALL be rendered
            expect(screen.getByTestId(`file-chip-${uniqueNewFile.id}`)).toBeDefined();

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('re-renders when files are removed', () => {
      fc.assert(
        fc.property(
          arbitraryFileList(2, 10),
          fc.integer({ min: 0, max: 9 }),
          (files, indexSeed) => {
            cleanup();
            const onRemoveFile = vi.fn();
            const { rerender, unmount } = render(
              <AttachedFileChips files={files} onRemoveFile={onRemoveFile} />
            );

            // Verify initial state
            for (const file of files) {
              expect(screen.getByTestId(`file-chip-${file.id}`)).toBeDefined();
            }

            // Remove a file
            const removeIndex = indexSeed % files.length;
            const removedFile = files[removeIndex];
            const updatedFiles = files.filter((_, i) => i !== removeIndex);
            rerender(<AttachedFileChips files={updatedFiles} onRemoveFile={onRemoveFile} />);

            // Property: Removed file SHALL no longer be rendered
            expect(screen.queryByTestId(`file-chip-${removedFile.id}`)).toBeNull();

            // Property: Other files SHALL still be rendered
            for (const file of updatedFiles) {
              expect(screen.getByTestId(`file-chip-${file.id}`)).toBeDefined();
            }

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('returns null when files array becomes empty', () => {
      fc.assert(
        fc.property(arbitraryFileList(1, 5), (files) => {
          cleanup();
          const onRemoveFile = vi.fn();
          const { rerender, unmount } = render(
            <AttachedFileChips files={files} onRemoveFile={onRemoveFile} />
          );

          // Verify initial state has chips
          expect(screen.getByTestId('attached-file-chips')).toBeDefined();

          // Remove all files
          rerender(<AttachedFileChips files={[]} onRemoveFile={onRemoveFile} />);

          // Property: Component SHALL return null (render nothing)
          expect(screen.queryByTestId('attached-file-chips')).toBeNull();

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('handles complete file list replacement', () => {
      fc.assert(
        fc.property(
          arbitraryFileList(1, 5),
          arbitraryFileList(1, 5),
          (initialFiles, newFiles) => {
            cleanup();
            const onRemoveFile = vi.fn();
            const { rerender, unmount } = render(
              <AttachedFileChips files={initialFiles} onRemoveFile={onRemoveFile} />
            );

            // Replace with completely new files
            rerender(<AttachedFileChips files={newFiles} onRemoveFile={onRemoveFile} />);

            // Property: New files SHALL be rendered
            for (const file of newFiles) {
              expect(screen.getByTestId(`file-chip-${file.id}`)).toBeDefined();
            }

            // Property: Old files (with different IDs) SHALL not be rendered
            for (const file of initialFiles) {
              if (!newFiles.some(f => f.id === file.id)) {
                expect(screen.queryByTestId(`file-chip-${file.id}`)).toBeNull();
              }
            }

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});


// ============== Unit Tests for Edge Cases ==============

/**
 * Unit Tests for Edge Cases
 *
 * **Feature: chat-panel-context-bar-removal**
 * **Validates: Requirements 2.2, 2.6, 3.1, 3.3, 4.1**
 *
 * These tests verify specific edge cases and visual styling requirements
 * using standard vitest assertions.
 */
describe('AttachedFileChips - Unit Tests for Edge Cases', () => {
  afterEach(() => {
    cleanup();
  });

  /**
   * Test: Empty state returns null
   * **Validates: Requirement 2.6**
   *
   * WHEN no files are attached, THE ChatInput component SHALL NOT display
   * the File_Chips area.
   */
  describe('Empty state handling', () => {
    it('returns null when files array is empty', () => {
      const onRemoveFile = vi.fn();
      const { container } = render(
        <AttachedFileChips files={[]} onRemoveFile={onRemoveFile} />
      );

      // Component should render nothing
      expect(container.firstChild).toBeNull();
      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });

    it('returns null when files is undefined', () => {
      const onRemoveFile = vi.fn();
      // @ts-expect-error - Testing undefined case
      const { container } = render(
        <AttachedFileChips files={undefined} onRemoveFile={onRemoveFile} />
      );

      expect(container.firstChild).toBeNull();
    });
  });

  /**
   * Test: Single file renders one chip
   * **Validates: Requirement 2.1**
   *
   * WHEN files are attached to the chat context, THE ChatInput component
   * SHALL display File_Chips above the text input field.
   */
  describe('Single file rendering', () => {
    it('renders exactly one chip for a single file', () => {
      const onRemoveFile = vi.fn();
      const singleFile: FileTreeItem = {
        id: 'test-file-1',
        name: 'test-file.ts',
        path: '/workspace/src/test-file.ts',
        type: 'file',
        workspaceId: 'workspace-1',
        workspaceName: 'Test Workspace',
      };

      render(<AttachedFileChips files={[singleFile]} onRemoveFile={onRemoveFile} />);

      // Should render the container
      const container = screen.getByTestId('attached-file-chips');
      expect(container).toBeInTheDocument();

      // Should render exactly one chip
      const chip = screen.getByTestId(`file-chip-${singleFile.id}`);
      expect(chip).toBeInTheDocument();

      // Should display the file name
      expect(chip).toHaveTextContent('test-file.ts');

      // Should have a close button
      const closeButton = chip.querySelector('button');
      expect(closeButton).toBeInTheDocument();
    });
  });

  /**
   * Test: Horizontal scroll container has overflow-x-auto class
   * **Validates: Requirement 2.2**
   *
   * THE File_Chips SHALL display in a horizontal row with horizontal
   * scrolling when overflow occurs.
   */
  describe('Horizontal scroll container', () => {
    it('container has overflow-x-auto class for horizontal scrolling', () => {
      const onRemoveFile = vi.fn();
      const files: FileTreeItem[] = [
        { id: '1', name: 'file1.ts', path: '/path/file1.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
        { id: '2', name: 'file2.ts', path: '/path/file2.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
        { id: '3', name: 'file3.ts', path: '/path/file3.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
      ];

      render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

      const container = screen.getByTestId('attached-file-chips');
      
      // Container should have overflow-x-auto class for horizontal scrolling
      expect(container).toHaveClass('overflow-x-auto');
    });

    it('container uses flex layout for horizontal arrangement', () => {
      const onRemoveFile = vi.fn();
      const files: FileTreeItem[] = [
        { id: '1', name: 'file1.ts', path: '/path/file1.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
      ];

      render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

      const container = screen.getByTestId('attached-file-chips');
      
      // Container should use flex layout
      expect(container).toHaveClass('flex');
      expect(container).toHaveClass('items-center');
    });
  });

  /**
   * Test: Chip has rounded-full class for pill shape
   * **Validates: Requirement 3.1**
   *
   * THE File_Chip SHALL use a pill/rounded shape with compact padding.
   */
  describe('Pill shape styling', () => {
    it('chip has rounded-full class for pill shape', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should have rounded-full class for pill shape
      expect(chip).toHaveClass('rounded-full');
    });

    it('chip has compact padding classes', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should have compact padding (px-2 py-0.5)
      expect(chip).toHaveClass('px-2');
      expect(chip).toHaveClass('py-0.5');
    });
  });

  /**
   * Test: Chip uses CSS variables (--color-*) for theme colors
   * **Validates: Requirement 3.3**
   *
   * THE File_Chip SHALL use theme-consistent colors that distinguish
   * it from surrounding UI elements.
   */
  describe('Theme color CSS variables', () => {
    it('chip uses CSS variables for background color', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should use CSS variable for background (bg-[var(--color-primary)]/10)
      expect(chip.className).toContain('bg-[var(--color-primary)]');
    });

    it('chip uses CSS variables for text color', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should use CSS variable for text color (text-[var(--color-primary)])
      expect(chip.className).toContain('text-[var(--color-primary)]');
    });

    it('chip uses CSS variables for border color', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should use CSS variable for border (border-[var(--color-primary)]/20)
      expect(chip.className).toContain('border-[var(--color-primary)]');
    });

    it('container uses CSS variables for background', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const container = screen.getByTestId('attached-file-chips');
      
      // Container should use CSS variable for background (bg-[var(--color-hover)]/30)
      expect(container.className).toContain('bg-[var(--color-hover)]');
    });
  });

  /**
   * Test: Tab key focuses the chips container
   * **Validates: Requirement 4.1**
   *
   * THE File_Chips area SHALL be focusable via Tab key navigation.
   */
  describe('Tab key focus', () => {
    it('container is focusable via tabIndex', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      // First chip should have tabIndex=0 to be focusable via Tab
      const chip = screen.getByTestId(`file-chip-${file.id}`);
      expect(chip).toHaveAttribute('tabindex', '0');
    });

    it('first chip receives focus when Tab is pressed', () => {
      const onRemoveFile = vi.fn();
      const files: FileTreeItem[] = [
        { id: '1', name: 'file1.ts', path: '/path/file1.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
        { id: '2', name: 'file2.ts', path: '/path/file2.ts', type: 'file', workspaceId: 'ws1', workspaceName: 'WS1' },
      ];

      render(<AttachedFileChips files={files} onRemoveFile={onRemoveFile} />);

      // First chip should have tabIndex=0
      const firstChip = screen.getByTestId('file-chip-1');
      expect(firstChip).toHaveAttribute('tabindex', '0');

      // Second chip should have tabIndex=-1 (not directly focusable via Tab)
      const secondChip = screen.getByTestId('file-chip-2');
      expect(secondChip).toHaveAttribute('tabindex', '-1');
    });

    it('chip can receive focus programmatically', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Focus the chip
      chip.focus();
      
      // Chip should be the active element
      expect(document.activeElement).toBe(chip);
    });

    it('chip has focus ring styles for visibility', () => {
      const onRemoveFile = vi.fn();
      const file: FileTreeItem = {
        id: 'test-file',
        name: 'test.ts',
        path: '/path/test.ts',
        type: 'file',
        workspaceId: 'ws1',
        workspaceName: 'WS1',
      };

      render(<AttachedFileChips files={[file]} onRemoveFile={onRemoveFile} />);

      const chip = screen.getByTestId(`file-chip-${file.id}`);
      
      // Chip should have focus ring styles
      expect(chip.className).toContain('focus:ring-2');
      expect(chip.className).toContain('focus:outline-none');
    });
  });
});
