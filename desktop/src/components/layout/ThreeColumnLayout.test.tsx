/**
 * Unit Tests for ThreeColumnLayout - ChatContextBar Removal Verification
 *
 * **Feature: chat-panel-context-bar-removal**
 * **Task 5.2: Write test to verify ChatContextBar is not rendered**
 * **Validates: Requirements 1.1, 1.5**
 *
 * These tests verify that:
 * - ThreeColumnLayout does NOT render ChatContextBar component
 * - ChatDropZone still wraps the main content for drag-drop file attachment
 *
 * Requirements tested:
 * - 1.1: THE System SHALL remove the ChatContextBar component from the Main_Chat_Panel
 * - 1.5: THE ChatDropZone component SHALL continue to wrap the chat content area for drag-drop file attachment
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import ThreeColumnLayout from './ThreeColumnLayout';

// ============== Mocks ==============

// Mock global fetch to prevent jsdom/undici compatibility issues
const mockFetch = vi.fn().mockResolvedValue({
  ok: true,
  json: () => Promise.resolve({}),
  text: () => Promise.resolve(''),
});
vi.stubGlobal('fetch', mockFetch);

// Mock XMLHttpRequest to prevent jsdom/undici compatibility issues
vi.stubGlobal('XMLHttpRequest', vi.fn().mockImplementation(() => ({
  open: vi.fn(),
  send: vi.fn(),
  setRequestHeader: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  abort: vi.fn(),
  readyState: 4,
  status: 200,
  response: '{}',
  responseText: '{}',
})));

// Mock Tauri window API
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    startDragging: vi.fn().mockResolvedValue(undefined),
  }),
}));

// Mock Tauri fs plugin
vi.mock('@tauri-apps/plugin-fs', () => ({
  readTextFile: vi.fn().mockResolvedValue(''),
  writeTextFile: vi.fn().mockResolvedValue(undefined),
}));

// Mock modal components to prevent HTTP requests from their internal pages
// Note: SkillsModal and MCPSettingsModal removed — now integrated into Settings tabs
vi.mock('../modals/SettingsModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) =>
    isOpen ? <div data-testid="settings-modal"><button onClick={onClose}>Close</button></div> : null,
}));

// swarmWorkspacesService removed — singleton workspace model (task 12.9)

// Mock services used by modal pages to avoid API calls
vi.mock('../../services/skills', () => ({
  skillsService: {
    list: vi.fn().mockResolvedValue([]),
    sync: vi.fn().mockResolvedValue({ added: 0, updated: 0, removed: 0 }),
  },
}));

vi.mock('../../services/mcpConfig', () => ({
  mcpConfigService: {
    listAll: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../../services/agents', () => ({
  agentsService: {
    list: vi.fn().mockResolvedValue([]),
    getDefault: vi.fn().mockResolvedValue({ id: 'default', name: 'Default Agent' }),
  },
}));

vi.mock('../../services/settings', () => ({
  settingsService: {
    getAPIConfiguration: vi.fn().mockResolvedValue({ models: [] }),
    getSettings: vi.fn().mockResolvedValue({}),
  },
}));

// Mock window.matchMedia for ThemeProvider
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock localStorage
class MockLocalStorage {
  private store: Map<string, string> = new Map();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  clear(): void {
    this.store.clear();
  }

  get length(): number {
    return this.store.size;
  }

  key(index: number): string | null {
    const keys = Array.from(this.store.keys());
    return keys[index] ?? null;
  }
}

// Store original values
let originalLocalStorage: Storage;
let mockStorage: MockLocalStorage;

// ============== Helper Functions ==============

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
 * Wrapper component that provides all necessary providers
 */
function TestWrapper({ children }: { children: React.ReactNode }) {
  const queryClient = createTestQueryClient();
  return (
    <ThemeProvider>
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            {children}
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>
    </ThemeProvider>
  );
}

/**
 * Renders ThreeColumnLayout with test wrapper
 */
function renderThreeColumnLayout(children: React.ReactNode) {
  return render(
    <TestWrapper>
      <ThreeColumnLayout>
        {children}
      </ThreeColumnLayout>
    </TestWrapper>
  );
}

// ============== Tests ==============

describe('ThreeColumnLayout - ChatContextBar Removal', () => {
  beforeEach(() => {
    // Save original localStorage and replace with mock
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
      configurable: true,
    });

    // Set initial window width
    Object.defineProperty(window, 'innerWidth', {
      value: 1200,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    // Restore original localStorage
    Object.defineProperty(window, 'localStorage', {
      value: originalLocalStorage,
      writable: true,
      configurable: true,
    });
    mockStorage.clear();

    // Cleanup rendered components
    cleanup();
  });

  /**
   * Test: ThreeColumnLayout does not render ChatContextBar
   * **Validates: Requirement 1.1**
   *
   * THE System SHALL remove the ChatContextBar component from the Main_Chat_Panel
   */
  describe('Requirement 1.1: ChatContextBar is not rendered', () => {
    it('should not render any element with ChatContextBar test ID', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // ChatContextBar should not be present in the DOM
      expect(screen.queryByTestId('chat-context-bar')).not.toBeInTheDocument();
    });

    it('should not render workspace scope badge at the top of chat area', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // Workspace scope badge (part of ChatContextBar) should not be present
      expect(screen.queryByTestId('workspace-scope-badge')).not.toBeInTheDocument();
    });

    it('should not render attached files list at the top of chat area', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // Attached files list (part of ChatContextBar) should not be present
      expect(screen.queryByTestId('context-attached-files')).not.toBeInTheDocument();
    });

    it('should not have ChatContextBar as a direct child of MainChatPanel', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // Find the main element (MainChatPanel)
      const mainPanel = document.querySelector('main.flex-1');
      expect(mainPanel).not.toBeNull();

      // Check that ChatContextBar is not a child
      const chatContextBar = mainPanel?.querySelector('[data-testid="chat-context-bar"]');
      expect(chatContextBar).toBeNull();
    });
  });

  /**
   * Test: ChatDropZone still wraps the main content
   * **Validates: Requirement 1.5**
   *
   * ChatDropZone was moved from ThreeColumnLayout to ChatPage so it has
   * direct prop access to useUnifiedAttachments.  ThreeColumnLayout now
   * renders children directly inside MainChatPanel without a drop zone
   * wrapper.  The drop zone is tested at the ChatPage level instead.
   */
  describe('Requirement 1.5: ChatDropZone moved to ChatPage', () => {
    it('should render children directly inside main panel without ChatDropZone wrapper', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // Children should be rendered inside the main panel
      const mainPanel = document.querySelector('main.flex-1');
      expect(mainPanel).not.toBeNull();
      const chatContent = screen.getByTestId('test-chat-content');
      expect(mainPanel?.contains(chatContent)).toBe(true);
    });

    it('should not render ChatDropZone at the ThreeColumnLayout level', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // ChatDropZone is now inside ChatPage, not ThreeColumnLayout
      expect(screen.queryByTestId('chat-drop-zone')).not.toBeInTheDocument();
    });
  });

  /**
   * Test: Layout structure is correct after ChatContextBar removal
   */
  describe('Layout structure verification', () => {
    it('should maintain three-column layout structure', () => {
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">Test Chat Content</div>
      );

      // Left sidebar should be present
      expect(screen.getByTestId('left-sidebar')).toBeInTheDocument();

      // Main panel should be present
      const mainPanel = document.querySelector('main.flex-1');
      expect(mainPanel).not.toBeNull();

      // Children should be rendered
      expect(screen.getByTestId('test-chat-content')).toBeInTheDocument();
    });

    it('should render children content correctly', () => {
      const testContent = 'This is test chat content';
      renderThreeColumnLayout(
        <div data-testid="test-chat-content">{testContent}</div>
      );

      const chatContent = screen.getByTestId('test-chat-content');
      expect(chatContent.textContent).toBe(testContent);
    });
  });
});
