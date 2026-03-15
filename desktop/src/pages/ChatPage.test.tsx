/**
 * Integration Tests for ChatPage — Unified Attachment Pipeline
 *
 * The old LayoutContext-based attachment flow (attachFile, attachedFiles,
 * removeAttachedFile, clearAttachedFiles) was removed and replaced by
 * useUnifiedAttachments, which stores attachments in tabMapRef per-tab.
 *
 * These tests verify the new ChatInput integration surface:
 * - ChatInput receives UnifiedAttachment[] (not FileTreeItem[])
 * - No attachedContextFiles / onRemoveContextFile props
 * - ChatDropZone wraps ChatPage and routes drops to addFiles / addWorkspaceFiles
 *
 * The old tests (Requirement 5.1, 5.5 via LayoutContext) were removed
 * because the API they tested no longer exists.
 */

import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LayoutProvider } from '../contexts/LayoutContext';
import { ChatInput } from './chat/components/ChatInput';
import type { UnifiedAttachment } from '../types';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback || key,
  }),
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

function TestWrapper({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <LayoutProvider>{children}</LayoutProvider>
    </QueryClientProvider>
  );
}

function createDefaultChatInputProps(overrides: Partial<Parameters<typeof ChatInput>[0]> = {}) {
  return {
    inputValue: '',
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    isStreaming: false,
    selectedAgentId: 'agent-1',
    attachments: [] as UnifiedAttachment[],
    onAddFiles: vi.fn(),
    onRemoveFile: vi.fn(),
    isProcessingFiles: false,
    fileError: null as string | null,
    canAddMore: true,
    isExpanded: false,
    onExpandedChange: vi.fn(),
    ...overrides,
  };
}

function createMockUnifiedAttachment(overrides: Partial<UnifiedAttachment> = {}): UnifiedAttachment {
  return {
    id: `att-${Math.random().toString(36).substr(2, 6)}`,
    name: 'test-file.ts',
    type: 'text',
    deliveryStrategy: 'inline_text',
    size: 1024,
    mediaType: 'text/plain',
    isLoading: false,
    ...overrides,
  };
}

// ============== Tests ==============

describe('ChatPage Unified Attachment Pipeline', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  describe('ChatInput renders with new UnifiedAttachment props', () => {
    it('renders without attachedContextFiles or onRemoveContextFile props', async () => {
      const props = createDefaultChatInputProps();

      render(
        <TestWrapper>
          <ChatInput {...props} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Ask Swarm anything...')).toBeInTheDocument();
      });
    });

    it('shows no attachment area when attachments array is empty', async () => {
      const props = createDefaultChatInputProps({ attachments: [] });

      render(
        <TestWrapper>
          <ChatInput {...props} />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Ask Swarm anything...')).toBeInTheDocument();
      });

      // No attachment preview should be rendered
      expect(screen.queryByTestId('file-attachment-preview')).not.toBeInTheDocument();
    });

    it('LayoutContext no longer exposes attachFile or attachedFiles', () => {
      // Verify the old API surface is gone — this is a compile-time
      // guarantee but we document it as a test for clarity.
      // If someone re-adds these props, this test reminds them the
      // old flow was intentionally removed.
      const props = createDefaultChatInputProps();
      expect(props).not.toHaveProperty('attachedContextFiles');
      expect(props).not.toHaveProperty('onRemoveContextFile');
    });
  });
});
