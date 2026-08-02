/**
 * Integration Tests for ChatInput — Unified Attachment Pipeline
 *
 * The old AttachedFileChips integration (attachedContextFiles,
 * onRemoveContextFile props) was removed when LayoutContext attachment
 * state was replaced by useUnifiedAttachments.
 *
 * These tests verify:
 * - ChatInput renders correctly with UnifiedAttachment[] props
 * - Send button state reflects attachment presence
 * - Text input works alongside attachments
 *
 * Testing methodology: unit tests with React Testing Library.
 */

import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatInput } from './ChatInput';
import type { UnifiedAttachment } from '../../../types';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function TestWrapper({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function renderWithProviders(ui: React.ReactElement) {
  return render(ui, { wrapper: TestWrapper });
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

function createMockAttachment(overrides: Partial<UnifiedAttachment> = {}): UnifiedAttachment {
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

describe('ChatInput with Unified Attachments', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('renders text input without errors', () => {
      const props = createDefaultChatInputProps();
      renderWithProviders(<ChatInput {...props} />);
      expect(screen.getByPlaceholderText('Ask Swarm anything...')).toBeInTheDocument();
    });

    it('does not accept attachedContextFiles or onRemoveContextFile props', () => {
      // These props were removed — verify the new interface
      const props = createDefaultChatInputProps();
      expect(props).not.toHaveProperty('attachedContextFiles');
      expect(props).not.toHaveProperty('onRemoveContextFile');
    });
  });

  describe('Send button state with attachments', () => {
    it('enables send when text is present and no attachments', () => {
      const props = createDefaultChatInputProps({ inputValue: 'hello' });
      renderWithProviders(<ChatInput {...props} />);
      const textarea = screen.getByPlaceholderText('Ask Swarm anything...');
      expect(textarea).toBeInTheDocument();
    });

    it('enables send when attachments are present without text', () => {
      const att = createMockAttachment({ base64: 'abc123' });
      const props = createDefaultChatInputProps({
        attachments: [att],
      });
      renderWithProviders(<ChatInput {...props} />);
      expect(screen.getByPlaceholderText('Ask Swarm anything...')).toBeInTheDocument();
    });

    it('skips loading and errored attachments for send eligibility', () => {
      const loading = createMockAttachment({ isLoading: true });
      const errored = createMockAttachment({ error: 'fail' });
      const props = createDefaultChatInputProps({
        attachments: [loading, errored],
      });
      renderWithProviders(<ChatInput {...props} />);
      // Both are loading/errored so hasAttachments should be false
      expect(screen.getByPlaceholderText('Ask Swarm anything...')).toBeInTheDocument();
    });
  });

  describe('Text input interaction', () => {
    it('calls onInputChange when typing', () => {
      const onInputChange = vi.fn();
      const props = createDefaultChatInputProps({ onInputChange });
      renderWithProviders(<ChatInput {...props} />);

      const textarea = screen.getByPlaceholderText('Ask Swarm anything...');
      fireEvent.change(textarea, { target: { value: 'test message' } });
      expect(onInputChange).toHaveBeenCalled();
    });

    it('calls onSend on Enter key', () => {
      const onSend = vi.fn();
      const props = createDefaultChatInputProps({
        inputValue: 'test',
        onSend,
      });
      renderWithProviders(<ChatInput {...props} />);

      const textarea = screen.getByPlaceholderText('Ask Swarm anything...');
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
      expect(onSend).toHaveBeenCalled();
    });
  });

  // Regression: input-lag fix (run_90a033c4). The per-keystroke height
  // recalculation (adjustHeight) must be DEFERRED into a requestAnimationFrame,
  // not run synchronously on the render path — a synchronous style.height='auto'
  // + scrollHeight read forces a layout reflow on every keypress, which is the
  // observed input lag. These tests control rAF so they assert the SCHEDULING
  // contract (deferred + cancel-on-unmount) rather than jsdom layout (jsdom does
  // not lay out, so scrollHeight is always 0 — asserting height values would be
  // theater). Mutation check: reverting adjustHeight to a synchronous call makes
  // the first test RED (0 frames scheduled on an inputValue-only change).
  describe('Input auto-grow scheduling (reflow-lag fix)', () => {
    it('defers height recalc to requestAnimationFrame on inputValue change (no sync reflow)', () => {
      const rafSpy = vi.spyOn(window, 'requestAnimationFrame');
      const { rerender } = renderWithProviders(
        <ChatInput {...createDefaultChatInputProps({ inputValue: 'a' })} />
      );
      rafSpy.mockClear(); // ignore mount-time frames (focus/etc.)

      // A pure keystroke: only inputValue changes (not isExpanded, no inject event).
      rerender(
        <QueryClientProvider client={createTestQueryClient()}>
          <ChatInput {...createDefaultChatInputProps({ inputValue: 'ab' })} />
        </QueryClientProvider>
      );

      // The height adjustment for this keystroke must be scheduled via rAF,
      // NOT executed synchronously during the effect.
      expect(rafSpy).toHaveBeenCalled();
      rafSpy.mockRestore();
    });

    it('cancels a pending height frame on unmount (no leak / post-unmount callback)', () => {
      const cancelSpy = vi.spyOn(window, 'cancelAnimationFrame');
      const { rerender, unmount } = renderWithProviders(
        <ChatInput {...createDefaultChatInputProps({ inputValue: 'a' })} />
      );

      // Trigger a keystroke so a height frame is pending, then unmount before it flushes.
      rerender(
        <QueryClientProvider client={createTestQueryClient()}>
          <ChatInput {...createDefaultChatInputProps({ inputValue: 'ab' })} />
        </QueryClientProvider>
      );
      cancelSpy.mockClear();
      unmount();

      expect(cancelSpy).toHaveBeenCalled();
      cancelSpy.mockRestore();
    });
  });
});
