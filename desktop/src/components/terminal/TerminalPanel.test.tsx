/**
 * Render tests for TerminalPanel (AC5 tab-bar behavior).
 *
 * xterm + the PTY service are mocked (jsdom can't render a real terminal, and
 * we don't want real PTY spawns) — we test the PANEL's tab-bar wiring: tabs
 * render, +opens, per-tab close, active switching. TerminalTab is mocked to a
 * stub so we don't drag xterm into the render.
 */
import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Mock PTY service so the store's openTerminal doesn't spawn a real shell.
let spawnCount = 0;
vi.mock('../../services/pty', () => ({
  spawn: () => ({
    pid: ++spawnCount,
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
  }),
}));

// Stub TerminalTab (avoids importing xterm into jsdom).
vi.mock('./TerminalTab', () => ({
  default: ({ tab, active }: { tab: { id: string }; active: boolean }) => (
    <div data-testid={`tab-surface-${tab.id}`} data-active={active} />
  ),
}));

import { TerminalProvider } from '../../contexts/TerminalContext';
import TerminalPanel from './TerminalPanel';
import { terminalStore } from '../../stores/TerminalStore';

const renderPanel = () =>
  render(
    <TerminalProvider>
      <TerminalPanel />
    </TerminalProvider>,
  );

describe('TerminalPanel', () => {
  beforeEach(() => {
    spawnCount = 0;
    localStorage.clear();
    terminalStore.clear();
  });

  it('AC2: auto-opens exactly ONE terminal when the panel opens with no tabs', () => {
    renderPanel();
    // No "No terminals" placeholder — a ready shell is auto-opened instead.
    expect(screen.queryByText(/No terminals/i)).not.toBeInTheDocument();
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(1);
    expect(terminalStore.count()).toBe(1);
    expect(screen.getByTestId('terminal-new')).toBeInTheDocument();
  });

  it('AC2: StrictMode double-mount still auto-opens exactly ONE terminal (count guard, not stale snapshot)', () => {
    // The mount effect runs twice under StrictMode against the same commit; a
    // `tabs.length===0` guard would read stale 0 both times and spawn TWO
    // shells. The terminalStore.count() guard reads the live registry → one.
    render(
      <StrictMode>
        <TerminalProvider>
          <TerminalPanel />
        </TerminalProvider>
      </StrictMode>,
    );
    expect(terminalStore.count()).toBe(1);
    expect(screen.getAllByTestId(/^tab-surface-/).length).toBe(1);
  });

  it('AC2: does NOT auto-open a second terminal when one already exists (e.g. explorer opened a cwd tab first)', () => {
    // Simulate the explorer right-click path: a cwd terminal exists before the
    // panel mounts. Auto-open must skip (count()===1), not add an empty 2nd tab.
    terminalStore.openTerminal({ cwd: '/x/Projects/AIDLC' });
    renderPanel();
    expect(terminalStore.count()).toBe(1);
    expect(terminalStore.list()[0].title).toBe('AIDLC');
  });

  it('AC5: clicking + opens an additional terminal tab (auto-opened 1 + clicked 1 = 2)', () => {
    renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(2);
    // the newest (just-clicked) is active
    expect(surfaces.filter((s) => s.getAttribute('data-active') === 'true').length).toBe(1);
  });

  it('AC5: opening multiple terminals renders multiple chips; newest is active', () => {
    renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    fireEvent.click(screen.getByTestId('terminal-new'));
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(3); // 1 auto + 2 clicked
    // exactly one active
    expect(surfaces.filter((s) => s.getAttribute('data-active') === 'true').length).toBe(1);
  });

  it('AC6: per-tab close button removes that tab; closing the last shows the placeholder', () => {
    renderPanel(); // auto-opens 1
    const tab = terminalStore.list()[0];
    fireEvent.click(screen.getByTestId(`terminal-close-${tab.id}`));
    expect(screen.queryByTestId(`tab-surface-${tab.id}`)).not.toBeInTheDocument();
    // Closing the last terminal falls back to the placeholder (auto-open is
    // mount-only, so it does not re-fire on close).
    expect(screen.getByText(/No terminals/i)).toBeInTheDocument();
  });

  it('clicking a non-active tab chip activates it', () => {
    renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    const tabs = terminalStore.list();
    const first = tabs[0];
    const last = tabs[tabs.length - 1];
    // last is active (newest). Click the first chip to activate it.
    fireEvent.click(screen.getByTestId(`terminal-tab-${first.id}`));
    expect(screen.getByTestId(`tab-surface-${first.id}`).getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId(`tab-surface-${last.id}`).getAttribute('data-active')).toBe('false');
  });

  it('collapse button hides the panel (setPanelOpen false persists)', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-collapse'));
    expect(localStorage.getItem('terminalPanelOpen')).toBe('false');
  });
});
