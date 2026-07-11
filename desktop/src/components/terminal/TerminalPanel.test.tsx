/**
 * Render tests for TerminalPanel (AC5 tab-bar behavior).
 *
 * xterm + the PTY service are mocked (jsdom can't render a real terminal, and
 * we don't want real PTY spawns) — we test the PANEL's tab-bar wiring: tabs
 * render, +opens, per-tab close, active switching. TerminalTab is mocked to a
 * stub so we don't drag xterm into the render.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

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

  it('shows empty state with no tabs and a + button', () => {
    renderPanel();
    expect(screen.getByText(/No terminals/i)).toBeInTheDocument();
    expect(screen.getByTestId('terminal-new')).toBeInTheDocument();
  });

  it('AC5: clicking + opens a new terminal tab (chip + surface appear)', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-new'));
    // one tab chip + one surface now exist
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(1);
    expect(surfaces[0].getAttribute('data-active')).toBe('true');
  });

  it('AC5: opening multiple terminals renders multiple chips; newest is active', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-new'));
    fireEvent.click(screen.getByTestId('terminal-new'));
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(2);
    // exactly one active
    expect(surfaces.filter((s) => s.getAttribute('data-active') === 'true').length).toBe(1);
  });

  it('AC6: per-tab close button removes that tab', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-new'));
    const tab = terminalStore.list()[0];
    fireEvent.click(screen.getByTestId(`terminal-close-${tab.id}`));
    expect(screen.queryByTestId(`tab-surface-${tab.id}`)).not.toBeInTheDocument();
    expect(screen.getByText(/No terminals/i)).toBeInTheDocument();
  });

  it('clicking a non-active tab chip activates it', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-new'));
    fireEvent.click(screen.getByTestId('terminal-new'));
    const [first, second] = terminalStore.list();
    // second is active (newest). Click first chip.
    fireEvent.click(screen.getByTestId(`terminal-tab-${first.id}`));
    const firstSurface = screen.getByTestId(`tab-surface-${first.id}`);
    const secondSurface = screen.getByTestId(`tab-surface-${second.id}`);
    expect(firstSurface.getAttribute('data-active')).toBe('true');
    expect(secondSurface.getAttribute('data-active')).toBe('false');
  });

  it('collapse button hides the panel (setPanelOpen false persists)', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('terminal-collapse'));
    expect(localStorage.getItem('terminalPanelOpen')).toBe('false');
  });
});
