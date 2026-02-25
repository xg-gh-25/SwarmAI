import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, act, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
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

// Mock modal components to prevent HTTP requests from their internal pages
vi.mock('../modals/WorkspacesModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => 
    isOpen ? <div data-testid="workspaces-modal"><button onClick={onClose}>Close</button></div> : null,
}));

vi.mock('../modals/SwarmCoreModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => 
    isOpen ? <div data-testid="swarmcore-modal"><button onClick={onClose}>Close</button></div> : null,
}));

vi.mock('../modals/SkillsModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => 
    isOpen ? <div data-testid="skills-modal"><button onClick={onClose}>Close</button></div> : null,
}));

vi.mock('../modals/MCPServersModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => 
    isOpen ? <div data-testid="mcp-modal"><button onClick={onClose}>Close</button></div> : null,
}));

vi.mock('../modals/AgentsModal', () => ({
  default: ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => 
    isOpen ? <div data-testid="agents-modal"><button onClick={onClose}>Close</button></div> : null,
}));

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

vi.mock('../../services/mcp', () => ({
  mcpService: {
    list: vi.fn().mockResolvedValue([]),
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
 * Verifies the three-column layout structure is present and correctly ordered
 */
function verifyLayoutStructure(): {
  hasLeftSidebar: boolean;
  hasWorkspaceExplorer: boolean;
  hasMainChatPanel: boolean;
  isCorrectlyOrdered: boolean;
  explorerIsCollapsed: boolean;
} {
  // Find the main layout container (flex container with the three columns)
  const layoutContainer = document.querySelector('.flex.flex-1.overflow-hidden');
  
  if (!layoutContainer) {
    return {
      hasLeftSidebar: false,
      hasWorkspaceExplorer: false,
      hasMainChatPanel: false,
      isCorrectlyOrdered: false,
      explorerIsCollapsed: false,
    };
  }

  const children = Array.from(layoutContainer.children);
  
  // Left Sidebar should be first child (aside element with fixed width)
  const leftSidebar = children[0] as HTMLElement;
  const hasLeftSidebar = leftSidebar?.tagName === 'ASIDE' && 
    leftSidebar.style.width === `${LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH}px`;

  // Workspace Explorer should be second child (div with border-r)
  const workspaceExplorer = children[1] as HTMLElement;
  const hasWorkspaceExplorer = workspaceExplorer?.tagName === 'DIV' && 
    workspaceExplorer.classList.contains('border-r');

  // Check if explorer is collapsed (narrow width with expand button)
  const explorerIsCollapsed = workspaceExplorer?.querySelector('button[title*="Expand"]') !== null ||
    workspaceExplorer?.querySelector('button[title*="expand"]') !== null;

  // Main Chat Panel should be third child (main element with flex-1)
  const mainChatPanel = children[2] as HTMLElement;
  const hasMainChatPanel = mainChatPanel?.tagName === 'MAIN' && 
    mainChatPanel.classList.contains('flex-1');

  // Verify correct ordering: LeftSidebar -> WorkspaceExplorer -> MainChatPanel
  const isCorrectlyOrdered = hasLeftSidebar && hasWorkspaceExplorer && hasMainChatPanel;

  return {
    hasLeftSidebar,
    hasWorkspaceExplorer,
    hasMainChatPanel,
    isCorrectlyOrdered,
    explorerIsCollapsed,
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
          <ThreeColumnLayout>
            {children}
          </ThreeColumnLayout>
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
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
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
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
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

          let structure = verifyLayoutStructure();
          expect(structure.explorerIsCollapsed).toBe(false);

          act(() => {
            simulateWindowResize(narrowWidth);
          });

          structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);
          expect(structure.explorerIsCollapsed).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should keep workspace explorer expanded when width is above 768px', () => {
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

          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);
          expect(structure.explorerIsCollapsed).toBe(false);

          unmount();
        }),
        { numRuns: 100 }
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
            expect(structure.hasWorkspaceExplorer).toBe(true);
            expect(structure.hasMainChatPanel).toBe(true);
            expect(structure.isCorrectlyOrdered).toBe(true);

            const finalWidth = resizeSequence[resizeSequence.length - 1];
            if (finalWidth < LAYOUT_CONSTANTS.NARROW_VIEWPORT_BREAKPOINT) {
              expect(structure.explorerIsCollapsed).toBe(true);
            }

            unmount();
          }
        ),
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
            expect(structure.hasWorkspaceExplorer).toBe(true);
            expect(structure.hasMainChatPanel).toBe(true);
            expect(structure.isCorrectlyOrdered).toBe(true);

            unmount();
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should maintain correct DOM order: sidebar -> explorer -> main panel', () => {
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

          expect(children.length).toBe(3);
          expect(children[0].tagName).toBe('ASIDE');
          expect(children[1].tagName).toBe('DIV');
          expect(children[2].tagName).toBe('MAIN');

          unmount();
        }),
        { numRuns: 100 }
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
    const navModalTypes = ['skills', 'mcp', 'agents', 'settings'] as const;
    type NavModalType = typeof navModalTypes[number];

    const modalToNavTestId: Record<NavModalType, string> = {
      skills: 'nav-skills',
      mcp: 'nav-mcp',
      agents: 'nav-agents',
      settings: 'nav-settings',
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

          const navButton = screen.getByTestId(modalToNavTestId[modalType]);
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
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          // Property: Chat content SHALL still be visible (preserved)
          const chatContent = screen.getByTestId('chat-content');
          expect(chatContent).not.toBeNull();
          expect(chatContent.textContent).toBe('Chat Content');

          unmount();
        }),
        { numRuns: 100 }
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

          const navButton = screen.getByTestId(modalToNavTestId[modalType]);
          act(() => {
            navButton.click();
          });

          // Property: Layout structure SHALL be preserved regardless of window width
          const structure = verifyLayoutStructure();
          expect(structure.hasLeftSidebar).toBe(true);
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should handle clicking different navigation icons in sequence', () => {
      fc.assert(
        fc.property(modalClickSequenceArb, (clickSequence) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          for (const modalType of clickSequence) {
            const navButton = screen.getByTestId(modalToNavTestId[modalType]);
            act(() => {
              navButton.click();
            });

            // Property: After each click, the clicked button SHALL be active
            expect(navButton.getAttribute('aria-pressed')).toBe('true');

            // Property: Layout SHALL be preserved after each click
            const structure = verifyLayoutStructure();
            expect(structure.hasLeftSidebar).toBe(true);
            expect(structure.hasWorkspaceExplorer).toBe(true);
            expect(structure.hasMainChatPanel).toBe(true);
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should switch active modal when clicking a different navigation icon', () => {
      const twoDistinctModalsArb = fc.tuple(navModalTypeArb, navModalTypeArb)
        .filter(([first, second]) => first !== second);

      fc.assert(
        fc.property(twoDistinctModalsArb, ([firstModal, secondModal]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const firstButton = screen.getByTestId(modalToNavTestId[firstModal]);
          act(() => {
            firstButton.click();
          });

          expect(firstButton.getAttribute('aria-pressed')).toBe('true');

          const secondButton = screen.getByTestId(modalToNavTestId[secondModal]);
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
        { numRuns: 100 }
      );
    });

    it('should keep left sidebar visible and accessible when modal is open', () => {
      fc.assert(
        fc.property(navModalTypeArb, (modalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(modalToNavTestId[modalType]);
          act(() => {
            navButton.click();
          });

          // Property: Left sidebar SHALL remain visible (Requirement 2.3)
          const leftSidebar = screen.getByTestId('left-sidebar');
          expect(leftSidebar).not.toBeNull();

          // Property: All navigation icons SHALL remain accessible
          for (const otherModalType of navModalTypes) {
            const otherButton = screen.getByTestId(modalToNavTestId[otherModalType]);
            expect(otherButton).not.toBeNull();
            expect(otherButton.hasAttribute('disabled')).toBe(false);
          }

          unmount();
        }),
        { numRuns: 100 }
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
    const navModalTypes = ['skills', 'mcp', 'agents', 'settings'] as const;
    type NavModalType = typeof navModalTypes[number];

    const modalToNavTestId: Record<NavModalType, string> = {
      skills: 'nav-skills',
      mcp: 'nav-mcp',
      agents: 'nav-agents',
      settings: 'nav-settings',
    };

    const navModalTypeArb = fc.constantFrom(...navModalTypes);

    it('should display active visual indicator on the corresponding nav icon when modal is active', () => {
      fc.assert(
        fc.property(navModalTypeArb, (activeModalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(modalToNavTestId[activeModalType]);
          act(() => {
            activeButton.click();
          });

          // Property: Active button SHALL have aria-pressed="true"
          expect(activeButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Active button SHALL have visual indicator classes
          expect(activeButton.classList.contains('bg-[var(--color-primary)]/15') ||
                 activeButton.className.includes('color-primary')).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should only show active indicator on the currently active modal icon', () => {
      fc.assert(
        fc.property(navModalTypeArb, (activeModalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(modalToNavTestId[activeModalType]);
          act(() => {
            activeButton.click();
          });

          // Property: Only the active modal's icon SHALL have the active indicator
          for (const modalType of navModalTypes) {
            const button = screen.getByTestId(modalToNavTestId[modalType]);
            if (modalType === activeModalType) {
              expect(button.getAttribute('aria-pressed')).toBe('true');
            } else {
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should update active indicator when switching between modals', () => {
      const modalSequenceArb = fc.array(navModalTypeArb, { minLength: 2, maxLength: 8 });

      fc.assert(
        fc.property(modalSequenceArb, (modalSequence) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          for (let i = 0; i < modalSequence.length; i++) {
            const currentModal = modalSequence[i];
            const currentButton = screen.getByTestId(modalToNavTestId[currentModal]);

            act(() => {
              currentButton.click();
            });

            // Property: Current button SHALL have active indicator
            expect(currentButton.getAttribute('aria-pressed')).toBe('true');

            // Property: All other buttons SHALL NOT have active indicator
            for (const otherModal of navModalTypes) {
              if (otherModal !== currentModal) {
                const otherButton = screen.getByTestId(modalToNavTestId[otherModal]);
                expect(otherButton.getAttribute('aria-pressed')).toBe('false');
              }
            }
          }

          unmount();
        }),
        { numRuns: 100 }
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
          for (const modalType of navModalTypes) {
            const button = screen.getByTestId(modalToNavTestId[modalType]);
            expect(button.getAttribute('aria-pressed')).toBe('false');
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should maintain active indicator through window resize events', () => {
      const validWindowWidthArb = fc.integer({ min: 320, max: 2000 });

      fc.assert(
        fc.property(navModalTypeArb, validWindowWidthArb, (activeModalType, newWidth) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(modalToNavTestId[activeModalType]);
          act(() => {
            activeButton.click();
          });

          act(() => {
            simulateWindowResize(newWidth);
          });

          // Property: Active indicator SHALL be maintained after resize
          expect(activeButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Other buttons SHALL remain inactive
          for (const modalType of navModalTypes) {
            if (modalType !== activeModalType) {
              const button = screen.getByTestId(modalToNavTestId[modalType]);
              expect(button.getAttribute('aria-pressed')).toBe('false');
            }
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should correctly reflect active state in aria-pressed attribute for accessibility', () => {
      fc.assert(
        fc.property(navModalTypeArb, (modalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const button = screen.getByTestId(modalToNavTestId[modalType]);

          // Property: Before click, aria-pressed SHALL be "false"
          expect(button.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            button.click();
          });

          // Property: After click, aria-pressed SHALL be "true"
          expect(button.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    it('should have consistent visual styling for active vs inactive states', () => {
      fc.assert(
        fc.property(navModalTypeArb, (activeModalType) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const activeButton = screen.getByTestId(modalToNavTestId[activeModalType]);
          act(() => {
            activeButton.click();
          });

          // Property: Active button SHALL have distinct styling from inactive buttons
          for (const modalType of navModalTypes) {
            const button = screen.getByTestId(modalToNavTestId[modalType]);
            const buttonClasses = button.className;

            if (modalType === activeModalType) {
              // Active button should have primary color styling
              expect(buttonClasses.includes('color-primary') ||
                     buttonClasses.includes('ring-1')).toBe(true);
            } else {
              // Inactive buttons should have muted/hover styling
              expect(buttonClasses.includes('color-sidebar-icon') ||
                     buttonClasses.includes('hover:')).toBe(true);
            }
          }

          unmount();
        }),
        { numRuns: 100 }
      );
    });
  });


  /**
   * Property 1: Navigation Item Order Consistency (left-navigation-redesign)
   * **Feature: left-navigation-redesign, Property 1: Navigation Item Order Consistency**
   * **Validates: Requirements 1.1**
   *
   * For any render of the LeftSidebar component, the navigation items SHALL appear
   * in exactly this order: Workspaces, SwarmCore, Agents, Skills, MCP Servers,
   * with no items missing or duplicated.
   */
  describe('Feature: left-navigation-redesign, Property 1: Navigation Item Order Consistency', () => {
    // Expected navigation items in exact order per Requirements 1.1
    const expectedNavOrder = [
      { testId: 'nav-workspaces', label: 'Workspaces' },
      { testId: 'nav-swarmcore', label: 'SwarmCore' },
      { testId: 'nav-agents', label: 'Agents' },
      { testId: 'nav-skills', label: 'Skills' },
      { testId: 'nav-mcp', label: 'MCP Servers' },
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

      // Get all nav buttons in DOM order
      const navButtons = navContainer.querySelectorAll('button[data-testid^="nav-"]');
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

    it('should display all 5 navigation items in exact order for any render', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const result = verifyNavigationOrder();

          // Property: All 5 modal navigation items SHALL be present
          expect(result.allItemsPresent).toBe(true);

          // Property: Modal nav items SHALL appear in exact order: Workspaces, SwarmCore, Agents, Skills, MCP Servers
          expect(result.correctOrder).toBe(true);

          // Property: No items SHALL be duplicated
          expect(result.noDuplicates).toBe(true);

          // Property: At least 5 modal nav items SHALL be present (section nav items may also exist)
          expect(result.foundItems.length).toBeGreaterThanOrEqual(5);

          unmount();
        }),
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
      );
    });

    it('should maintain navigation order when modals are opened', () => {
      const navModalTypes = ['workspaces', 'swarmcore', 'agents', 'skills', 'mcp'] as const;
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
    // All navigation items including new workspaces and swarmcore
    const allNavItems = [
      { testId: 'nav-workspaces', modalType: 'workspaces', label: 'Workspaces' },
      { testId: 'nav-swarmcore', modalType: 'swarmcore', label: 'SwarmCore' },
      { testId: 'nav-agents', modalType: 'agents', label: 'Agents' },
      { testId: 'nav-skills', modalType: 'skills', label: 'Skills' },
      { testId: 'nav-mcp', modalType: 'mcp', label: 'MCP Servers' },
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Navigation click behavior SHALL work through any sequence of clicks.
     */
    it('should correctly handle any sequence of navigation clicks', () => {
      const clickSequenceArb = fc.array(navItemArb, { minLength: 1, maxLength: 10 });

      fc.assert(
        fc.property(clickSequenceArb, (clickSequence: NavItemType[]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          for (const item of clickSequence) {
            const button = screen.getByTestId(item.testId);
            act(() => {
              button.click();
            });

            // Property: After each click, the clicked item SHALL be active
            expect(button.getAttribute('aria-pressed')).toBe('true');

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
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Specifically test the new workspaces navigation item.
     */
    it('should open workspaces modal when workspaces navigation is clicked', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration: number) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const workspacesButton = screen.getByTestId('nav-workspaces');

          // Property: Initially not active
          expect(workspacesButton.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            workspacesButton.click();
          });

          // Property: After click, workspaces modal SHALL be active
          expect(workspacesButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 2.3, 4.1, 5.1**
     *
     * Specifically test the new swarmcore navigation item.
     */
    it('should open swarmcore modal when swarmcore navigation is clicked', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 100 }), (_iteration: number) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const swarmcoreButton = screen.getByTestId('nav-swarmcore');

          // Property: Initially not active
          expect(swarmcoreButton.getAttribute('aria-pressed')).toBe('false');

          act(() => {
            swarmcoreButton.click();
          });

          // Property: After click, swarmcore modal SHALL be active
          expect(swarmcoreButton.getAttribute('aria-pressed')).toBe('true');

          unmount();
        }),
        { numRuns: 100 }
      );
    });

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
          expect(structure.hasWorkspaceExplorer).toBe(true);
          expect(structure.hasMainChatPanel).toBe(true);
          expect(structure.isCorrectlyOrdered).toBe(true);

          // Property: Chat content SHALL still be accessible
          const chatContent = screen.getByTestId('chat-content');
          expect(chatContent).not.toBeNull();

          unmount();
        }),
        { numRuns: 100 }
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
        { numRuns: 100 }
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
    // All 5 navigation items per the redesign
    const allNavItems = [
      { testId: 'nav-workspaces', modalType: 'workspaces', label: 'Workspaces' },
      { testId: 'nav-swarmcore', modalType: 'swarmcore', label: 'SwarmCore' },
      { testId: 'nav-agents', modalType: 'agents', label: 'Agents' },
      { testId: 'nav-skills', modalType: 'skills', label: 'Skills' },
      { testId: 'nav-mcp', modalType: 'mcp', label: 'MCP Servers' },
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
        { numRuns: 100 }
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

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          const navButton = screen.getByTestId(navItem.testId);

          // Click to open modal
          act(() => {
            navButton.click();
          });

          // Property: The navigation item SHALL display active visual state
          expect(navButton.getAttribute('aria-pressed')).toBe('true');

          // Property: Active button SHALL have visual indicator classes
          expect(
            navButton.classList.contains('bg-[var(--color-primary)]/15') ||
            navButton.className.includes('color-primary') ||
            navButton.className.includes('ring-1')
          ).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 8.1, 8.4**
     *
     * Active state SHALL correctly track through any sequence of modal switches.
     */
    it('should correctly track active state through any sequence of modal switches', () => {
      const modalSequenceArb = fc.array(navItemArb, { minLength: 2, maxLength: 10 });

      fc.assert(
        fc.property(modalSequenceArb, (modalSequence: NavItemType[]) => {
          mockStorage.clear();

          const { unmount } = renderWithCleanup(
            <div data-testid="chat-content">Chat Content</div>
          );

          for (const currentItem of modalSequence) {
            const currentButton = screen.getByTestId(currentItem.testId);

            act(() => {
              currentButton.click();
            });

            // Property: Current item SHALL have active state
            expect(currentButton.getAttribute('aria-pressed')).toBe('true');

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
        { numRuns: 100 }
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

          // Property: Active button SHALL have highlighted background color using primary color
          // Property: Active button SHALL have ring border indicator
          const buttonClasses = navButton.className;
          expect(
            buttonClasses.includes('color-primary') ||
            buttonClasses.includes('ring-1') ||
            buttonClasses.includes('bg-')
          ).toBe(true);

          unmount();
        }),
        { numRuns: 100 }
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
        { numRuns: 100 }
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
        { numRuns: 100 }
      );
    });
  });
});
