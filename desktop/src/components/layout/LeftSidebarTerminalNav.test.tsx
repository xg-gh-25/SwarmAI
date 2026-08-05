/**
 * Tests for the LeftSidebar integrated-terminal nav entry (third entry point).
 *
 * The terminal has three entry points: the BottomBar toggle (⌘`), the explorer
 * right-click "Open terminal here", and — added here — a nav icon in the left
 * 50px sidebar. This nav button must:
 *   - render (data-testid="nav-terminal")
 *   - reflect the real panel open-state via aria-pressed (shared TerminalProvider)
 *   - toggle panelOpen on click (the SAME togglePanel the other entries use),
 *     so all three entries stay in sync.
 *
 * We wrap LeftSidebar in the two providers it consumes (LayoutProvider +
 * TerminalProvider). We mock the pty service so no real Tauri invoke is needed —
 * but the nav button only flips panelOpen (no PTY is spawned by a toggle), so
 * the mock is belt-and-suspenders against accidental spawns.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LayoutProvider } from '../../contexts/LayoutContext';
import { OverlayProvider } from '../../contexts/OverlayContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { LeftSidebar } from './ThreeColumnLayout';

// Mock the pty service boundary — a panel toggle spawns nothing, but this keeps
// the suite free of any real Tauri invoke path.
vi.mock('../../services/pty', () => ({
  spawn: vi.fn(() => ({
    pid: 1,
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
  })),
}));

function renderSidebar() {
  // QueryClientProvider: LeftSidebar polls Hive fleet status via useQuery
  // (useHiveStatusDot, run_b450108e); disabled in jsdom (isDesktop()=false) but the
  // hook still needs a client in scope.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <LayoutProvider>
          <OverlayProvider>
            <TerminalProvider>
              <LeftSidebar />
            </TerminalProvider>
          </OverlayProvider>
        </LayoutProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('LeftSidebar — integrated terminal nav entry', () => {
  beforeEach(() => {
    // Ensure a clean panel-open state so "inactive by default" is not
    // order-dependent on a prior test's persisted value.
    try {
      localStorage.removeItem('terminalPanelOpen');
    } catch {
      /* ignore */
    }
  });

  afterEach(() => {
    cleanup();
    try {
      localStorage.removeItem('terminalPanelOpen');
    } catch {
      /* ignore */
    }
  });

  it('renders the terminal nav button', () => {
    renderSidebar();
    expect(screen.getByTestId('nav-terminal')).toBeInTheDocument();
  });

  it('is inactive by default (panel closed → aria-pressed false)', () => {
    renderSidebar();
    expect(screen.getByTestId('nav-terminal')).toHaveAttribute('aria-pressed', 'false');
  });

  it('toggles the panel open on click, then closed on a second click', () => {
    renderSidebar();
    const btn = screen.getByTestId('nav-terminal');

    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'false');
  });
});
