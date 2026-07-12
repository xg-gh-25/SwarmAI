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
    // The panel is now ALWAYS mounted and self-hides on panelOpen; auto-open +
    // all tab-bar behavior only apply when the panel is OPEN. Seed panelOpen=true
    // (TerminalProvider reads this key at init) so these tests exercise the
    // open-panel state they assert on. (A dedicated test below covers the
    // collapsed state = no auto-spawn.)
    localStorage.setItem('terminalPanelOpen', 'true');
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

  it('regression: inactive tab WRAPPER is display:none so it cannot overlay + swallow the active tab clicks', () => {
    // The bug: every tab renders into an `absolute inset-0` wrapper. When only
    // the inner surface toggled display (not the wrapper), an inactive tab's
    // full-size transparent wrapper stayed on top in DOM order and swallowed
    // every mousedown → opening a 2nd tab made the 1st tab unclickable /
    // unselectable. Assert the WRAPPER (parent of the surface) is hidden when
    // inactive and shown when active. Mutation check: reverting the wrapper
    // style to always-visible makes this go RED (both wrappers display:block).
    renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new')); // opens 2nd, now active
    const tabs = terminalStore.list();
    const first = tabs[0];
    const second = tabs[1];

    const firstWrapper = screen.getByTestId(`tab-surface-${first.id}`).parentElement!;
    const secondWrapper = screen.getByTestId(`tab-surface-${second.id}`).parentElement!;

    // 2nd is active → visible; 1st is inactive → removed from hit-testing.
    expect(secondWrapper.style.display).toBe('block');
    expect(firstWrapper.style.display).toBe('none');

    // Switch back to the 1st tab: its wrapper must become visible, the 2nd's hidden.
    fireEvent.click(screen.getByTestId(`terminal-tab-${first.id}`));
    expect(firstWrapper.style.display).toBe('block');
    expect(secondWrapper.style.display).toBe('none');
  });

  it('regression: an always-mounted but COLLAPSED panel does NOT auto-spawn a shell at startup', () => {
    // The panel is now always mounted (so collapse/reopen preserves history).
    // The adversarial gate caught that a bare mount-effect would then spawn a
    // shell at APP STARTUP even when the user never opened the terminal. The
    // auto-open must gate on panelOpen. Render with panelOpen=false (collapsed).
    localStorage.setItem('terminalPanelOpen', 'false');
    render(
      <TerminalProvider>
        <TerminalPanel />
      </TerminalProvider>,
    );
    // No shell spawned, and the panel is hidden (display:none), not unmounted.
    expect(terminalStore.count()).toBe(0);
    expect(screen.getByTestId('terminal-panel').style.display).toBe('none');
  });

  it('regression: collapse then reopen preserves the SAME tab (no unmount, no re-spawn → history survives)', () => {
    // The bug: {panelOpen && <TerminalPanel/>} unmounted the panel on collapse →
    // term.dispose() destroyed xterm scrollback → reopen showed a blank shell.
    // Now the panel self-hides via display:none and PTYs survive in the store,
    // so reopening shows the SAME tab id (its live PTY), never a fresh spawn.
    // (This test asserts the tab/PTY identity survives; xterm buffer survival is
    // a direct consequence of the component never unmounting — verified live.)
    renderPanel(); // panelOpen=true → auto-opens exactly 1
    const originalId = terminalStore.list()[0].id;
    expect(terminalStore.count()).toBe(1);

    // Collapse (▾) → panelOpen=false. The panel stays mounted, hidden.
    fireEvent.click(screen.getByTestId('terminal-collapse'));
    expect(screen.getByTestId('terminal-panel').style.display).toBe('none');
    // The PTY tab is NOT killed by collapse (only ✕ / closeTerminal kills it).
    expect(terminalStore.count()).toBe(1);
    expect(terminalStore.list()[0].id).toBe(originalId);

    // Reopen via the toggle event → panelOpen=true. Same tab, NO new spawn.
    fireEvent(window, new Event('swarm:toggle-terminal'));
    expect(screen.getByTestId('terminal-panel').style.display).toBe('flex');
    expect(terminalStore.count()).toBe(1);
    expect(terminalStore.list()[0].id).toBe(originalId);
  });
});
