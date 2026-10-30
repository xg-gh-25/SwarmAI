/**
 * ThreeColumnLayout × region boundaries: a strip that throws while rendering shows one
 * line where it was; the chat area and the rest of the layout keep rendering.
 *
 * (Mocks and helpers below are copied from ThreeColumnLayout.test.tsx.)
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

// The strips under test throw while rendering.
vi.mock('./BottomBar', () => ({ BottomBar: () => { throw new Error('bottom bar broke'); } }));
vi.mock('../terminal/TerminalPanel', () => ({ default: () => { throw new Error('terminal broke'); } }));

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

// Settings + OS Eval migrated to the OverlayHost registry (M3-tail) — they render
// through overlaySurfaces (SettingsPage / EvalOverlay), not a Modal wrapper. Mock the
// heavy page content so the layout tests don't fire the pages' internal HTTP.
vi.mock('../../pages/SettingsPage', () => ({
  default: () => <div data-testid="settings-page" />,
}));
vi.mock('../../pages/eval/EvalOverlay', () => ({
  default: () => <div data-testid="eval-dashboard" />,
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

describe('a layout strip that throws stays in its strip', () => {
  beforeEach(() => {
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', { value: mockStorage, writable: true });
  });
  afterEach(() => {
    cleanup();
    Object.defineProperty(window, 'localStorage', { value: originalLocalStorage, writable: true });
  });

  it('shows a one-line error for each broken strip; the chat area still renders', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      renderThreeColumnLayout(<div data-testid="chat-area-child">chat</div>);
      expect(screen.getByTestId('chat-area-child')).toBeInTheDocument();
      expect(screen.getAllByTestId('layout-region-error')).toHaveLength(2);
      expect(screen.queryByText('Something went wrong')).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});
