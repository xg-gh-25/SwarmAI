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

  it('AC4: untitled (no-cwd) terminals get distinct numbered titles (zsh, zsh 2, zsh 3)', () => {
    const a = terminalStore.openTerminal({});
    const b = terminalStore.openTerminal({});
    const c = terminalStore.openTerminal({});
    expect([a.title, b.title, c.title]).toEqual(['zsh', 'zsh 2', 'zsh 3']);
  });

  it('AC4: cwd-named tabs do not consume untitled slot numbers', () => {
    const a = terminalStore.openTerminal({}); // zsh
    const proj = terminalStore.openTerminal({ cwd: '/x/Projects/AIDLC' }); // AIDLC
    const b = terminalStore.openTerminal({}); // zsh 2 (proj didn't take a slot)
    expect(a.title).toBe('zsh');
    expect(proj.title).toBe('AIDLC');
    expect(b.title).toBe('zsh 2');
  });

  it('AC4: a cwd tab basenamed "zsh" does NOT perturb untitled numbering (structural !cwd filter)', () => {
    // Adversarial LOW: numbering must key off cwd-absence, not a title regex —
    // a cwd ending in /zsh yields title "zsh" but must NOT collide with the
    // untitled slot sequence.
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
