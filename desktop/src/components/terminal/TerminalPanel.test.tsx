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
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

// The working-repo read (terminalLocation → canvasSessionService.workingRepo).
const workingRepoMock = vi.fn(async (_sid: string): Promise<{ path: string; name: string } | null> => null);
vi.mock('../../services/canvasSession', () => ({
  canvasSessionService: { workingRepo: (sid: string) => workingRepoMock(sid) },
}));
// The native folder picker (Browse).
const dialogOpen = vi.fn(async (_o: unknown): Promise<string | null> => null);
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: (o: unknown) => dialogOpen(o) }));

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
import { terminalLocation } from '../../stores/terminalLocation';

/** Render the panel and let the reveal's working-repo read settle (the auto-open
 *  waits for it). */
const renderPanel = async () => {
  const r = render(
    <TerminalProvider>
      <TerminalPanel />
    </TerminalProvider>,
  );
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return r;
};
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

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
    terminalLocation.reset();
    workingRepoMock.mockReset();
    workingRepoMock.mockResolvedValue(null);
  });

  it('AC2: auto-opens exactly ONE terminal when the panel opens with no tabs', async () => {
    await renderPanel();
    // No "No terminals" placeholder — a ready shell is auto-opened instead.
    expect(screen.queryByText(/No terminals/i)).not.toBeInTheDocument();
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(1);
    expect(terminalStore.count()).toBe(1);
    expect(screen.getByTestId('terminal-new')).toBeInTheDocument();
  });

  it('AC2: StrictMode double-mount still auto-opens exactly ONE terminal (count guard, not stale snapshot)', async () => {
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
    await flush();
    expect(terminalStore.count()).toBe(1);
    expect(screen.getAllByTestId(/^tab-surface-/).length).toBe(1);
  });

  it('AC2: does NOT auto-open a second terminal when the location already has one (explorer opened it first)', async () => {
    // The explorer right-click path: the folder becomes the location and a shell
    // opens there before the panel mounts. Auto-open must skip it.
    terminalLocation.choose({ kind: 'custom', path: '/x/Projects/AIDLC' });
    terminalStore.openTerminal({ cwd: '/x/Projects/AIDLC' });
    await renderPanel();
    expect(terminalStore.count()).toBe(1);
    expect(screen.getByTestId('terminal-loc-custom')).toHaveAttribute('aria-selected', 'true');
  });

  it('AC5: clicking + opens an additional terminal tab (auto-opened 1 + clicked 1 = 2)', async () => {
    await renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(2);
    // the newest (just-clicked) is active
    expect(surfaces.filter((s) => s.getAttribute('data-active') === 'true').length).toBe(1);
  });

  it('AC5: opening multiple terminals renders multiple chips; newest is active', async () => {
    await renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    fireEvent.click(screen.getByTestId('terminal-new'));
    const surfaces = screen.getAllByTestId(/^tab-surface-/);
    expect(surfaces.length).toBe(3); // 1 auto + 2 clicked
    // exactly one active
    expect(surfaces.filter((s) => s.getAttribute('data-active') === 'true').length).toBe(1);
  });

  it('AC6: per-tab close button removes that tab; closing the last shows the placeholder', async () => {
    await renderPanel(); // auto-opens 1
    const tab = terminalStore.list()[0];
    fireEvent.click(screen.getByTestId(`terminal-close-${tab.id}`));
    expect(screen.queryByTestId(`tab-surface-${tab.id}`)).not.toBeInTheDocument();
    // Closing the last terminal shows the empty state (auto-open runs on reveal
    // only, so it does not re-fire on close).
    expect(screen.getByText(/No terminal in SwarmWS/i)).toBeInTheDocument();
  });

  it('clicking a non-active tab chip activates it', async () => {
    await renderPanel(); // auto-opens 1
    fireEvent.click(screen.getByTestId('terminal-new'));
    const tabs = terminalStore.list();
    const first = tabs[0];
    const last = tabs[tabs.length - 1];
    // last is active (newest). Click the first chip to activate it.
    fireEvent.click(screen.getByTestId(`terminal-tab-${first.id}`));
    expect(screen.getByTestId(`tab-surface-${first.id}`).getAttribute('data-active')).toBe('true');
    expect(screen.getByTestId(`tab-surface-${last.id}`).getAttribute('data-active')).toBe('false');
  });

  it('collapse button hides the panel (setPanelOpen false persists)', async () => {
    await renderPanel();
    fireEvent.click(screen.getByTestId('terminal-collapse'));
    expect(localStorage.getItem('terminalPanelOpen')).toBe('false');
  });

  it('regression: inactive tab WRAPPER is display:none so it cannot overlay + swallow the active tab clicks', async () => {
    // The bug: every tab renders into an `absolute inset-0` wrapper. When only
    // the inner surface toggled display (not the wrapper), an inactive tab's
    // full-size transparent wrapper stayed on top in DOM order and swallowed
    // every mousedown → opening a 2nd tab made the 1st tab unclickable /
    // unselectable. Assert the WRAPPER (parent of the surface) is hidden when
    // inactive and shown when active. Mutation check: reverting the wrapper
    // style to always-visible makes this go RED (both wrappers display:block).
    await renderPanel(); // auto-opens 1
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

  it('regression: an always-mounted but COLLAPSED panel does NOT auto-spawn a shell at startup', async () => {
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
    await flush();
    // No shell spawned, and the panel is hidden (display:none), not unmounted.
    expect(terminalStore.count()).toBe(0);
    expect(screen.getByTestId('terminal-panel').style.display).toBe('none');
  });

  it('regression: collapse then reopen preserves the SAME tab (no unmount, no re-spawn → history survives)', async () => {
    // The bug: {panelOpen && <TerminalPanel/>} unmounted the panel on collapse →
    // term.dispose() destroyed xterm scrollback → reopen showed a blank shell.
    // Now the panel self-hides via display:none and PTYs survive in the store,
    // so reopening shows the SAME tab id (its live PTY), never a fresh spawn.
    // (This test asserts the tab/PTY identity survives; xterm buffer survival is
    // a direct consequence of the component never unmounting — verified live.)
    await renderPanel(); // panelOpen=true → auto-opens exactly 1
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
    await flush();
    expect(screen.getByTestId('terminal-panel').style.display).toBe('flex');
    expect(terminalStore.count()).toBe(1);
    expect(terminalStore.list()[0].id).toBe(originalId);
  });
});

describe('TerminalPanel locations (docs/design/terminal-locations.md)', () => {
  const REPO = { path: '/Users/x/Desktop/SwarmAI-Workspace/swarmai', name: 'swarmai' };

  beforeEach(() => {
    spawnCount = 0;
    localStorage.clear();
    localStorage.setItem('terminalPanelOpen', 'true');
    terminalStore.clear();
    terminalLocation.reset();
    workingRepoMock.mockReset();
    workingRepoMock.mockResolvedValue(null);
  });

  const bindChat = async (tabId: string, sessionId: string | null) => {
    await act(async () => {
      terminalLocation.setActiveChat(tabId, sessionId);
      await terminalLocation.refresh();
    });
  };

  it('with a working repo the first shell opens IN the repo; without one only SwarmWS shows', async () => {
    workingRepoMock.mockImplementation(async (sid) => (sid === 'sess-1' ? REPO : null));
    await bindChat('chat-1', 'sess-1');
    await renderPanel();
    expect(terminalStore.list().map((t) => t.cwd)).toEqual([REPO.path]);
    expect(screen.getByTestId('terminal-loc-repo')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('terminal-loc-repo')).toHaveTextContent('swarmai');

    await bindChat('chat-2', 'sess-2');          // a chat with no run
    expect(screen.queryByTestId('terminal-loc-repo')).toBeNull();
    // the repo shell keeps running, so its folder stays reachable as a segment
    expect(screen.getByTestId('terminal-loc-custom')).toHaveTextContent('swarmai');
    expect(screen.getByTestId('terminal-loc-swarmws')).toHaveAttribute('aria-selected', 'true');
    // a chat switch only SHOWS: it opened nothing in SwarmWS
    expect(terminalStore.count()).toBe(1);
    expect(screen.getByText(/No terminal in SwarmWS/)).toBeInTheDocument();
  });

  it('waits for the working repo before the first auto-open (no stray SwarmWS shell)', async () => {
    let resolve!: (v: typeof REPO) => void;
    workingRepoMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    terminalLocation.setActiveChat('chat-1', 'sess-1');
    await renderPanel();
    expect(terminalStore.count()).toBe(0);
    await act(async () => { resolve(REPO); });
    await waitFor(() => expect(terminalStore.count()).toBe(1));
    expect(terminalStore.list()[0].cwd).toBe(REPO.path);
  });

  it('switching location shows only that location\'s shells and re-uses them; counts badge the others', async () => {
    workingRepoMock.mockResolvedValue(REPO);
    await bindChat('chat-1', 'sess-1');
    await renderPanel();                                     // repo shell #1
    fireEvent.click(screen.getByTestId('terminal-new')); // repo shell #2
    fireEvent.click(screen.getByTestId('terminal-loc-swarmws'));
    expect(terminalStore.countAt('swarmws')).toBe(1);  // opened, since it had none
    expect(screen.getAllByTestId(/^terminal-tab-/)).toHaveLength(1);
    expect(screen.getByTestId('terminal-loc-count-repo')).toHaveTextContent('2');
    fireEvent.click(screen.getByTestId('terminal-loc-repo'));
    expect(terminalStore.count()).toBe(3);             // re-used, nothing new
    expect(screen.getAllByTestId(/^terminal-tab-/)).toHaveLength(2);
    expect(screen.getByTestId('terminal-loc-count-swarmws')).toHaveTextContent('1');
  });

  it('each chat tab keeps its own choice', async () => {
    workingRepoMock.mockResolvedValue(REPO);
    await bindChat('chat-1', 'sess-1');
    await renderPanel();
    fireEvent.click(screen.getByTestId('terminal-loc-swarmws'));  // chat-1 → SwarmWS
    await bindChat('chat-2', 'sess-2');
    expect(screen.getByTestId('terminal-loc-repo')).toHaveAttribute('aria-selected', 'true');  // default
    await bindChat('chat-1', 'sess-1');
    expect(screen.getByTestId('terminal-loc-swarmws')).toHaveAttribute('aria-selected', 'true');
  });

  it('a custom folder with running shells keeps a segment after switching away', async () => {
    await renderPanel();
    act(() => { terminalLocation.choose({ kind: 'custom', path: '/tmp/proj' }); });
    fireEvent(window, new Event('swarm:terminal-goto'));
    expect(terminalStore.countAt('/tmp/proj')).toBe(1);
    fireEvent.click(screen.getByTestId('terminal-loc-swarmws'));
    const custom = screen.getByTestId('terminal-loc-custom');
    expect(custom).toHaveTextContent('proj');
    expect(screen.getByTestId('terminal-loc-count-custom')).toHaveTextContent('1');
  });

  it('closing the shown shell shows the next one at the SAME location', async () => {
    workingRepoMock.mockResolvedValue(REPO);
    await bindChat('chat-1', 'sess-1');
    await renderPanel();
    const repoShell = terminalStore.list()[0];
    fireEvent.click(screen.getByTestId('terminal-new'));
    const second = terminalStore.list()[1];
    fireEvent.click(screen.getByTestId('terminal-loc-swarmws'));  // a newer shell elsewhere
    fireEvent.click(screen.getByTestId('terminal-loc-repo'));
    fireEvent.click(screen.getByTestId(`terminal-close-${second.id}`));
    expect(screen.getByTestId(`tab-surface-${repoShell.id}`).getAttribute('data-active')).toBe('true');
  });

  it('reveal re-reads the repo: a run that moved it opens the new repo, not the cached one', async () => {
    workingRepoMock.mockResolvedValue(REPO);
    await bindChat('chat-1', 'sess-1');                 // cached: swarmai
    localStorage.setItem('terminalPanelOpen', 'false');
    const NEW = { path: '/r/other', name: 'other' };
    workingRepoMock.mockResolvedValue(NEW);            // a newer run since
    await renderPanel();
    fireEvent(window, new Event('swarm:toggle-terminal'));
    await flush();
    expect(terminalStore.list().map((t) => t.cwd)).toEqual([NEW.path]);
  });

  it('a slow reveal read for one chat never opens a shell for the chat now in front', async () => {
    let resolve!: (v: typeof REPO) => void;
    let first = true;
    workingRepoMock.mockImplementation((sid) => {
      if (sid !== 'sess-1') return new Promise(() => {});   // chat-2's read never answers
      if (!first) return Promise.resolve(REPO);
      first = false;
      return new Promise((r) => { resolve = r; });
    });
    terminalLocation.setActiveChat('chat-1', 'sess-1');
    await renderPanel();
    act(() => { terminalLocation.setActiveChat('chat-2', 'sess-2'); });
    await act(async () => { resolve(REPO); });
    await flush();
    expect(terminalStore.count()).toBe(0);
  });

  it('↵ restarts an exited shell only when typed into it, not ⌘↵ or ↵ on the bar\'s buttons', async () => {
    await renderPanel();
    const first = terminalStore.list()[0];
    const onExit = first.pty.onExit as unknown as ReturnType<typeof vi.fn>;
    act(() => (onExit.mock.calls[0][0] as (e: { exitCode: number }) => void)({ exitCode: 0 }));
    const surface = screen.getByTestId(`tab-surface-${first.id}`);
    fireEvent.keyDown(surface, { key: 'Enter', metaKey: true });
    fireEvent.keyDown(screen.getByTestId(`terminal-restart-${first.id}`), { key: 'Enter' });
    expect(terminalStore.get(first.id)).toBeDefined();
  });

  it('an exited shell shows its exit code and ↵ restarts it at the same location', async () => {
    await renderPanel();
    const first = terminalStore.list()[0];
    const onExit = first.pty.onExit as unknown as ReturnType<typeof vi.fn>;
    const fire = onExit.mock.calls[0][0] as (e: { exitCode: number }) => void;
    act(() => fire({ exitCode: 2 }));
    expect(screen.getByTestId(`terminal-exited-${first.id}`)).toHaveTextContent('code 2');
    fireEvent.keyDown(screen.getByTestId(`tab-surface-${first.id}`), { key: 'Enter' });
    expect(terminalStore.get(first.id)).toBeUndefined();
    expect(terminalStore.countAt('swarmws')).toBe(1);
  });

  it('send-to-chat sends the selection when there is one, else the buffer tail, with the cwd', async () => {
    await renderPanel();
    const tab = terminalStore.list()[0];
    tab.getBuffer = () => 'tail';
    tab.getSelection = () => 'picked  ';
    const got: unknown[] = [];
    const h = (e: Event) => got.push((e as CustomEvent).detail);
    window.addEventListener('swarm:attach-terminal', h);
    fireEvent.click(screen.getByTestId('terminal-attach'));
    tab.getSelection = () => '';
    fireEvent.click(screen.getByTestId('terminal-attach'));
    window.removeEventListener('swarm:attach-terminal', h);
    expect(got).toEqual([{ bufferTail: 'picked', cwd: '' }, { bufferTail: 'tail', cwd: '' }]);
  });
});

describe('TerminalPanel Browse (one click)', () => {
  beforeEach(() => {
    spawnCount = 0;
    localStorage.clear();
    localStorage.setItem('terminalPanelOpen', 'true');
    terminalStore.clear();
    terminalLocation.reset();
    workingRepoMock.mockReset();
    workingRepoMock.mockResolvedValue(null);
    dialogOpen.mockReset();
  });

  it('one click on the folder button opens the picker; the picked folder becomes the location', async () => {
    dialogOpen.mockResolvedValue('/Users/x/Repos/kiro-crew');
    await renderPanel();
    expect(screen.queryByTestId('terminal-more-locations')).toBeNull();   // no recents yet: no chevron
    fireEvent.click(screen.getByTestId('terminal-browse'));
    await flush();
    expect(dialogOpen).toHaveBeenCalledWith(expect.objectContaining({ directory: true }));
    expect(terminalStore.countAt('/Users/x/Repos/kiro-crew')).toBe(1);
    expect(screen.getByTestId('terminal-loc-custom')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('terminal-loc-custom')).toHaveTextContent('kiro-crew');
  });

  it('cancelling the picker changes nothing', async () => {
    dialogOpen.mockResolvedValue(null);
    await renderPanel();
    fireEvent.click(screen.getByTestId('terminal-browse'));
    await flush();
    expect(terminalStore.count()).toBe(1);   // just the auto-opened SwarmWS shell
    expect(screen.getByTestId('terminal-loc-swarmws')).toHaveAttribute('aria-selected', 'true');
  });

  it('the recents chevron lists only folders that are not already a segment', async () => {
    act(() => {
      terminalLocation.choose({ kind: 'custom', path: '/a/one' });
      terminalLocation.choose({ kind: 'custom', path: '/a/two' });
      terminalLocation.choose({ kind: 'swarmws' });
    });
    terminalStore.openTerminal({ cwd: '/a/two' });   // /a/two has a live shell → a segment
    await renderPanel();
    fireEvent.click(screen.getByTestId('terminal-more-locations'));
    const items = screen.getAllByTestId('terminal-recent').map((b) => b.getAttribute('title'));
    expect(items).toEqual(['/a/one']);
    fireEvent.click(screen.getAllByTestId('terminal-recent')[0]);
    expect(screen.getByText('one')).toBeInTheDocument();
    expect(screen.queryByTestId('terminal-recents-menu')).toBeNull();
  });
});
