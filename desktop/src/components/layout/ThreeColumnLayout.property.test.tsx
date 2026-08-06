import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, act, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import ThreeColumnLayout from './ThreeColumnLayout';
import { LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';

// ============== Test Setup ==============

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

// The Hive nav card is desktop-only (isDesktop() gate). jsdom's isDesktop() is false,
// which would hide nav-hive and break the expectedNavOrder assertion — force desktop.
vi.mock('../../services/tauri', async () => {
  const actual = await vi.importActual<typeof import('../../services/tauri')>('../../services/tauri');
  return { ...actual, isDesktop: () => true };
});

// Settings + OS Eval migrated to the OverlayHost registry (M3-tail) — render via
// overlaySurfaces (SettingsPage / EvalDashboard). Mock the heavy page content.
vi.mock('../../pages/SettingsPage', () => ({
  default: () => <div data-testid="settings-page" />,
}));
vi.mock('../../pages/EvalDashboard', () => ({
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

// Mock localStorage for testing
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
let originalInnerWidth: number;

// Mock Tauri window API
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({
    startDragging: vi.fn().mockResolvedValue(undefined),
  }),
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

// ============== Helper Functions ==============

/**
 * Simulates a window resize event
 */
function simulateWindowResize(width: number): void {
  Object.defineProperty(window, 'innerWidth', {
    value: width,
    writable: true,
    configurable: true,
  });
  
  // Dispatch resize event
  window.dispatchEvent(new Event('resize'));
}

/**
 * Verifies the layout structure is present and correctly ordered.
 *
 * A10 redesign (run_1aab916c): the WorkspaceExplorer is no longer an always-on
 * middle column — it opens on demand as the SwarmWS fullscreen overlay. So the
 * main flex row is now TWO columns: LeftSidebar (ASIDE) -> MainChatPanel (MAIN).
 * `explorerColumnPresent` verifies the OLD column is GONE from the main row
 * (should always be false now); the explorer lives in an overlay instead.
 */
function verifyLayoutStructure(): {
  hasLeftSidebar: boolean;
  explorerColumnPresent: boolean;
  hasMainChatPanel: boolean;
  isCorrectlyOrdered: boolean;
} {
  // Find the main layout container (flex row holding the columns)
  const layoutContainer = document.querySelector('.flex.flex-1.overflow-hidden');

  if (!layoutContainer) {
    return {
      hasLeftSidebar: false,
      explorerColumnPresent: false,
      hasMainChatPanel: false,
      isCorrectlyOrdered: false,
    };
  }

  const children = Array.from(layoutContainer.children);

  // Left Sidebar should be first child (aside element with fixed width)
  const leftSidebar = children[0] as HTMLElement;
  const hasLeftSidebar = leftSidebar?.tagName === 'ASIDE' &&
    leftSidebar.style.width === `${LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH}px`;

  // The old explorer column (a border-r DIV sibling) must NOT be in the main row.
  const explorerColumnPresent = children.some(
    (c) => c.tagName === 'DIV' && c.getAttribute('data-testid') === 'workspace-explorer',
  );

  // Main Chat Panel is now the SECOND child (main element with flex-1).
  const mainChatPanel = children.find((c) => c.tagName === 'MAIN') as HTMLElement | undefined;
  const hasMainChatPanel = mainChatPanel?.classList.contains('flex-1') ?? false;

  // Correct ordering (2-col): LeftSidebar -> MainChatPanel, no explorer column.
  const isCorrectlyOrdered = hasLeftSidebar && hasMainChatPanel && !explorerColumnPresent;

  return {
    hasLeftSidebar,
    explorerColumnPresent,
    hasMainChatPanel,
    isCorrectlyOrdered,
  };
}

/**
 * Helper to render component with proper cleanup for property tests
 */
function renderWithCleanup(children: React.ReactNode) {
  cleanup(); // Ensure clean state before render
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <ToastProvider>
            <ThreeColumnLayout>
              {children}
            </ThreeColumnLayout>
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>
    </ThemeProvider>
  );
}

// ============== Property-Based Tests ==============

describe('ThreeColumnLayout - Property-Based Tests', () => {
  beforeEach(() => {
    // Save original localStorage and replace with mock
    originalLocalStorage = window.localStorage;
    mockStorage = new MockLocalStorage();
    Object.defineProperty(window, 'localStorage', {
      value: mockStorage,
      writable: true,
      configurable: true,
    });

    // Save original innerWidth
    originalInnerWidth = window.innerWidth;
    
    // Set initial window width to a wide value
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

    // Restore original innerWidth
    Object.defineProperty(window, 'innerWidth', {
      value: originalInnerWidth,
      writable: true,
      configurable: true,
    });

    // Cleanup rendered components
    cleanup();
  });

  /**
   * Property 1: Layout Structure Maintained on Resize
   * **Feature: three-column-layout, Property 1: Layout Structure Maintained on Resize**
   * **Validates: Requirements 1.5**
   */
  describe('Feature: three-column-layout, Property 1: Layout Structure Maintained on Resize', () => {
    const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });
    const wideWindowWidthArb = fc.integer({ min: 769, max: 2000 });
    const narrowWindowWidthArb = fc.integer({ min: 320, max: 767 }); // < 768 triggers collapse

    it('should maintain three-column structure for any valid window width', () => {
      fc.assert(
        fc.property(validWindowWidthArb, (windowWidth) => {
          mockStorage.clear();
          
          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }  // Heavy render — 100 runs exceeds timeout under parallel execution
      );
    });

    it('should maintain layout structure after resize from any width to any other valid width', () => {
      fc.assert(
        fc.property(validWindowWidthArb, validWindowWidthArb, (initialWidth, targetWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: initialWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          act(() => {
            simulateWindowResize(targetWidth);
          });

          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should auto-collapse workspace explorer when width falls below 768px', () => {
      fc.assert(
        fc.property(narrowWindowWidthArb, (narrowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: 1200,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // A10 redesign: the explorer is no longer a column that collapses on
          // narrow width — it's an on-demand overlay. The invariant is now that
          // resizing to a narrow width keeps the stable 2-column layout with NO
          // explorer column at any width.
          let structure = verifyLayoutStructure();
          expect(structure.explorerColumnPresent).toBe(false);

          act(() => {
            simulateWindowResize(narrowWidth);
          });

          structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('keeps the 2-column layout (no explorer column) at any wide width', () => {
      fc.assert(
        fc.property(wideWindowWidthArb, (wideWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: wideWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // A10 redesign: explorer is overlay-only, never an always-on column.
          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should maintain structure through multiple consecutive resize events', () => {
      fc.assert(
        fc.property(
          fc.array(validWindowWidthArb, { minLength: 2, maxLength: 10 }),
          (resizeSequence) => {
            mockStorage.clear();

            Object.defineProperty(window, 'innerWidth', {
              value: resizeSequence[0],
              writable: true,
              configurable: true,
            });

            const { unmount } = renderWithCleanup(
              <div data-testid="chat-content">Chat Content</div>
            );

            for (const width of resizeSequence.slice(1)) {
              act(() => {
                simulateWindowResize(width);
              });
            }

            const structure = verifyLayoutStructure();
            expect(structure.hasLeftSidebar).toBe(true);
            expect(structure.explorerColumnPresent).toBe(false);
            expect(structure.hasMainChatPanel).toBe(true);
            expect(structure.isCorrectlyOrdered).toBe(true);

            // A10 redesign: no explorer column at any width, narrow or wide.

            unmount();
          }
        ),
        { numRuns: 30 }
      );
    });

    it('should preserve left sidebar fixed width regardless of window size', () => {
      fc.assert(
        fc.property(validWindowWidthArb, (windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const leftSidebar = document.querySelector('aside');
          expect(leftSidebar).not.toBeNull();
          expect(leftSidebar?.style.width).toBe(`${LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH}px`);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should ensure main chat panel fills remaining space', () => {
      fc.assert(
        fc.property(wideWindowWidthArb, (windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const mainPanel = document.querySelector('main.flex-1');
          expect(mainPanel).not.toBeNull();
          expect(mainPanel?.classList.contains('flex-1')).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should render children content in main chat panel', () => {
      fc.assert(
        fc.property(validWindowWidthArb, (windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const testContent = `test-content-${windowWidth}`;
          
          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">{testContent}</div>
          );

          const chatContent = screen.getByTestId('chat-content');
          expect(chatContent).not.toBeNull();
          expect(chatContent.textContent).toBe(testContent);

          const mainPanel = document.querySelector('main.flex-1');
          expect(mainPanel?.contains(chatContent)).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should handle rapid resize events without breaking layout', () => {
      fc.assert(
        fc.property(
          fc.array(validWindowWidthArb, { minLength: 5, maxLength: 20 }),
          (rapidResizes) => {
            mockStorage.clear();

            Object.defineProperty(window, 'innerWidth', {
              value: 1200,
              writable: true,
              configurable: true,
            });

            const { unmount } = renderWithCleanup(
              <div data-testid="chat-content">Chat Content</div>
            );

            act(() => {
              for (const width of rapidResizes) {
                simulateWindowResize(width);
              }
            });

            const structure = verifyLayoutStructure();
            expect(structure.hasLeftSidebar).toBe(true);
            expect(structure.explorerColumnPresent).toBe(false);
            expect(structure.hasMainChatPanel).toBe(true);
            expect(structure.isCorrectlyOrdered).toBe(true);

            unmount();
          }
        ),
        { numRuns: 30 }
      );
    });

    it('should maintain correct DOM order: sidebar -> main panel (explorer is overlay)', () => {
      fc.assert(
        fc.property(validWindowWidthArb, (windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const layoutContainer = document.querySelector('.flex.flex-1.overflow-hidden');
          const children = Array.from(layoutContainer?.children || []);

          // A10 redesign: 2-column main row — LeftSidebar (ASIDE) -> MainChatPanel (MAIN).
          // The explorer column DIV that used to sit between them is gone (overlay now).
          expect(children[0].tagName).toBe('ASIDE');
          const lastChild = children[children.length - 1];
          expect(lastChild.tagName).toBe('MAIN');
          // no explorer column in the main row
          const hasExplorerColumn = children.some(
            (c) => c.getAttribute('data-testid') === 'workspace-explorer',
          );
          expect(hasExplorerColumn).toBe(false);

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 4: Navigation Modal Opening
   * **Feature: three-column-layout, Property 4: Navigation Modal Opening**
   * **Validates: Requirements 2.2**
   *
   * For any navigation icon click in the Left_Sidebar, the corresponding modal
   * (Skills, MCP Servers, Agents, or Settings) SHALL open as an overlay while
   * preserving the underlying layout.
   */
  describe('Feature: three-column-layout, Property 4: Navigation Modal Opening', () => {
    // A10 redesign: the modal-opening domain cards are Settings (nav-settings ->
    // settings modal) and OS Eval (nav-eval -> eval modal). Each is an INDEPENDENT
    // single modal that toggles open/closed on click (no settings-tab machine —
    // Skills/MCP now live in the first-class Capabilities overlay (run_b5d98151),
    // which opens via swarm:show-capabilities like the other domain overlays).
    const navModalTypes = ['settings', 'eval'] as const;
    type NavModalType = typeof navModalTypes[number];

    const navToTestId: Record<NavModalType, string> = {
      settings: 'nav-settings',
      eval: 'nav-eval',
    };

    const navModalTypeArb = fc.constantFrom(...navModalTypes);
    const modalClickSequenceArb = fc.array(navModalTypeArb, { minLength: 1, maxLength: 10 });

    it('should open the corresponding modal when any navigation icon is clicked', () => {
      fc.assert(
        fc.property(navModalTypeArb, (modalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navToTestId[modalType]);
          expect(navButton).not.toBeNull();
          expect(navButton.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            navButton.click();
          });

          // Property: After clicking, the button SHALL indicate active state
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          // Property: The underlying layout SHALL be preserved
          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          // Property: Chat content SHALL still be visible (preserved)
          const chatContent = screen.getByTestId('chat-content');
          expect(chatContent).not.toBeNull();
          expect(chatContent.textContent).toBe('Chat Content');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should preserve layout structure when opening any modal at any window width', () => {
      const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

      fc.assert(
        fc.property(navModalTypeArb, validWindowWidthArb, (modalType, windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navToTestId[modalType]);
          act(() => {
            navButton.click();
          });

          // Property: Layout structure SHALL be preserved regardless of window width
          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should handle clicking different navigation icons in sequence', () => {
      fc.assert(
        fc.property(modalClickSequenceArb, (clickSequence) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // A10: settings + eval are independent single modals. Only ONE
          // activeModal slot exists, so opening one closes the other. Model it:
          let activeModal: NavModalType | null = null;

          for (const modalType of clickSequence) {
            const navButton = screen.getByTestId(navToTestId[modalType]);
            act(() => {
              navButton.click();
            });

            // Toggle: clicking the open one closes it; clicking another switches.
            activeModal = activeModal === modalType ? null : modalType;

            // Property: button active state matches modeled state
            expect(navButton.getAttribute('aria-pressed')).toBe(String(activeModal === modalType));

            // Property: Layout SHALL be preserved after each click
            const structure = verifyLayoutStructure();
            expect(structure.hasLeftSidebar).toBe(true);
            expect(structure.explorerColumnPresent).toBe(false);
            expect(structure.hasMainChatPanel).toBe(true);
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should switch active modal when clicking a different navigation icon', () => {
      // A10: settings + eval are independent single modals sharing one activeModal
      // slot — opening the second closes the first (a true switch).
      const twoDistinctArb = fc.tuple(navModalTypeArb, navModalTypeArb)
        .filter(([first, second]) => first !== second);

      fc.assert(
        fc.property(twoDistinctArb, ([firstModal, secondModal]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const firstButton = screen.getByTestId(navToTestId[firstModal]);
          act(() => {
            firstButton.click();
          });

          expect(firstButton.getAttribute('aria-pressed')).toBe('true');

          const secondButton = screen.getByTestId(navToTestId[secondModal]);
          act(() => {
            secondButton.click();
          });

          // Property: Second button SHALL now be active
          expect(secondButton.getAttribute('aria-pressed')).toBe('true');

          // Property: First button SHALL no longer be active
          expect(firstButton.getAttribute('aria-pressed')).toBe('false');

          // Property: Layout SHALL be preserved
          const structure = verifyLayoutStructure();
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should keep left sidebar visible and accessible when modal is open', () => {
      fc.assert(
        fc.property(navModalTypeArb, (modalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navToTestId[modalType]);
          act(() => {
            navButton.click();
          });

          // Property: Left sidebar SHALL remain visible (Requirement 2.3)
          const leftSidebar = screen.getByTestId('left-sidebar');
          expect(leftSidebar).not.toBeNull();

          // Property: All navigation icons SHALL remain accessible
          for (const otherModalType of navModalTypes) {
            const otherButton = screen.getByTestId(navToTestId[otherModalType]);
            expect(otherButton).not.toBeNull();
            expect(otherButton.hasAttribute('disabled')).toBe(false);
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 5: Active Navigation Indicator
   * **Feature: three-column-layout, Property 5: Active Navigation Indicator**
   * **Validates: Requirements 2.5**
   *
   * For any active modal state, the corresponding navigation icon in the
   * Left_Sidebar SHALL display the active visual indicator (highlighted state).
   */
  describe('Feature: three-column-layout, Property 5: Active Navigation Indicator', () => {
    // A10: the modal-opening domain cards are Settings + OS Eval — independent
    // single modals sharing one activeModal slot. The active card is exactly the
    // one whose modal is open.
    const navItems = ['settings', 'eval'] as const;
    type NavItem = typeof navItems[number];

    const navToTestId: Record<NavItem, string> = {
      settings: 'nav-settings',
      eval: 'nav-eval',
    };

    const navItemArb = fc.constantFrom(...navItems);

    it('should display active visual indicator on the corresponding nav icon when clicked', () => {
      fc.assert(
        fc.property(navItemArb, (activeItem) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(navToTestId[activeItem]);
          act(() => {
            activeButton.click();
          });

          // Property: Clicked button SHALL have aria-pressed="true"
          expect(activeButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Active button SHALL carry the .a10-card class (active
          // bg+ring are CSS-driven via .a10-card--active + the --ac group color).
          // The behavioral contract is aria-pressed + .a10-card.
          expect(activeButton.classList.contains('a10-card')).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should only show active indicator on the clicked nav icon', () => {
      fc.assert(
        fc.property(navItemArb, (clickedItem) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(navToTestId[clickedItem]);
          act(() => {
            activeButton.click();
          });

          // Skills/MCP/Settings all open the Settings modal.
          // Skills and MCP set a specific tab; Settings uses default (undefined).
          // Active indicator: Skills/MCP track by settingsTab, Settings button
          // is active when modal is open AND settingsTab is undefined (i.e.,
          // user clicked Settings directly, not Skills/MCP).
          for (const item of navItems) {
            const button = screen.getByTestId(navToTestId[item]);
            if (item === clickedItem) {
              expect(button.getAttribute('aria-pressed')).toBe('true');
            } else {
              // Other nav items should NOT be active
              // (Skills/MCP have different settingsTab values; Settings button
              // isActive only when settingsTab is undefined)
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should update active indicator when switching between nav items', () => {
      const navSequenceArb = fc.array(navItemArb, { minLength: 2, maxLength: 8 });

      fc.assert(
        fc.property(navSequenceArb, (navSequence) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Track state to predict toggle behavior
          // Settings button: closes if modal already open (any tab)
          // Skills/MCP: closes if same tab active, else switches tab
          // A10: settings + eval are independent single modals sharing one
          // activeModal slot. Toggle: click the open one → close; click another → switch.
          let activeModal: NavItem | null = null;

          for (let i = 0; i < navSequence.length; i++) {
            const currentItem = navSequence[i];
            const currentButton = screen.getByTestId(navToTestId[currentItem]);

            act(() => {
              currentButton.click();
            });

            activeModal = activeModal === currentItem ? null : currentItem;

            // Property: Button active state matches predicted state
            expect(currentButton.getAttribute('aria-pressed')).toBe(String(activeModal === currentItem));

            // Property: All other buttons SHALL NOT have active indicator
            for (const otherItem of navItems) {
              if (otherItem !== currentItem) {
                const otherButton = screen.getByTestId(navToTestId[otherItem]);
                expect(otherButton.getAttribute('aria-pressed')).toBe('false');
              }
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should show no active indicator when no modal is open initially', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Property: Initially, no navigation icon SHALL have active indicator
          for (const item of navItems) {
            const button = screen.getByTestId(navToTestId[item]);
            expect(button.getAttribute('aria-pressed')).toBe('false');
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should maintain active indicator through window resize events', () => {
      const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

      fc.assert(
        fc.property(navItemArb, validWindowWidthArb, (activeItem, newWidth) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(navToTestId[activeItem]);
          act(() => {
            activeButton.click();
          });

          act(() => {
            simulateWindowResize(newWidth);
          });

          // Property: Active indicator SHALL be maintained after resize
          expect(activeButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Other buttons SHALL remain inactive
          for (const item of navItems) {
            if (item !== activeItem) {
              const button = screen.getByTestId(navToTestId[item]);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should correctly reflect active state in aria-pressed attribute for accessibility', () => {
      fc.assert(
        fc.property(navItemArb, (clickedItem) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const button = screen.getByTestId(navToTestId[clickedItem]);

          // Property: Before click, aria-pressed SHALL be "false"
          expect(button.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            button.click();
          });

          // Property: After click, aria-pressed SHALL be "true"
          expect(button.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should have consistent visual styling for active vs inactive states', () => {
      fc.assert(
        fc.property(navItemArb, (activeItem) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(navToTestId[activeItem]);
          act(() => {
            activeButton.click();
          });

          // Property: Active button SHALL have distinct styling from inactive buttons
          for (const item of navItems) {
            const button = screen.getByTestId(navToTestId[item]);
            const buttonClasses = button.className;

            if (item === activeItem) {
              // A10: active styling is CSS-driven via .a10-card--active (bg+ring
              // from --ac). Assert the base class + active class + pressed state.
              expect(button.classList.contains('a10-card')).toBe(true);
              expect(button.classList.contains('a10-card--active')).toBe(true);
              expect(button.getAttribute('aria-pressed')).toBe('true');
            } else {
              // Inactive: same .a10-card, no active class; distinguisher is aria-pressed.
              expect(button.classList.contains('a10-card')).toBe(true);
              expect(button.classList.contains('a10-card--active')).toBe(false);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 1: Navigation Item Order Consistency (left-navigation-redesign)
   * **Feature: left-navigation-redesign, Property 1: Navigation Item Order Consistency**
   * **Validates: Requirements 1.1**
   *
   * For any render of the LeftSidebar component, the navigation items SHALL appear
   * in exactly this order: Skills, MCP Servers, with no items missing or duplicated.
   */
  describe('Feature: left-navigation-redesign, Property 1: Navigation Item Order Consistency', () => {
    // Exact DOM order of the DOMAIN cards (excludes nav-new-brain, a distinct
    // .a10-newbrain create-affordance). Cognition (Context/Memory/Brain Hub) →
    // Work (ToDo/Workspace/Pipeline/Pollinate — daily-common pair first, A4) →
    // System. "Workspace" is the de-jargoned label for the SwarmWS card.
    // Terminal + GitHub live in the footer, OUTSIDE the nav container.
    const expectedNavOrder = [
      { testId: 'nav-context', label: 'C&M' },
      { testId: 'nav-library', label: 'Library' },
      { testId: 'nav-brain-hub', label: 'Brain Hub' },
      { testId: 'nav-todo', label: 'ToDo' },
      { testId: 'nav-swarmws', label: 'Workspace' },
      // NOTE: no nav-canvas — Canvas is output-triggered, not a nav card (run_990b0a03).
      { testId: 'nav-pipeline', label: 'Pipeline' },
      { testId: 'nav-pollinate', label: 'Pollinate' },
      { testId: 'nav-capabilities', label: 'Capabilities' },
      { testId: 'nav-jobs', label: 'Jobs & Runs' },
      { testId: 'nav-hive', label: 'Hive' },
      { testId: 'nav-eval', label: 'OS Eval' },
      { testId: 'nav-settings', label: 'Settings' },
      { testId: 'nav-community', label: 'Community' },
    ] as const;

    const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

    /**
     * Verifies navigation items appear in the correct order in the DOM
     */
    function verifyNavigationOrder(): {
      allItemsPresent: boolean;
      correctOrder: boolean;
      noDuplicates: boolean;
      foundItems: string[];
    } {
      const navContainer = document.querySelector('[data-testid="nav-icons"]');
      if (!navContainer) {
        return {
          allItemsPresent: false,
          correctOrder: false,
          noDuplicates: false,
          foundItems: [],
        };
      }

      // Get all DOMAIN cards in DOM order. nav-new-brain matches the nav-* prefix
      // but is a "+ New Brain" create-affordance (.a10-newbrain), NOT a domain
      // card — exclude it so the order aligns with expectedNavOrder (domain cards
      // only). (Repairs pre-existing drift: a prior session added new-brain +
      // nav-todo without syncing this selector.)
      const navButtons = navContainer.querySelectorAll('button[data-testid^="nav-"]:not([data-testid="nav-new-brain"])');
      const foundTestIds = Array.from(navButtons).map(btn => btn.getAttribute('data-testid'));

      // Check all expected items are present
      const expectedTestIds = expectedNavOrder.map(item => item.testId);
      const allItemsPresent = expectedTestIds.every(testId => foundTestIds.includes(testId));

      // Check correct order - items should appear in exact expected sequence
      const correctOrder = expectedTestIds.every((testId, index) => foundTestIds[index] === testId);

      // Check no duplicates
      const uniqueTestIds = new Set(foundTestIds);
      const noDuplicates = uniqueTestIds.size === foundTestIds.length;

      return {
        allItemsPresent,
        correctOrder,
        noDuplicates,
        foundItems: foundTestIds.filter((id): id is string => id !== null),
      };
    }

    it('should display all navigation items in exact order for any render', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const result = verifyNavigationOrder();

          // Property: All modal navigation items SHALL be present
          expect(result.allItemsPresent).toBe(true);

          // Property: Modal nav items SHALL appear in exact order: Skills, MCP Servers
          expect(result.correctOrder).toBe(true);

          // Property: No items SHALL be duplicated
          expect(result.noDuplicates).toBe(true);

          // Property: At least 2 modal nav items SHALL be present
          expect(result.foundItems.length).toBeGreaterThanOrEqual(2);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should maintain navigation order at any window width', () => {
      fc.assert(
        fc.property(validWindowWidthArb, (windowWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const result = verifyNavigationOrder();

          // Property: Navigation order SHALL be maintained regardless of window width
          expect(result.allItemsPresent).toBe(true);
          expect(result.correctOrder).toBe(true);
          expect(result.noDuplicates).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should maintain navigation order after window resize events', () => {
      fc.assert(
        fc.property(validWindowWidthArb, validWindowWidthArb, (initialWidth, targetWidth) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: initialWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Verify initial order
          let result = verifyNavigationOrder();
          expect(result.correctOrder).toBe(true);

          // Resize window
          act(() => {
            simulateWindowResize(targetWidth);
          });

          // Property: Navigation order SHALL be preserved after resize
          result = verifyNavigationOrder();
          expect(result.allItemsPresent).toBe(true);
          expect(result.correctOrder).toBe(true);
          expect(result.noDuplicates).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should maintain navigation order through multiple resize events', () => {
      fc.assert(
        fc.property(
          fc.array(validWindowWidthArb, { minLength: 2, maxLength: 10 }),
          (resizeSequence) => {
            mockStorage.clear();

            Object.defineProperty(window, 'innerWidth', {
              value: resizeSequence[0],
              writable: true,
              configurable: true,
            });

            const { unmount } = renderWithCleanup(
              <div data-testid="chat-content">Chat Content</div>
            );

            // Apply resize sequence
            for (const width of resizeSequence.slice(1)) {
              act(() => {
                simulateWindowResize(width);
              });
            }

            // Property: Navigation order SHALL be maintained through any resize sequence
            const result = verifyNavigationOrder();
            expect(result.allItemsPresent).toBe(true);
            expect(result.correctOrder).toBe(true);
            expect(result.noDuplicates).toBe(true);

            unmount();
          }
        ),
        { numRuns: 30 }
      );
    });

    it('should maintain navigation order when modals are opened', () => {
      const navModalTypes = ['settings', 'eval'] as const;
      const navModalTypeArb = fc.constantFrom(...navModalTypes);

      fc.assert(
        fc.property(navModalTypeArb, (modalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Click a navigation item to open modal
          const navButton = screen.getByTestId(`nav-${modalType}`);
          act(() => {
            navButton.click();
          });

          // Property: Navigation order SHALL be maintained when any modal is open
          const result = verifyNavigationOrder();
          expect(result.allItemsPresent).toBe(true);
          expect(result.correctOrder).toBe(true);
          expect(result.noDuplicates).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should have each navigation item accessible by its test ID', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Property: Each navigation item SHALL be accessible by its designated test ID
          for (const item of expectedNavOrder) {
            const button = screen.getByTestId(item.testId);
            expect(button).not.toBeNull();
            expect(button.tagName).toBe('BUTTON');
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('should have navigation items with correct title attributes', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Property: Each navigation item SHALL have the correct title/label
          for (const item of expectedNavOrder) {
            const button = screen.getByTestId(item.testId);
            expect(button.getAttribute('title')).toBe(item.label);
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 2: Navigation Click Opens Corresponding Modal (left-navigation-redesign)
   * **Feature: left-navigation-redesign, Property 2: Navigation Click Opens Corresponding Modal**
   * **Validates: Requirements 2.3, 4.1, 5.1**
   *
   * For any navigation item in the navItems array, clicking that item SHALL result
   * in the activeModal state being set to that item's modalType value.
   */
  describe('Feature: left-navigation-redesign, Property 2: Navigation Click Opens Corresponding Modal', () => {
    // All navigation items (v1 navbar)
    const allNavItems = [
      { testId: 'nav-settings', modalType: 'settings', label: 'Settings' },
      { testId: 'nav-eval', modalType: 'eval', label: 'OS Eval' },
    ] as const;

    type NavItemType = typeof allNavItems[number];

    const navItemArb = fc.constantFrom(...allNavItems);
    const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * For any navigation item, clicking it SHALL set activeModal to that item's modalType.
     */
    it('should open corresponding modal when any navigation item is clicked', () => {
      fc.assert(
        fc.property(navItemArb, (navItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Find the navigation button
          const navButton = screen.getByTestId(navItem.testId);
          expect(navButton).not.toBeNull();

          // Property: Before click, button SHALL NOT be active
          expect(navButton.getAttribute('aria-pressed')).toBe('false');

          // Click the navigation item
          act(() => {
            navButton.click();
          });

          // Property: After click, activeModal SHALL be set to the item's modalType
          // This is verified by the aria-pressed attribute being "true"
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Clicking a navigation item SHALL result in only that item being active.
     */
    it('should set only the clicked navigation item as active', () => {
      fc.assert(
        fc.property(navItemArb, (clickedItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const clickedButton = screen.getByTestId(clickedItem.testId);
          act(() => {
            clickedButton.click();
          });

          // Property: Only the clicked item SHALL be active
          for (const item of allNavItems) {
            const button = screen.getByTestId(item.testId);
            if (item.testId === clickedItem.testId) {
              expect(button.getAttribute('aria-pressed')).toBe('true');
            } else {
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Navigation click behavior SHALL work at any valid window width.
     */
    it('should open corresponding modal at any window width', () => {
      fc.assert(
        fc.property(navItemArb, validWindowWidthArb, (navItem: NavItemType, windowWidth: number) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);
          act(() => {
            navButton.click();
          });

          // Property: Modal SHALL open regardless of window width
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Clicking a different navigation item SHALL switch the active modal.
     */
    it('should switch active modal when clicking different navigation items', () => {
      const twoDistinctItemsArb = fc.tuple(navItemArb, navItemArb)
        .filter(([first, second]) => first.testId !== second.testId);

      fc.assert(
        fc.property(twoDistinctItemsArb, ([firstItem, secondItem]: [NavItemType, NavItemType]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Click first item
          const firstButton = screen.getByTestId(firstItem.testId);
          act(() => {
            firstButton.click();
          });

          // Property: First item SHALL be active
          expect(firstButton.getAttribute('aria-pressed')).toBe('true');

          // Click second item
          const secondButton = screen.getByTestId(secondItem.testId);
          act(() => {
            secondButton.click();
          });

          // Property: Second item SHALL now be active
          expect(secondButton.getAttribute('aria-pressed')).toBe('true');

          // Property: First item SHALL no longer be active
          expect(firstButton.getAttribute('aria-pressed')).toBe('false');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Navigation click behavior SHALL work through any sequence of clicks.
     * Toggle behavior: clicking the same item twice closes it (aria-pressed=false).
     */
    it('should correctly handle any sequence of navigation clicks', () => {
      const clickSequenceArb = fc.array(navItemArb, { minLength: 1, maxLength: 10 });

      fc.assert(
        fc.property(clickSequenceArb, (clickSequence: NavItemType[]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          let lastClickedId: string | null = null;

          for (const item of clickSequence) {
            const button = screen.getByTestId(item.testId);
            act(() => {
              button.click();
            });

            if (item.testId === lastClickedId) {
              // Toggle: clicking same item again closes it
              expect(button.getAttribute('aria-pressed')).toBe('false');
              lastClickedId = null;
            } else {
              // New item: SHALL be active
              expect(button.getAttribute('aria-pressed')).toBe('true');
              lastClickedId = item.testId;
            }

            // Property: All other items SHALL be inactive
            for (const otherItem of allNavItems) {
              if (otherItem.testId !== item.testId) {
                const otherButton = screen.getByTestId(otherItem.testId);
                expect(otherButton.getAttribute('aria-pressed')).toBe('false');
              }
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Specifically test the new workspaces navigation item.
     */
    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Navigation click SHALL preserve the underlying layout structure.
     */
    it('should preserve layout structure when opening any modal', () => {
      fc.assert(
        fc.property(navItemArb, (navItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);
          act(() => {
            navButton.click();
          });

          // Property: Layout structure SHALL be preserved
          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.explorerColumnPresent).toBe(false);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          // Property: Chat content SHALL still be accessible
          const chatContent = screen.getByTestId('chat-content');
          expect(chatContent).not.toBeNull();

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * All navigation items SHALL remain accessible when any modal is open.
     */
    it('should keep all navigation items accessible when modal is open', () => {
      fc.assert(
        fc.property(navItemArb, (activeItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Open a modal
          const activeButton = screen.getByTestId(activeItem.testId);
          act(() => {
            activeButton.click();
          });

          // Property: All navigation items SHALL remain accessible
          for (const item of allNavItems) {
            const button = screen.getByTestId(item.testId);
            expect(button).not.toBeNull();
            expect(button.hasAttribute('disabled')).toBe(false);
          }

          // Property: Left sidebar SHALL remain visible
          const leftSidebar = screen.getByTestId('left-sidebar');
          expect(leftSidebar).not.toBeNull();

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 5: Active State Reflects Open Modal (left-navigation-redesign)
   * **Feature: left-navigation-redesign, Property 5: Active State Reflects Open Modal**
   * **Validates: Requirements 8.1, 8.4**
   *
   * For any navigation item, that item SHALL display the active visual state if and only if
   * activeModal equals that item's modalType. When activeModal is null, no navigation item
   * SHALL display the active state.
   */
  describe('Feature: left-navigation-redesign, Property 5: Active State Reflects Open Modal', () => {
    // v1 navigation items
    const allNavItems = [
      { testId: 'nav-settings', modalType: 'settings', label: 'Settings' },
      { testId: 'nav-eval', modalType: 'eval', label: 'OS Eval' },
    ] as const;

    type NavItemType = typeof allNavItems[number];

    const navItemArb = fc.constantFrom(...allNavItems);
    const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * When no modal is open (activeModal is null), no navigation item SHALL display active state.
     */
    it('should show no active indicator when no modal is open', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Property: When activeModal is null, NO navigation item SHALL display active state
          for (const item of allNavItems) {
            const button = screen.getByTestId(item.testId);
            expect(button.getAttribute('aria-pressed')).toBe('false');
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * For any navigation item, clicking it SHALL result in that item displaying active state.
     */
    it('should display active state on navigation item when its modal is open', () => {
      fc.assert(
        fc.property(navItemArb, (navItem: NavItemType) => {
          mockStorage.clear();

          // Scope to this render's container (global screen queries can match an
          // orphaned tree left by a heavy prior fast-check block — test-infra
          // isolation, not a component bug).
          const { unmount, container } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = container.querySelector<HTMLElement>(`[data-testid="${navItem.testId}"]`)!;

          // Click to open modal
          act(() => {
            navButton.click();
          });

          // Property: The navigation item SHALL display active visual state
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Active card carries the .a10-card class — its active
          // bg+ring are CSS-driven via .a10-card--active + --ac (A10 redesign).
          expect(navButton.classList.contains('a10-card')).toBe(true);

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * For any navigation item, that item SHALL display active state if and only if
     * activeModal equals that item's modalType.
     */
    it('should display active state only on the navigation item whose modal is open', () => {
      fc.assert(
        fc.property(navItemArb, (activeItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Open the modal for activeItem
          const activeButton = screen.getByTestId(activeItem.testId);
          act(() => {
            activeButton.click();
          });

          // Property: Only the active item SHALL display active state
          for (const item of allNavItems) {
            const button = screen.getByTestId(item.testId);
            if (item.testId === activeItem.testId) {
              expect(button.getAttribute('aria-pressed')).toBe('true');
            } else {
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL correctly update when switching between modals.
     */
    it('should update active state when switching between different modals', () => {
      const twoDistinctItemsArb = fc.tuple(navItemArb, navItemArb)
        .filter(([first, second]) => first.testId !== second.testId);

      fc.assert(
        fc.property(twoDistinctItemsArb, ([firstItem, secondItem]: [NavItemType, NavItemType]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Open first modal
          const firstButton = screen.getByTestId(firstItem.testId);
          act(() => {
            firstButton.click();
          });

          // Property: First item SHALL be active
          expect(firstButton.getAttribute('aria-pressed')).toBe('true');

          // Open second modal
          const secondButton = screen.getByTestId(secondItem.testId);
          act(() => {
            secondButton.click();
          });

          // Property: Second item SHALL now be active
          expect(secondButton.getAttribute('aria-pressed')).toBe('true');

          // Property: First item SHALL no longer be active
          expect(firstButton.getAttribute('aria-pressed')).toBe('false');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL be maintained at any window width.
     */
    it('should maintain active state at any window width', () => {
      fc.assert(
        fc.property(navItemArb, validWindowWidthArb, (navItem: NavItemType, windowWidth: number) => {
          mockStorage.clear();

          Object.defineProperty(window, 'innerWidth', {
            value: windowWidth,
            writable: true,
            configurable: true,
          });

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);
          act(() => {
            navButton.click();
          });

          // Property: Active state SHALL be displayed regardless of window width
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Other items SHALL remain inactive
          for (const item of allNavItems) {
            if (item.testId !== navItem.testId) {
              const button = screen.getByTestId(item.testId);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL be preserved through window resize events.
     */
    it('should preserve active state through window resize events', () => {
      fc.assert(
        fc.property(navItemArb, validWindowWidthArb, (navItem: NavItemType, newWidth: number) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);
          act(() => {
            navButton.click();
          });

          // Resize window
          act(() => {
            simulateWindowResize(newWidth);
          });

          // Property: Active state SHALL be maintained after resize
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Other items SHALL remain inactive after resize
          for (const item of allNavItems) {
            if (item.testId !== navItem.testId) {
              const button = screen.getByTestId(item.testId);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL correctly track through any sequence of modal switches.
     * Toggle behavior: clicking the same modal item twice closes it.
     */
    it('should correctly track active state through any sequence of modal switches', () => {
      const modalSequenceArb = fc.array(navItemArb, { minLength: 2, maxLength: 10 });

      fc.assert(
        fc.property(modalSequenceArb, (modalSequence: NavItemType[]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          let lastClickedId: string | null = null;

          for (const currentItem of modalSequence) {
            const currentButton = screen.getByTestId(currentItem.testId);

            act(() => {
              currentButton.click();
            });

            if (currentItem.testId === lastClickedId) {
              // Toggle: same item clicked again → modal closes, no active state
              expect(currentButton.getAttribute('aria-pressed')).toBe('false');
              lastClickedId = null;
            } else {
              // Property: Current item SHALL have active state
              expect(currentButton.getAttribute('aria-pressed')).toBe('true');
              lastClickedId = currentItem.testId;
            }

            // Property: All other items SHALL NOT have active state
            for (const otherItem of allNavItems) {
              if (otherItem.testId !== currentItem.testId) {
                const otherButton = screen.getByTestId(otherItem.testId);
                expect(otherButton.getAttribute('aria-pressed')).toBe('false');
              }
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL have correct visual styling (highlighted background and ring border).
     */
    it('should have correct visual styling for active state', () => {
      fc.assert(
        fc.property(navItemArb, (navItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);
          act(() => {
            navButton.click();
          });

          // Property: Active card carries .a10-card + .a10-card--active + aria-pressed=true.
          // Highlighted bg + ring are CSS-driven via .a10-card--active reading --ac (A10).
          expect(navButton.classList.contains('a10-card')).toBe(true);
          expect(navButton.classList.contains('a10-card--active')).toBe(true);
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Inactive navigation items SHALL NOT display active visual state.
     */
    it('should not display active visual state on inactive navigation items', () => {
      fc.assert(
        fc.property(navItemArb, (activeItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          // Open modal for activeItem
          const activeButton = screen.getByTestId(activeItem.testId);
          act(() => {
            activeButton.click();
          });

          // Property: Inactive items SHALL NOT have active visual state
          for (const item of allNavItems) {
            if (item.testId !== activeItem.testId) {
              const button = screen.getByTestId(item.testId);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL be correctly reflected in aria-pressed attribute for accessibility.
     */
    it('should correctly reflect active state in aria-pressed attribute', () => {
      fc.assert(
        fc.property(navItemArb, (navItem: NavItemType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);

          // Property: Before click, aria-pressed SHALL be "false"
          expect(navButton.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            navButton.click();
          });

          // Property: After click, aria-pressed SHALL be "true"
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });


  /**
   * Property 6: A10 active-card indicator (leftnav-redesign)
   * **Feature: leftnav-redesign, Property 6: Active Card**
   * **Validates: AC1**
   *
   * A10 replaced the icon-rail's opacity-toggled active-bar span with the
   * `.a10-card--active` state class (bg+ring driven by --ac). The modal-opening
   * cards (Settings, OS Eval) gain `.a10-card--active` only while their modal is
   * open; clicking one flips exactly its state.
   */
  describe('Feature: leftnav-redesign, Property 6: Active Card', () => {
    const navTestIds = ['nav-settings', 'nav-eval'] as const;
    const navTestIdArb = fc.constantFrom(...navTestIds);

    it('renders every domain card as an .a10-card (present in all states)', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navContainer = document.querySelector('[data-testid="nav-icons"]');
          // Exclude nav-new-brain: a .a10-newbrain create-affordance, not a
          // domain .a10-card (matches the nav-* prefix but is a distinct control).
          const navButtons = navContainer?.querySelectorAll('button[data-testid^="nav-"]:not([data-testid="nav-new-brain"])') ?? [];
          expect(navButtons.length).toBeGreaterThanOrEqual(2);
          for (const btn of Array.from(navButtons)) {
            expect(btn.classList.contains('a10-card')).toBe(true);
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });

    it('adds .a10-card--active only to the active card (its modal open), none otherwise', () => {
      fc.assert(
        fc.property(navTestIdArb, (clickedTestId) => {
          mockStorage.clear();

          // Scope queries to THIS render's container (getByTestId on `screen`
          // searches all of document.body — a heavy prior fast-check block can
          // leave an orphaned tree that global queries then match, so we bind to
          // the fresh container instead).
          const { unmount, container } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );
          const q = (id: string) => container.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;

          const clickedButton = q(clickedTestId);
          act(() => {
            clickedButton.click();
          });

          // Active card carries the active state class
          expect(clickedButton.classList.contains('a10-card--active')).toBe(true);

          // The other modal-opening card does NOT (single activeModal slot)
          for (const testId of navTestIds) {
            if (testId === clickedTestId) continue;
            const otherButton = q(testId);
            expect(otherButton.classList.contains('a10-card--active')).toBe(false);
          }

          unmount();
        }),
        { numRuns: 30 }
      );
    });
  });
});
