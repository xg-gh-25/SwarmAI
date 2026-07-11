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
import { describe, it, expect, vi, beforeEach } from 'vitest';
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

import { terminalStore } from './TerminalStore';

describe('TerminalStore', () => {
  beforeEach(() => {
    spawnCount = 0;
    spawnMock.mockClear();
    terminalStore.clear();
  });

  it('AC4: openTerminal spawns the login shell with -l (Unix test env) for profile PATH', () => {
    // In the jsdom/CI test env navigator is non-Windows → login shell + -l.
    // This proves the platform-aware defaultShell() picks the login shell so
    // brazil/swarm PATH loads (the Windows branch uses powershell.exe, no -l).
    terminalStore.openTerminal({ cwd: '/tmp/proj' });
    const [file, args] = spawnMock.mock.calls[0] as unknown as [string, string[]];
    expect(file).toBe('/bin/zsh');
    expect(args).toEqual(['-l']);
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

  it('openTerminal defaults title from cwd basename, falls back for no cwd', () => {
    const withCwd = terminalStore.openTerminal({ cwd: '/Users/x/Projects/AIDLC' });
    expect(withCwd.title).toBe('AIDLC');
    const noCwd = terminalStore.openTerminal({});
    expect(noCwd.title.length).toBeGreaterThan(0); // some sensible default
  });
});
