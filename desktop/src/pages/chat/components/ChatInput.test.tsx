/**
 * Integration Tests for ChatInput with AttachedFileChips
 *
 * **Feature: chat-panel-context-bar-removal**
 * **Task 2.3: Write integration tests for ChatInput with AttachedFileChips**
 * **Validates: Requirements 2.1, 2.5, 2.6**
 *
 * These tests verify that ChatInput correctly integrates with AttachedFileChips
 * to display and manage context files attached from Workspace Explorer.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatInput } from './ChatInput';
import type { FileAttachment } from '../../../types';
import type { FileTreeItem } from '../../../components/workspace-explorer/FileTreeNode';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

// Create a QueryClient for testing
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

// Wrapper component that provides QueryClient
function TestWrapper({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}

// Custom render function that wraps with providers
function renderWithProviders(ui: React.ReactElement) {
  return render(ui, { wrapper: TestWrapper });
}

// ============== Test Helpers ==============

/**
 * Creates default props for ChatInput component
 * ChatInput has many required props, so this helper provides sensible defaults
 */
function createDefaultChatInputProps(overrides: Partial<Parameters<typeof ChatInput>[0]> = {}) {
  return {
    inputValue: '',
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    isStreaming: false,
    selectedAgentId: 'agent-1',
    attachments: [] as FileAttachment[],
    onAddFiles: vi.fn(),
    onRemoveFile: vi.fn(),
    isProcessingFiles: false,
    fileError: null as string | null,
    canAddMore: true,
    attachedContextFiles: [] as FileTreeItem[],
    onRemoveContextFile: vi.fn(),
    ...overrides,
  };
}

/**
 * Creates a mock FileTreeItem for testing
 */
function createMockFileTreeItem(overrides: Partial<FileTreeItem> = {}): FileTreeItem {
  const id = overrides.id || `file-${Math.random().toString(36).substr(2, 9)}`;
  return {
    id,
    name: overrides.name || `test-file-${id}.ts`,
    path: overrides.path || `/workspace/src/test-file-${id}.ts`,
    type: 'file',
    workspaceId: overrides.workspaceId || 'workspace-1',
    workspaceName: overrides.workspaceName || 'Test Workspace',
    isSwarmWorkspace: overrides.isSwarmWorkspace ?? false,
    ...overrides,
  };
}

/**
 * Creates multiple mock FileTreeItems
 */
function createMockFileTreeItems(count: number): FileTreeItem[] {
  return Array.from({ length: count }, (_, i) =>
    createMockFileTreeItem({
      id: `file-${i}`,
      name: `file-${i}.ts`,
      path: `/workspace/src/file-${i}.ts`,
    })
  );
}

// ============== Integration Tests ==============

describe('ChatInput with AttachedFileChips Integration', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  /**
   * Test: ChatInput renders AttachedFileChips when attachedContextFiles is provided
   * **Validates: Requirement 2.1**
   *
   * WHEN files are attached to the chat context,
   * THE ChatInput component SHALL display File_Chips above the text input field
   */
  describe('Requirement 2.1: Display File_Chips when files are attached', () => {
    it('renders AttachedFileChips when attachedContextFiles has files', () => {
      const files = createMockFileTreeItems(3);
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      // AttachedFileChips container should be rendered
      const chipsContainer = screen.getByTestId('attached-file-chips');
      expect(chipsContainer).toBeInTheDocument();

      // Each file should have a corresponding chip
      for (const file of files) {
        const chip = screen.getByTestId(`file-chip-${file.id}`);
        expect(chip).toBeInTheDocument();
      }
    });

    it('renders single file as chip', () => {
      const file = createMockFileTreeItem({ id: 'single-file', name: 'single.ts' });
      const props = createDefaultChatInputProps({
        attachedContextFiles: [file],
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      const chip = screen.getByTestId('file-chip-single-file');
      expect(chip).toBeInTheDocument();
      expect(chip.textContent).toContain('single.ts');
    });

    it('renders multiple files as chips', () => {
      const files = [
        createMockFileTreeItem({ id: 'file-a', name: 'fileA.ts' }),
        createMockFileTreeItem({ id: 'file-b', name: 'fileB.tsx' }),
        createMockFileTreeItem({ id: 'file-c', name: 'fileC.json' }),
      ];
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      expect(screen.getByTestId('file-chip-file-a')).toBeInTheDocument();
      expect(screen.getByTestId('file-chip-file-b')).toBeInTheDocument();
      expect(screen.getByTestId('file-chip-file-c')).toBeInTheDocument();
    });
  });

  /**
   * Test: ChatInput does not render AttachedFileChips when attachedContextFiles is empty
   * **Validates: Requirement 2.6**
   *
   * WHEN no files are attached,
   * THE ChatInput component SHALL NOT display the File_Chips area
   */
  describe('Requirement 2.6: Do not display File_Chips when no files attached', () => {
    it('does not render AttachedFileChips when attachedContextFiles is empty array', () => {
      const props = createDefaultChatInputProps({
        attachedContextFiles: [],
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      // AttachedFileChips container should NOT be rendered
      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });

    it('does not render AttachedFileChips when attachedContextFiles is undefined', () => {
      const props = createDefaultChatInputProps({
        attachedContextFiles: undefined,
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });

    it('does not render AttachedFileChips when onRemoveContextFile is undefined', () => {
      const files = createMockFileTreeItems(2);
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: undefined,
      });

      renderWithProviders(<ChatInput {...props} />);

      // Should not render because the condition requires both files AND callback
      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });
  });

  /**
   * Test: Remove callback is called when chip close button is clicked
   * **Validates: Requirement 2.5**
   *
   * WHEN a user clicks the close button on a File_Chip,
   * THE System SHALL remove that file from the Attached_Files list
   */
  describe('Requirement 2.5: Remove file when close button clicked', () => {
    it('calls onRemoveContextFile when chip close button is clicked', () => {
      const file = createMockFileTreeItem({ id: 'removable-file', name: 'removable.ts' });
      const onRemoveContextFile = vi.fn();
      const props = createDefaultChatInputProps({
        attachedContextFiles: [file],
        onRemoveContextFile,
      });

      renderWithProviders(<ChatInput {...props} />);

      const chip = screen.getByTestId('file-chip-removable-file');
      const closeButton = chip.querySelector('button');
      expect(closeButton).toBeInTheDocument();

      fireEvent.click(closeButton!);

      expect(onRemoveContextFile).toHaveBeenCalledTimes(1);
      expect(onRemoveContextFile).toHaveBeenCalledWith(file);
    });

    it('calls onRemoveContextFile with correct file when multiple files exist', () => {
      const files = [
        createMockFileTreeItem({ id: 'file-1', name: 'first.ts' }),
        createMockFileTreeItem({ id: 'file-2', name: 'second.ts' }),
        createMockFileTreeItem({ id: 'file-3', name: 'third.ts' }),
      ];
      const onRemoveContextFile = vi.fn();
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile,
      });

      renderWithProviders(<ChatInput {...props} />);

      // Click close button on the second file
      const secondChip = screen.getByTestId('file-chip-file-2');
      const closeButton = secondChip.querySelector('button');
      fireEvent.click(closeButton!);

      expect(onRemoveContextFile).toHaveBeenCalledTimes(1);
      expect(onRemoveContextFile).toHaveBeenCalledWith(files[1]);
    });

    it('allows removing multiple files sequentially', () => {
      const files = createMockFileTreeItems(3);
      const onRemoveContextFile = vi.fn();
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile,
      });

      renderWithProviders(<ChatInput {...props} />);

      // Remove first file
      const firstChip = screen.getByTestId('file-chip-file-0');
      fireEvent.click(firstChip.querySelector('button')!);

      // Remove third file
      const thirdChip = screen.getByTestId('file-chip-file-2');
      fireEvent.click(thirdChip.querySelector('button')!);

      expect(onRemoveContextFile).toHaveBeenCalledTimes(2);
      expect(onRemoveContextFile).toHaveBeenNthCalledWith(1, files[0]);
      expect(onRemoveContextFile).toHaveBeenNthCalledWith(2, files[2]);
    });
  });

  /**
   * Additional integration tests for ChatInput behavior with AttachedFileChips
   */
  describe('ChatInput integration behavior', () => {
    it('renders AttachedFileChips alongside other ChatInput elements', () => {
      const files = createMockFileTreeItems(2);
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
      });

      renderWithProviders(<ChatInput {...props} />);

      // AttachedFileChips should be present
      expect(screen.getByTestId('attached-file-chips')).toBeInTheDocument();

      // Other ChatInput elements should also be present
      expect(screen.getByPlaceholderText('Ask anything')).toBeInTheDocument();
    });

    it('AttachedFileChips does not interfere with text input', () => {
      const files = createMockFileTreeItems(2);
      const onInputChange = vi.fn();
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
        onInputChange,
      });

      renderWithProviders(<ChatInput {...props} />);

      const textarea = screen.getByPlaceholderText('Ask anything');
      fireEvent.change(textarea, { target: { value: 'test message' } });

      expect(onInputChange).toHaveBeenCalled();
    });

    it('AttachedFileChips does not interfere with send functionality', () => {
      const files = createMockFileTreeItems(2);
      const onSend = vi.fn();
      const props = createDefaultChatInputProps({
        inputValue: 'test message',
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
        onSend,
      });

      renderWithProviders(<ChatInput {...props} />);

      const textarea = screen.getByPlaceholderText('Ask anything');
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });

      expect(onSend).toHaveBeenCalled();
    });

    it('updates display when attachedContextFiles prop changes', () => {
      const initialFiles = [createMockFileTreeItem({ id: 'initial', name: 'initial.ts' })];
      const props = createDefaultChatInputProps({
        attachedContextFiles: initialFiles,
        onRemoveContextFile: vi.fn(),
      });

      const { rerender } = renderWithProviders(<ChatInput {...props} />);

      // Initial file should be displayed
      expect(screen.getByTestId('file-chip-initial')).toBeInTheDocument();

      // Update with new files
      const newFiles = [
        createMockFileTreeItem({ id: 'new-1', name: 'new1.ts' }),
        createMockFileTreeItem({ id: 'new-2', name: 'new2.ts' }),
      ];
      rerender(
        <TestWrapper>
          <ChatInput {...props} attachedContextFiles={newFiles} />
        </TestWrapper>
      );

      // Old file should be gone, new files should be displayed
      expect(screen.queryByTestId('file-chip-initial')).not.toBeInTheDocument();
      expect(screen.getByTestId('file-chip-new-1')).toBeInTheDocument();
      expect(screen.getByTestId('file-chip-new-2')).toBeInTheDocument();
    });

    it('hides AttachedFileChips when all files are removed', () => {
      const files = [createMockFileTreeItem({ id: 'last-file', name: 'last.ts' })];
      const props = createDefaultChatInputProps({
        attachedContextFiles: files,
        onRemoveContextFile: vi.fn(),
      });

      const { rerender } = renderWithProviders(<ChatInput {...props} />);

      // Initially visible
      expect(screen.getByTestId('attached-file-chips')).toBeInTheDocument();

      // Remove all files
      rerender(
        <TestWrapper>
          <ChatInput {...props} attachedContextFiles={[]} />
        </TestWrapper>
      );

      // Should no longer be visible
      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });
  });
});
