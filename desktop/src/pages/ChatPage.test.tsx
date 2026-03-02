/**
 * Integration Tests for ChatPage File Attachment Flow
 *
 * **Feature: chat-panel-context-bar-removal**
 * **Task 4.2: Write integration test for ChatPage file attachment flow**
 * **Validates: Requirements 5.1, 5.5**
 *
 * These tests verify the integration between LayoutContext and ChatInput
 * for file attachment display. Since ChatPage is a complex component with
 * many dependencies, we test the integration at the LayoutContext + ChatInput
 * level, which is the actual integration point for file attachments.
 *
 * Requirements tested:
 * - 5.1: WHEN a user drags a file from Workspace Explorer and drops it on the chat panel,
 *        THE System SHALL add the file to Attached_Files and display it as a File_Chip in ChatInput
 * - 5.5: WHEN the Attached_Files list changes in LayoutContext, THE ChatInput component
 *        SHALL reactively update the File_Chips display
 */

import React, { useEffect, useState } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LayoutProvider, useLayout } from '../contexts/LayoutContext';
import { ChatInput } from './chat/components/ChatInput';
import type { FileTreeItem } from '../components/workspace-explorer/FileTreeNode';
import type { FileAttachment } from '../types';

// ============== Mocks ==============

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key,
  }),
}));

// ============== Test Helpers ==============

/**
 * Creates a QueryClient for testing
 */
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });
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
 * Creates default props for ChatInput component
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
    ...overrides,
  };
}

/**
 * Wrapper component that provides all necessary providers
 */
function TestWrapper({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <LayoutProvider>
        {children}
      </LayoutProvider>
    </QueryClientProvider>
  );
}

/**
 * Integration test component that connects LayoutContext to ChatInput
 * This simulates how ChatPage connects these components
 */
function ChatInputWithLayoutContext({
  onContextReady,
  chatInputProps = {},
}: {
  onContextReady?: (context: ReturnType<typeof useLayout>) => void;
  chatInputProps?: Partial<Parameters<typeof ChatInput>[0]>;
}) {
  const { attachedFiles, removeAttachedFile, attachFile, clearAttachedFiles } = useLayout();
  const [contextExposed, setContextExposed] = useState(false);
  
  // Expose context to test via callback (only once)
  useEffect(() => {
    if (onContextReady && !contextExposed) {
      onContextReady({ attachedFiles, removeAttachedFile, attachFile, clearAttachedFiles } as ReturnType<typeof useLayout>);
      setContextExposed(true);
    }
  }, [onContextReady, attachFile, removeAttachedFile, clearAttachedFiles, attachedFiles, contextExposed]);

  const defaultProps = createDefaultChatInputProps({
    attachedContextFiles: attachedFiles,
    onRemoveContextFile: removeAttachedFile,
    ...chatInputProps,
  });

  return <ChatInput {...defaultProps} />;
}

// ============== Integration Tests ==============

describe('ChatPage File Attachment Flow Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  /**
   * Test: Files attached via LayoutContext appear as chips in ChatInput
   * **Validates: Requirement 5.1**
   *
   * WHEN a user drags a file from Workspace Explorer and drops it on the chat panel,
   * THE System SHALL add the file to Attached_Files and display it as a File_Chip in ChatInput
   *
   * Note: We test this by simulating the LayoutContext state change that would occur
   * when a file is dropped, since the actual drag-drop is handled by ChatDropZone
   * which calls LayoutContext.attachFile()
   */
  describe('Requirement 5.1: Files attached via drag-drop appear as chips', () => {
    it('displays file chips when files are attached via LayoutContext', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      // Wait for component to render
      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Simulate attaching a file via LayoutContext (as would happen from drag-drop)
      const testFile = createMockFileTreeItem({
        id: 'drag-drop-file',
        name: 'dropped-file.ts',
        path: '/workspace/src/dropped-file.ts',
      });

      act(() => {
        layoutContext?.attachFile(testFile);
      });

      // Verify the file chip appears in ChatInput
      await waitFor(() => {
        const chip = screen.getByTestId('file-chip-drag-drop-file');
        expect(chip).toBeInTheDocument();
        expect(chip.textContent).toContain('dropped-file.ts');
      });
    });

    it('displays multiple file chips when multiple files are attached', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach multiple files
      const files = [
        createMockFileTreeItem({ id: 'file-1', name: 'first.ts' }),
        createMockFileTreeItem({ id: 'file-2', name: 'second.tsx' }),
        createMockFileTreeItem({ id: 'file-3', name: 'third.json' }),
      ];

      act(() => {
        files.forEach((file) => layoutContext?.attachFile(file));
      });

      // Verify all file chips appear
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-file-1')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-file-2')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-file-3')).toBeInTheDocument();
      });
    });

    it('does not display duplicate chips when same file is attached twice', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      const testFile = createMockFileTreeItem({
        id: 'duplicate-file',
        name: 'duplicate.ts',
      });

      // Attach the same file twice
      act(() => {
        layoutContext?.attachFile(testFile);
        layoutContext?.attachFile(testFile);
      });

      // Should only have one chip
      await waitFor(() => {
        const chips = screen.getAllByTestId('file-chip-duplicate-file');
        expect(chips).toHaveLength(1);
      });
    });
  });

  /**
   * Test: Removing a chip updates LayoutContext state
   * **Validates: Requirement 5.5**
   *
   * WHEN the Attached_Files list changes in LayoutContext,
   * THE ChatInput component SHALL reactively update the File_Chips display
   */
  describe('Requirement 5.5: Removing chip updates LayoutContext state', () => {
    it('removes file from LayoutContext when chip close button is clicked', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach a file
      const testFile = createMockFileTreeItem({
        id: 'removable-file',
        name: 'removable.ts',
      });

      act(() => {
        layoutContext?.attachFile(testFile);
      });

      // Wait for chip to appear
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-removable-file')).toBeInTheDocument();
      });

      // Click the close button on the chip
      const chip = screen.getByTestId('file-chip-removable-file');
      const closeButton = chip.querySelector('button');
      expect(closeButton).toBeInTheDocument();

      act(() => {
        fireEvent.click(closeButton!);
      });

      // Verify chip is removed from display
      await waitFor(() => {
        expect(screen.queryByTestId('file-chip-removable-file')).not.toBeInTheDocument();
      });
    });

    it('reactively updates display when LayoutContext attachedFiles changes', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach files
      const files = [
        createMockFileTreeItem({ id: 'reactive-1', name: 'reactive1.ts' }),
        createMockFileTreeItem({ id: 'reactive-2', name: 'reactive2.ts' }),
      ];

      act(() => {
        files.forEach((file) => layoutContext?.attachFile(file));
      });

      // Verify both chips appear
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-reactive-1')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-reactive-2')).toBeInTheDocument();
      });

      // Remove one file via LayoutContext
      act(() => {
        layoutContext?.removeAttachedFile(files[0]);
      });

      // Verify display updates reactively
      await waitFor(() => {
        expect(screen.queryByTestId('file-chip-reactive-1')).not.toBeInTheDocument();
        expect(screen.getByTestId('file-chip-reactive-2')).toBeInTheDocument();
      });
    });

    it('clears all chips when clearAttachedFiles is called', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach multiple files
      const files = [
        createMockFileTreeItem({ id: 'clear-1', name: 'clear1.ts' }),
        createMockFileTreeItem({ id: 'clear-2', name: 'clear2.ts' }),
        createMockFileTreeItem({ id: 'clear-3', name: 'clear3.ts' }),
      ];

      act(() => {
        files.forEach((file) => layoutContext?.attachFile(file));
      });

      // Verify all chips appear
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-clear-1')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-clear-2')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-clear-3')).toBeInTheDocument();
      });

      // Clear all files
      act(() => {
        layoutContext?.clearAttachedFiles();
      });

      // Verify all chips are removed
      await waitFor(() => {
        expect(screen.queryByTestId('file-chip-clear-1')).not.toBeInTheDocument();
        expect(screen.queryByTestId('file-chip-clear-2')).not.toBeInTheDocument();
        expect(screen.queryByTestId('file-chip-clear-3')).not.toBeInTheDocument();
      });
    });

    it('removes correct file when multiple files exist and one is removed', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach multiple files
      const files = [
        createMockFileTreeItem({ id: 'multi-1', name: 'first.ts' }),
        createMockFileTreeItem({ id: 'multi-2', name: 'second.ts' }),
        createMockFileTreeItem({ id: 'multi-3', name: 'third.ts' }),
      ];

      act(() => {
        files.forEach((file) => layoutContext?.attachFile(file));
      });

      await waitFor(() => {
        expect(screen.getByTestId('file-chip-multi-1')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-multi-2')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-multi-3')).toBeInTheDocument();
      });

      // Remove the middle file by clicking its close button
      const middleChip = screen.getByTestId('file-chip-multi-2');
      const closeButton = middleChip.querySelector('button');

      act(() => {
        fireEvent.click(closeButton!);
      });

      // Verify only the middle file is removed
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-multi-1')).toBeInTheDocument();
        expect(screen.queryByTestId('file-chip-multi-2')).not.toBeInTheDocument();
        expect(screen.getByTestId('file-chip-multi-3')).toBeInTheDocument();
      });
    });
  });

  /**
   * Additional integration tests for edge cases
   */
  describe('Edge cases and additional scenarios', () => {
    it('handles files from different workspaces', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach files from different workspaces
      const files = [
        createMockFileTreeItem({
          id: 'ws1-file',
          name: 'workspace1-file.ts',
          workspaceId: 'workspace-1',
          workspaceName: 'Workspace 1',
        }),
        createMockFileTreeItem({
          id: 'ws2-file',
          name: 'workspace2-file.ts',
          workspaceId: 'workspace-2',
          workspaceName: 'Workspace 2',
        }),
      ];

      act(() => {
        files.forEach((file) => layoutContext?.attachFile(file));
      });

      // Both files should be displayed regardless of workspace
      await waitFor(() => {
        expect(screen.getByTestId('file-chip-ws1-file')).toBeInTheDocument();
        expect(screen.getByTestId('file-chip-ws2-file')).toBeInTheDocument();
      });
    });

    it('maintains file chips when other ChatInput interactions occur', async () => {
      let layoutContext: ReturnType<typeof useLayout> | null = null;
      const onInputChange = vi.fn();

      render(
        <TestWrapper>
          <ChatInputWithLayoutContext
            onContextReady={(ctx) => {
              layoutContext = ctx;
            }}
            chatInputProps={{ onInputChange }}
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // Attach a file
      const testFile = createMockFileTreeItem({
        id: 'persistent-file',
        name: 'persistent.ts',
      });

      act(() => {
        layoutContext?.attachFile(testFile);
      });

      await waitFor(() => {
        expect(screen.getByTestId('file-chip-persistent-file')).toBeInTheDocument();
      });

      // Type in the input (simulating user interaction)
      const textarea = screen.getByPlaceholderText('chat.placeholder');
      fireEvent.change(textarea, { target: { value: 'test message' } });

      // File chip should still be present
      expect(screen.getByTestId('file-chip-persistent-file')).toBeInTheDocument();
      expect(onInputChange).toHaveBeenCalled();
    });

    it('shows no chips area when no files are attached', async () => {
      render(
        <TestWrapper>
          <ChatInputWithLayoutContext />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('chat.placeholder')).toBeInTheDocument();
      });

      // No chips container should be present
      expect(screen.queryByTestId('attached-file-chips')).not.toBeInTheDocument();
    });
  });
});
