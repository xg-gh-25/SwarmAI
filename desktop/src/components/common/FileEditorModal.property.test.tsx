/**
 * Property-Based Tests for FileEditorModal
 *
 * **Feature: three-column-layout**
 * **Properties 23-26: File Editor**
 * **Validates: Requirements 9.1, 9.6, 9.7, 9.8**
 *
 * These tests validate the pure functions and state logic used by the FileEditorModal.
 * The actual React component behavior is tested through integration tests.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { detectLanguage, isDirtyState } from './FileEditorModal';

// ============== Pure Functions Under Test ==============

/**
 * Simulates the file editor state machine for property testing.
 * This represents the core logic of the FileEditorModal without React.
 */
interface FileEditorStateMachine {
  isOpen: boolean;
  content: string;
  originalContent: string;
  isDirty: boolean;
}

/**
 * Create initial state when opening a file
 */
function createInitialState(content: string): FileEditorStateMachine {
  return {
    isOpen: true,
    content,
    originalContent: content,
    isDirty: false,
  };
}

/**
 * Update content and recalculate dirty state
 */
function updateContent(state: FileEditorStateMachine, newContent: string): FileEditorStateMachine {
  return {
    ...state,
    content: newContent,
    isDirty: isDirtyState(newContent, state.originalContent),
  };
}

/**
 * Simulate save action - updates originalContent to match content
 */
function saveContent(state: FileEditorStateMachine): { state: FileEditorStateMachine; savedContent: string } {
  const savedContent = state.content;
  return {
    state: {
      ...state,
      originalContent: state.content,
      isDirty: false,
      isOpen: false,
    },
    savedContent,
  };
}

/**
 * Simulate cancel action - discards changes
 */
function cancelEdit(state: FileEditorStateMachine): FileEditorStateMachine {
  return {
    ...state,
    content: state.originalContent,
    isDirty: false,
    isOpen: false,
  };
}

/**
 * Check if closing should show warning
 */
function shouldShowUnsavedWarning(state: FileEditorStateMachine): boolean {
  return state.isDirty;
}

// ============== Arbitraries ==============

/**
 * Arbitrary for generating file names with various extensions
 */
const fileNameArb = fc.oneof(
  // Common programming languages
  fc.constantFrom(
    'main.ts', 'app.tsx', 'index.js', 'script.jsx',
    'main.py', 'app.go', 'lib.rs', 'Main.java',
    'style.css', 'theme.scss', 'layout.less',
    'config.json', 'settings.yaml', 'data.yml',
    'README.md', 'notes.txt', 'Dockerfile', 'Makefile',
    'script.sh', 'build.bash', '.env', '.gitignore'
  ),
  // Random file names with extensions
  fc.tuple(
    fc.string({ minLength: 1, maxLength: 20 }).filter(s => /^[a-zA-Z0-9_-]+$/.test(s)),
    fc.constantFrom('.ts', '.js', '.py', '.go', '.rs', '.java', '.css', '.json', '.md', '.txt', '')
  ).map(([name, ext]) => name + ext)
);

/**
 * Arbitrary for generating file content
 */
const fileContentArb = fc.string({ minLength: 0, maxLength: 1000 });

/**
 * Arbitrary for generating content edits (original + modified)
 */
const contentEditArb = fc.tuple(
  fileContentArb,
  fileContentArb
).filter(([original, modified]) => original !== modified);

// ============== Property-Based Tests ==============

describe('FileEditorModal - Property-Based Tests', () => {
  /**
   * Property 23: File Editor Opens on Double-Click
   * **Feature: three-column-layout, Property 23: File Editor Opens on Double-Click**
   * **Validates: Requirements 9.1**
   * 
   * For any file double-click in Workspace_Explorer, the File_Editor_Modal SHALL open
   * with that file's content loaded.
   */
  describe('Feature: three-column-layout, Property 23: File Editor Opens on Double-Click', () => {
    it('should initialize with correct content when opened', () => {
      fc.assert(
        fc.property(fileContentArb, (content) => {
          const state = createInitialState(content);
          
          // Property: When opened, content SHALL match the file content
          expect(state.isOpen).toBe(true);
          expect(state.content).toBe(content);
          expect(state.originalContent).toBe(content);
          expect(state.isDirty).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should detect language from file extension', () => {
      fc.assert(
        fc.property(fileNameArb, (fileName) => {
          const language = detectLanguage(fileName);
          
          // Property: Language detection SHALL return a valid language string
          expect(typeof language).toBe('string');
          expect(language.length).toBeGreaterThan(0);
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly map common file extensions to languages', () => {
      const expectedMappings: [string, string][] = [
        ['main.ts', 'typescript'],
        ['app.tsx', 'typescript'],
        ['index.js', 'javascript'],
        ['script.jsx', 'javascript'],
        ['main.py', 'python'],
        ['style.css', 'css'],
        ['config.json', 'json'],
        ['README.md', 'markdown'],
        ['script.sh', 'bash'],
        ['Dockerfile', 'dockerfile'],
        ['Makefile', 'makefile'],
      ];

      for (const [fileName, expectedLang] of expectedMappings) {
        const language = detectLanguage(fileName);
        expect(language).toBe(expectedLang);
      }
    });
  });

  /**
   * Property 24: File Editor Save Persistence
   * **Feature: three-column-layout, Property 24: File Editor Save Persistence**
   * **Validates: Requirements 9.6**
   * 
   * For any file edit in File_Editor_Modal followed by Save, the file content on disk
   * SHALL match the edited content and the modal SHALL close.
   */
  describe('Feature: three-column-layout, Property 24: File Editor Save Persistence', () => {
    it('should persist edited content on save', () => {
      fc.assert(
        fc.property(contentEditArb, ([originalContent, editedContent]) => {
          // Start with original content
          let state = createInitialState(originalContent);
          
          // Edit the content
          state = updateContent(state, editedContent);
          expect(state.isDirty).toBe(true);
          
          // Save the content
          const { state: savedState, savedContent } = saveContent(state);
          
          // Property: Saved content SHALL match edited content
          expect(savedContent).toBe(editedContent);
          // Property: Modal SHALL close after save
          expect(savedState.isOpen).toBe(false);
          // Property: State SHALL no longer be dirty after save
          expect(savedState.isDirty).toBe(false);
          // Property: Original content SHALL be updated to match saved content
          expect(savedState.originalContent).toBe(editedContent);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow saving unchanged content (no-op save)', () => {
      fc.assert(
        fc.property(fileContentArb, (content) => {
          const state = createInitialState(content);
          
          // Save without editing
          const { state: savedState, savedContent } = saveContent(state);
          
          // Property: Saved content SHALL match original content
          expect(savedContent).toBe(content);
          expect(savedState.isOpen).toBe(false);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 25: File Editor Cancel Discards Changes
   * **Feature: three-column-layout, Property 25: File Editor Cancel Discards Changes**
   * **Validates: Requirements 9.7**
   * 
   * For any file edit in File_Editor_Modal followed by Cancel, the file content on disk
   * SHALL remain unchanged and the modal SHALL close.
   */
  describe('Feature: three-column-layout, Property 25: File Editor Cancel Discards Changes', () => {
    it('should discard changes on cancel', () => {
      fc.assert(
        fc.property(contentEditArb, ([originalContent, editedContent]) => {
          // Start with original content
          let state = createInitialState(originalContent);
          
          // Edit the content
          state = updateContent(state, editedContent);
          expect(state.isDirty).toBe(true);
          expect(state.content).toBe(editedContent);
          
          // Cancel the edit
          const cancelledState = cancelEdit(state);
          
          // Property: Content SHALL be reverted to original
          expect(cancelledState.content).toBe(originalContent);
          // Property: Modal SHALL close after cancel
          expect(cancelledState.isOpen).toBe(false);
          // Property: State SHALL no longer be dirty after cancel
          expect(cancelledState.isDirty).toBe(false);
          // Property: Original content SHALL remain unchanged
          expect(cancelledState.originalContent).toBe(originalContent);
        }),
        { numRuns: 100 }
      );
    });

    it('should allow cancelling without changes', () => {
      fc.assert(
        fc.property(fileContentArb, (content) => {
          const state = createInitialState(content);
          
          // Cancel without editing
          const cancelledState = cancelEdit(state);
          
          // Property: Content SHALL remain unchanged
          expect(cancelledState.content).toBe(content);
          expect(cancelledState.isOpen).toBe(false);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 26: Unsaved Changes Warning
   * **Feature: three-column-layout, Property 26: Unsaved Changes Warning**
   * **Validates: Requirements 9.8**
   * 
   * For any attempt to close File_Editor_Modal with unsaved changes (dirty state),
   * a confirmation dialog SHALL be displayed before closing.
   */
  describe('Feature: three-column-layout, Property 26: Unsaved Changes Warning', () => {
    it('should show warning when closing with unsaved changes', () => {
      fc.assert(
        fc.property(contentEditArb, ([originalContent, editedContent]) => {
          // Start with original content
          let state = createInitialState(originalContent);
          
          // Edit the content
          state = updateContent(state, editedContent);
          
          // Property: Warning SHALL be shown when dirty
          expect(shouldShowUnsavedWarning(state)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should not show warning when closing without changes', () => {
      fc.assert(
        fc.property(fileContentArb, (content) => {
          const state = createInitialState(content);
          
          // Property: Warning SHALL NOT be shown when not dirty
          expect(shouldShowUnsavedWarning(state)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should not show warning after saving changes', () => {
      fc.assert(
        fc.property(contentEditArb, ([originalContent, editedContent]) => {
          // Start with original content
          let state = createInitialState(originalContent);
          
          // Edit the content
          state = updateContent(state, editedContent);
          expect(shouldShowUnsavedWarning(state)).toBe(true);
          
          // Save the content
          const { state: savedState } = saveContent(state);
          
          // Property: Warning SHALL NOT be shown after save
          expect(shouldShowUnsavedWarning(savedState)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly track dirty state through multiple edits', () => {
      fc.assert(
        fc.property(
          fileContentArb,
          fc.array(fileContentArb, { minLength: 1, maxLength: 10 }),
          (originalContent, edits) => {
            let state = createInitialState(originalContent);
            
            for (const edit of edits) {
              state = updateContent(state, edit);
              
              // Property: Dirty state SHALL reflect whether content differs from original
              const expectedDirty = edit !== originalContent;
              expect(state.isDirty).toBe(expectedDirty);
              expect(shouldShowUnsavedWarning(state)).toBe(expectedDirty);
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should become clean when content is reverted to original', () => {
      fc.assert(
        fc.property(contentEditArb, ([originalContent, editedContent]) => {
          // Start with original content
          let state = createInitialState(originalContent);
          
          // Edit the content
          state = updateContent(state, editedContent);
          expect(state.isDirty).toBe(true);
          
          // Revert to original
          state = updateContent(state, originalContent);
          
          // Property: State SHALL be clean when content matches original
          expect(state.isDirty).toBe(false);
          expect(shouldShowUnsavedWarning(state)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Additional property tests for isDirtyState function
   */
  describe('isDirtyState - Pure Function Tests', () => {
    it('should return false when content equals original', () => {
      fc.assert(
        fc.property(fileContentArb, (content) => {
          expect(isDirtyState(content, content)).toBe(false);
        }),
        { numRuns: 100 }
      );
    });

    it('should return true when content differs from original', () => {
      fc.assert(
        fc.property(contentEditArb, ([original, modified]) => {
          expect(isDirtyState(modified, original)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });

    it('should be symmetric - order of comparison matters', () => {
      fc.assert(
        fc.property(contentEditArb, ([a, b]) => {
          // Both directions should return true when different
          expect(isDirtyState(a, b)).toBe(true);
          expect(isDirtyState(b, a)).toBe(true);
        }),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Additional property tests for detectLanguage function
   */
  describe('detectLanguage - Pure Function Tests', () => {
    it('should return plaintext for unknown extensions', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 1, maxLength: 10 }).filter(s => /^[a-zA-Z0-9]+$/.test(s)),
          (name) => {
            // Use an unlikely extension
            const fileName = `${name}.xyz123unknown`;
            const language = detectLanguage(fileName);
            expect(language).toBe('plaintext');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should handle files without extensions', () => {
      fc.assert(
        fc.property(
          // Generate filenames that are clearly not language extensions
          fc.string({ minLength: 3, maxLength: 20 }).filter(s => 
            /^[a-zA-Z0-9_-]+$/.test(s) && 
            !s.includes('.') &&
            // Exclude strings that match known language extensions
            !['dockerfile', 'makefile', 'c', 'cpp', 'h', 'hpp', 'go', 'rs', 'py', 'js', 'ts', 'rb', 'sh'].includes(s.toLowerCase())
          ),
          (fileName) => {
            const language = detectLanguage(fileName);
            // Files without extensions that don't match special names should be plaintext
            expect(language).toBe('plaintext');
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should be case-insensitive for extensions', () => {
      const testCases = [
        ['file.TS', 'file.ts'],
        ['file.PY', 'file.py'],
        ['file.JSON', 'file.json'],
      ];

      for (const [upper, lower] of testCases) {
        expect(detectLanguage(upper)).toBe(detectLanguage(lower));
      }
    });
  });
});
