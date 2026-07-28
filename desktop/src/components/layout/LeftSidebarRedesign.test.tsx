/**
 * Tests for the LeftSidebar redesign (2026-07-12): B ordering + B2 group-tint.
 *
 * Verifies the *behavioral* contract of the visual redesign (things a snapshot
 * can't assert cheaply):
 *   1. NAV ORDER (B) — the 8 nav buttons appear top-to-bottom in group order:
 *        Terminal | Skills, MCP | Code Intel, Engine, OS Eval | Memory, Signals
 *      with Settings + GitHub in the footer (outside <nav>).
 *   2. GROUP TINT (B2) — every nav button carries its group's brand color as the
 *      inline `--ac` CSS custom property (drives hover/active bg+ring+bar without
 *      a Tailwind class → JIT-safe). Terminal=blue, Skills/MCP=purple,
 *      CodeIntel/Engine/OSEval=teal, Memory/Signals=amber.
 *   3. SEPARATORS — 3 group separators inside <nav> (4 groups → 3 rules).
 *
 * We drive the REAL LeftSidebar through its real providers (no mock of the
 * component under change — GUI32 prompt-source = answer-source). Only the pty
 * service boundary is mocked (a render spawns nothing).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { LayoutProvider } from '../../contexts/LayoutContext';
import { TerminalProvider } from '../../contexts/TerminalContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { LeftSidebar } from './ThreeColumnLayout';

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

// group brand colors (B2) — single source mirrored from the component
const BLUE = '#60a5fa';
const PURPLE = '#a78bfa';
const TEAL = '#2dd4bf';
const AMBER = '#fbbf24';

function renderSidebar() {
  return render(
    <ToastProvider>
      <LayoutProvider>
        <TerminalProvider>
          <LeftSidebar />
        </TerminalProvider>
      </LayoutProvider>
    </ToastProvider>,
  );
}

afterEach(() => cleanup());

// The group color each nav button must expose via inline `--ac`.
const EXPECTED: Array<[string, string]> = [
  ['nav-terminal', BLUE],
  ['nav-skills', PURPLE],
  ['nav-mcp', PURPLE],
  ['nav-brain-hub', TEAL],
  ['nav-code-intel', TEAL],
  ['nav-engine', TEAL],
  ['nav-eval', TEAL],
  ['nav-memory', AMBER],
  ['nav-signals', AMBER],
];

describe('LeftSidebar redesign — B ordering', () => {
  it('renders the 9 nav buttons top-to-bottom in group order', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    const ids = within(nav)
      .getAllByRole('button')
      .map((b) => b.getAttribute('data-testid'));
    expect(ids).toEqual([
      'nav-terminal',
      'nav-skills',
      'nav-mcp',
      'nav-brain-hub',
      'nav-code-intel',
      'nav-engine',
      'nav-eval',
      'nav-memory',
      'nav-signals',
    ]);
  });

  it('keeps Settings + GitHub OUT of the nav group (footer)', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    expect(within(nav).queryByTestId('nav-settings')).toBeNull();
    // Settings still exists somewhere in the sidebar (the footer)
    expect(screen.getByTestId('nav-settings')).toBeInTheDocument();
  });

  it('has 3 group separators inside the nav (4 groups → 3 rules)', () => {
    renderSidebar();
    const nav = screen.getByTestId('nav-icons');
    const seps = nav.querySelectorAll('[data-testid="nav-group-sep"]');
    expect(seps.length).toBe(3);
  });
});

describe('LeftSidebar redesign — B2 group tint', () => {
  it.each(EXPECTED)('%s carries its group --ac color', (testid, color) => {
    renderSidebar();
    const btn = screen.getByTestId(testid);
    // inline style custom property survives Tailwind JIT purge
    expect(btn.style.getPropertyValue('--ac').trim()).toBe(color);
  });

  // B (default-tint, 2026-07-12): accent-bearing nav-group icons are toned by
  // DEFAULT (not grey-until-hover) via the .nav-btn--tinted marker class; the
  // footer Settings button (no accent) stays neutral grey.
  it.each(EXPECTED)('%s is tinted by default (.nav-btn--tinted)', (testid) => {
    renderSidebar();
    expect(screen.getByTestId(testid).classList.contains('nav-btn--tinted')).toBe(true);
  });

  it('footer Settings is NOT default-tinted (stays neutral grey)', () => {
    renderSidebar();
    expect(screen.getByTestId('nav-settings').classList.contains('nav-btn--tinted')).toBe(false);
  });
});
