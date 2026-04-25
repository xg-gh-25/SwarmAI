/**
 * Property-based tests for workspace selector removal from ChatInput.
 *
 * What is being tested: ChatInput component (desktop/src/pages/chat/components/ChatInput.tsx)
 * and the streamChat call in chatService (desktop/src/services/chat.ts).
 *
 * Testing methodology: Property-based testing with fast-check + Vitest + @testing-library/react.
 *
 * Key properties verified:
 *   - Property 1: ChatInput never renders workspace indicator elements (Validates: Requirements 2.1)
 *   - Property 2: ChatInput preserves all non-workspace UI elements (Validates: Requirements 2.3)
 *   - Property 3: streamChat call never includes workspaceContext (Validates: Requirements 4.4)
 *
 * Each property runs with `{ numRuns: 30 }`.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import * as fc from 'fast-check';
import { render, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatInput } from './ChatInput';
import type { FileAttachment } from '../../../types';
import { chatService } from '../../../services/chat';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

// ============== Test Helpers ==============

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
}

const testQueryClient = createTestQueryClient();

function TestWrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={testQueryClient}>
      {children}
    </QueryClientProvider>
  );
}

function renderWithProviders(ui: React.ReactElement) {
  return render(ui, { wrapper: TestWrapper });
}

// ============== fast-check Arbitraries ==============

/** Arbitrary for generating a valid FileAttachment */
const arbitraryAttachment = (): fc.Arbitrary<FileAttachment> =>
  fc.record({
    id: fc.uuid(),
    file: fc.constant(new File(['test'], 'test.txt', { type: 'text/plain' })),
    name: fc.string({ minLength: 1, maxLength: 30 }),
    type: fc.constantFrom('image' as const, 'pdf' as const, 'text' as const, 'csv' as const),
    size: fc.integer({ min: 1, max: 10_000_000 }),
    mediaType: fc.constant('text/plain'),
    isLoading: fc.constant(false),
  });

/**
 * Arbitrary for generating random valid ChatInput props.
 * Varies: inputValue, isStreaming, selectedAgentId, attachments (0-3 items).
 * All other required props use stable defaults.
 */
const arbitraryChatInputProps = () =>
  fc.record({
    inputValue: fc.string({ maxLength: 200 }),
    isStreaming: fc.boolean(),
    selectedAgentId: fc.string({ minLength: 1, maxLength: 30 }),
    attachments: fc.array(arbitraryAttachment(), { minLength: 0, maxLength: 3 }),
  });

/** Builds a complete ChatInputProps object from the randomized subset. */
function buildProps(random: {
  inputValue: string;
  isStreaming: boolean;
  selectedAgentId: string;
  attachments: FileAttachment[];
}) {
  return {
    inputValue: random.inputValue,
    onInputChange: vi.fn(),
    onSend: vi.fn(),
    onStop: vi.fn(),
    isStreaming: random.isStreaming,
    selectedAgentId: random.selectedAgentId,
    attachments: random.attachments,
    onAddFiles: vi.fn(),
    onRemoveFile: vi.fn(),
    isProcessingFiles: false,
    fileError: null as string | null,
    canAddMore: true,
  };
}

// ============== Property-Based Tests ==============

describe('Feature: remove-workspace-selector', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  /**
   * Property 1: ChatInput never renders workspace indicator
   *
   * For any valid combination of ChatInput props, the rendered output
   * must not contain workspace name labels, icons, or file path indicator blocks.
   *
   * **Validates: Requirements 2.1**
   */
  it('Property 1: ChatInput never renders workspace indicator', () => {
    fc.assert(
      fc.property(arbitraryChatInputProps(), (random) => {
        const props = buildProps(random);
        const { container } = renderWithProviders(<ChatInput {...props} />);

        // No text containing "SwarmWS" (the legacy workspace name)
        expect(container.textContent).not.toContain('SwarmWS');

        // No element with testid "workspace-indicator"
        expect(container.querySelector('[data-testid="workspace-indicator"]')).toBeNull();

        // No element with a class containing "workspace"
        const allElements = container.querySelectorAll('*');
        for (const el of allElements) {
          const classList = Array.from(el.classList);
          for (const cls of classList) {
            expect(cls.toLowerCase()).not.toContain('workspace');
          }
        }

        cleanup();
      }),
      { numRuns: 30 },
    );
  });

  /**
   * Property 2: ChatInput preserves all non-workspace UI elements
   *
   * For any valid combination of ChatInput props, the rendered output
   * must contain a textarea (text input), a send/stop button, and a
   * file attachment button.
   *
   * **Validates: Requirements 2.3**
   */
  it('Property 2: ChatInput preserves all non-workspace UI elements', () => {
    fc.assert(
      fc.property(arbitraryChatInputProps(), (random) => {
        const props = buildProps(random);
        const { container } = renderWithProviders(<ChatInput {...props} />);

        // Must have a textarea for text input
        const textarea = container.querySelector('textarea');
        expect(textarea).not.toBeNull();

        // Must have a send or stop button
        const buttons = container.querySelectorAll('button');
        expect(buttons.length).toBeGreaterThan(0);

        // Must have a file attachment button (the FileAttachmentButton component)
        // It renders as a label with an input[type="file"] inside
        const fileInput = container.querySelector('input[type="file"]');
        expect(fileInput).not.toBeNull();

        cleanup();
      }),
      { numRuns: 30 },
    );
  });

  /**
   * Property 3: streamChat call never includes workspaceContext
   *
   * For any chat message sent through ChatPage, the streamChat call
   * must NOT include a workspaceContext field. The backend assembles
   * workspace context independently from the filesystem via ContextAssembler.
   *
   * We verify this by mocking fetch and calling chatService.streamChat
   * with the same shape of request that ChatPage constructs (no workspaceContext),
   * then asserting the serialized request body never contains workspace_context.
   *
   * **Validates: Requirements 4.4**
   */
  it('Property 3: streamChat call never includes workspaceContext', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 200 }).filter((s) => s.trim().length > 0),
        (messageText) => {
          // Capture the fetch call
          const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
            new Response(new ReadableStream(), { status: 200 }),
          );

          try {
            // Build request exactly as ChatPage does — no workspaceContext
            chatService.streamChat(
              {
                agentId: 'test-agent',
                message: messageText,
                sessionId: 'test-session',
                enableSkills: false,
                enableMCP: false,
              },
              vi.fn(),
              vi.fn(),
              vi.fn(),
            );

            // Assert fetch was called
            expect(fetchSpy).toHaveBeenCalledTimes(1);

            // Parse the request body that was sent
            const callArgs = fetchSpy.mock.calls[0];
            const init = callArgs[1] as RequestInit;
            const body = JSON.parse(init.body as string);

            // workspaceContext / workspace_context must NOT be present
            expect(body).not.toHaveProperty('workspace_context');
            expect(body).not.toHaveProperty('workspaceContext');
          } finally {
            fetchSpy.mockRestore();
          }
        },
      ),
      { numRuns: 30 },
    );
  });
});
