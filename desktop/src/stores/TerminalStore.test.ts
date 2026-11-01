/**
 * Tests for TerminalStore — the module-level registry of terminal tabs.
 *
 * Mirrors messageStoreRegistry (survives React StrictMode double-mount). Owns
 * the IPty lifecycle: openTerminal spawns, closeTerminal kills + removes.
 *
 * Covers Gate-1 findings:
 *   - C1/C2: StrictMode-safe — closeTerminal identity-checks the stored pty so a
 *     replaced (re-mounted) session's old pty can't be orphaned, and a stale
 *     close can't kill a newer pty.
 *   - AC6: close kills the OS process (pty.kill called) and removes from registry.
 *
 * We mock the pty service boundary (spawn) — the store is real code.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { IPty } from '../services/pty';

// Fake IPty factory — each spawn returns a distinct spy-able handle.
let spawnCount = 0;
function makeFakePty(): IPty {
  return {
    pid: ++spawnCount,
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    onExit: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    resize: vi.fn(),
    kill: vi.fn(),
  };
}

const spawnMock = vi.fn(() => makeFakePty());
vi.mock('../services/pty', () => ({
  spawn: (...args: unknown[]) => spawnMock(...(args as [])),
}));

// The Rust `default_shell` command answers with the user's $SHELL (else /bin/bash).
const { invokeMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(async (cmd: string): Promise<unknown> => {
    if (cmd === 'default_shell') return '/bin/zsh';
    throw new Error(`unexpected invoke ${cmd}`);
  }),
}));
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (cmd: string, args?: unknown) => invokeMock(cmd, args as never),
}));

import { terminalStore } from './TerminalStore';

const realUserAgent = Object.getOwnPropertyDescriptor(window.navigator, 'userAgent');
function setUserAgent(ua: string) {
  Object.defineProperty(window.navigator, 'userAgent', { value: ua, configurable: true });
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('TerminalStore', () => {
  beforeEach(async () => {
    spawnCount = 0;
    spawnMock.mockClear();
    invokeMock.mockClear();
    terminalStore.clear();
    terminalStore.resetDefaultShell();
    // Most cases open shells after the default shell is known (the usual case: it is
    // asked for when the store loads, long before a person opens a terminal).
    await terminalStore.loadDefaultShell();
  });

  afterEach(() => {
    if (realUserAgent) Object.defineProperty(window.navigator, 'userAgent', realUserAgent);
    else delete (window.navigator as unknown as Record<string, unknown>).userAgent;
  });

  it('macOS/Linux: spawns the shell the Rust side reports ($SHELL) as a login shell (-l)', async () => {
    invokeMock.mockImplementationOnce(async () => '/opt/homebrew/bin/fish');
    terminalStore.resetDefaultShell();
    await terminalStore.loadDefaultShell();
    const tab = terminalStore.openTerminal({ cwd: '/tmp/proj' });
    expect(invokeMock).toHaveBeenCalledWith('default_shell', undefined);
    const [file, args] = spawnMock.mock.calls[0] as unknown as [string, string[]];
    expect(file).toBe('/opt/homebrew/bin/fish');
    expect(args).toEqual(['-l']);
    expect(tab.title).toBe('fish');
  });

  it('a shell opened before the default shell is known spawns once it is, named after it', async () => {
    invokeMock.mockImplementationOnce(async () => '/bin/bash');
    terminalStore.resetDefaultShell();
    const tab = terminalStore.openTerminal({});
    expect(spawnMock).not.toHaveBeenCalled();
    tab.pty.write('ls');
    await flush();
    expect(spawnMock).toHaveBeenCalledTimes(1);
    const [file, args] = spawnMock.mock.calls[0] as unknown as [string, string[]];
    expect([file, args]).toEqual(['/bin/bash', ['-l']]);
    expect(terminalStore.get(tab.id)?.title).toBe('bash');
    const real = spawnMock.mock.results[0].value as IPty;
    expect(real.write).toHaveBeenCalledWith('ls'); // input typed early is not lost
  });

  it('a shell closed before the default shell is known is never spawned', async () => {
    terminalStore.resetDefaultShell();
    const tab = terminalStore.openTerminal({});
    terminalStore.closeTerminal(tab.id);
    await flush();
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it('Windows: spawns powershell.exe with no args and does not ask the Rust side', async () => {
    setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36');
    terminalStore.resetDefaultShell();
    invokeMock.mockClear();
    await terminalStore.loadDefaultShell();
    const tab = terminalStore.openTerminal({});
    const [file, args] = spawnMock.mock.calls[0] as unknown as [string, string[]];
    expect(file).toBe('powershell.exe');
    expect(args).toEqual([]);
    expect(tab.title).toBe('powershell');
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it('never falls back to a hardcoded /bin/zsh when the Rust side cannot answer', async () => {
    invokeMock.mockImplementationOnce(async () => { throw new Error('unknown command'); });
    terminalStore.resetDefaultShell();
    await terminalStore.loadDefaultShell();
    terminalStore.openTerminal({});
    const [file] = spawnMock.mock.calls[0] as unknown as [string, string[]];
    expect(file).toBe('/bin/bash');
  });

  it('openTerminal spawns a pty and registers a tab', () => {
    const tab = terminalStore.openTerminal({ cwd: '/tmp/proj' });
    expect(spawnMock).toHaveBeenCalledTimes(1);
    expect(terminalStore.count()).toBe(1);
    expect(tab.cwd).toBe('/tmp/proj');
    expect(terminalStore.get(tab.id)).toBe(tab);
  });

  it('AC6: closeTerminal kills the pty and removes the tab', () => {
    const tab = terminalStore.openTerminal({});
    const killSpy = tab.pty.kill as ReturnType<typeof vi.fn>;
    terminalStore.closeTerminal(tab.id);
    expect(killSpy).toHaveBeenCalledTimes(1);
    expect(terminalStore.get(tab.id)).toBeUndefined();
    expect(terminalStore.count()).toBe(0);
  });

  it('C2: closeTerminal on an unknown id is a no-op (no throw)', () => {
    expect(() => terminalStore.closeTerminal('does-not-exist')).not.toThrow();
    expect(terminalStore.count()).toBe(0);
  });

  it('AC6: closeAll kills every pty and empties the registry', () => {
    const a = terminalStore.openTerminal({});
    const b = terminalStore.openTerminal({});
    const killA = a.pty.kill as ReturnType<typeof vi.fn>;
    const killB = b.pty.kill as ReturnType<typeof vi.fn>;
    terminalStore.closeAll();
    expect(killA).toHaveBeenCalledTimes(1);
    expect(killB).toHaveBeenCalledTimes(1);
    expect(terminalStore.count()).toBe(0);
  });

  it('C1: each openTerminal gets a distinct id + distinct pty (no collision)', () => {
    const a = terminalStore.openTerminal({});
    const b = terminalStore.openTerminal({});
    expect(a.id).not.toBe(b.id);
    expect(a.pty).not.toBe(b.pty);
    expect(terminalStore.count()).toBe(2);
  });

  it('list returns tabs in insertion order (for a stable tab bar)', () => {
    const a = terminalStore.openTerminal({});
    const b = terminalStore.openTerminal({});
    const c = terminalStore.openTerminal({});
    expect(terminalStore.list().map((t) => t.id)).toEqual([a.id, b.id, c.id]);
  });

  it('titles are numbered within a LOCATION; the switcher, not the title, shows the folder', () => {
    const a = terminalStore.openTerminal({ cwd: '/Users/x/Projects/AIDLC' });
    const b = terminalStore.openTerminal({ cwd: '/Users/x/Projects/AIDLC/' }); // same location
    const ws = terminalStore.openTerminal({});
    expect([a.title, b.title, ws.title]).toEqual(['zsh', 'zsh 2', 'zsh']);
    expect([a.locKey, b.locKey, ws.locKey]).toEqual(['/Users/x/Projects/AIDLC', '/Users/x/Projects/AIDLC', 'swarmws']);
    expect(terminalStore.listAt('/Users/x/Projects/AIDLC').map((t) => t.id)).toEqual([a.id, b.id]);
    expect(terminalStore.countAt('swarmws')).toBe(1);
  });

  it('records the exit status when the shell exits', () => {
    let fire: ((e: { exitCode: number }) => void) | null = null;
    spawnMock.mockImplementationOnce(() => ({
      ...makeFakePty(),
      onExit: vi.fn((cb: (e: { exitCode: number }) => void) => { fire = cb; return { dispose: vi.fn() }; }),
    }) as unknown as IPty);
    const tab = terminalStore.openTerminal({});
    fire!({ exitCode: 3 });
    expect(terminalStore.get(tab.id)?.status).toBe('exited');
    expect(terminalStore.get(tab.id)?.exitCode).toBe(3);
  });

  it('AC4: untitled (no-cwd) terminals get distinct numbered titles (zsh, zsh 2, zsh 3)', () => {
    const a = terminalStore.openTerminal({});
    const b = terminalStore.openTerminal({});
    const c = terminalStore.openTerminal({});
    expect([a.title, b.title, c.title]).toEqual(['zsh', 'zsh 2', 'zsh 3']);
  });

  it('AC4: shells at another location do not consume this location\'s numbers', () => {
    const a = terminalStore.openTerminal({}); // zsh
    const proj = terminalStore.openTerminal({ cwd: '/x/Projects/AIDLC' }); // zsh (its own location)
    const b = terminalStore.openTerminal({}); // zsh 2
    expect(a.title).toBe('zsh');
    expect(proj.title).toBe('zsh');
    expect(b.title).toBe('zsh 2');
  });

  it('AC4: a cwd tab whose folder is named "zsh" numbers within its own location', () => {
    // Numbering keys off the location (locKey), never a title regex.
    const a = terminalStore.openTerminal({}); // untitled → zsh (slot 1)
    const proj = terminalStore.openTerminal({ cwd: '/x/zsh' }); // cwd basename "zsh"
    const b = terminalStore.openTerminal({}); // untitled → must be zsh 2, not zsh 3
    expect(a.title).toBe('zsh');
    expect(proj.title).toBe('zsh'); // basename, but it's a cwd tab
    expect(b.title).toBe('zsh 2'); // the cwd "zsh" did not consume slot 1
  });

  it('AC4: closing a middle untitled tab never produces a duplicate label (max-slot+1)', () => {
    const a = terminalStore.openTerminal({}); // zsh   (slot 1)
    const b = terminalStore.openTerminal({}); // zsh 2 (slot 2)
    const c = terminalStore.openTerminal({}); // zsh 3 (slot 3)
    expect([a.title, b.title, c.title]).toEqual(['zsh', 'zsh 2', 'zsh 3']);
    terminalStore.closeTerminal(b.id); // remove slot 2 → {zsh, zsh 3}
    const d = terminalStore.openTerminal({}); // must be zsh 4, NOT a second zsh 3
    expect(d.title).toBe('zsh 4');
    const titles = terminalStore.list().map((t) => t.title);
    expect(new Set(titles).size).toBe(titles.length); // all distinct
  });
});
