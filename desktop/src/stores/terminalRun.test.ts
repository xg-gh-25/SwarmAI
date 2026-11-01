/**
 * The one-click sign-in shell (design 2026-11-01-settings-account-merge §4.3).
 * Questions: does it open at the current terminal location (a repo too), with the title;
 * is the command written only after the shell has started (first output + quiet, or the
 * cap), exactly once, and never with Enter or another control character; and is nothing
 * written to a shell that exited or was closed first?
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { IPty } from '../services/pty';

type Fake = IPty & { emit: (d: string) => void; exit: () => void };
const ptys: Fake[] = [];
function makePty(): Fake {
  const data = new Set<(d: string) => void>();
  const exits = new Set<(e: { exitCode: number }) => void>();
  const p = {
    pid: ptys.length + 1,
    onData: (l: (d: string) => void) => { data.add(l); return { dispose: () => data.delete(l) }; },
    onExit: (l: (e: { exitCode: number }) => void) => { exits.add(l); return { dispose: () => exits.delete(l) }; },
    write: vi.fn(), resize: vi.fn(), kill: vi.fn(),
    emit: (d: string) => { for (const l of [...data]) l(d); },
    exit: () => { for (const l of [...exits]) l({ exitCode: 0 }); },
  } as unknown as Fake;
  ptys.push(p);
  return p;
}
vi.mock('../services/pty', () => ({ spawn: () => makePty() }));

import { terminalStore } from './TerminalStore';
import { terminalLocation } from './terminalLocation';
import { openShellWithCommand } from './terminalRun';

const open = (opts: Parameters<typeof terminalStore.openTerminal>[0]) => terminalStore.openTerminal(opts).id;

beforeEach(() => { vi.useFakeTimers(); ptys.length = 0; terminalStore.clear(); terminalLocation.reset(); });
afterEach(() => { vi.useRealTimers(); });

describe('openShellWithCommand', () => {
  it('opens at the current location with the title, and types only after the shell has started', () => {
    terminalLocation.choose({ kind: 'custom', path: '/work/repo' });
    const id = openShellWithCommand(open, 'mwinit -f', 'Midway sign-in');
    const tab = terminalStore.get(id)!;
    expect(tab.cwd).toBe('/work/repo');
    expect(tab.locKey).toBe(terminalLocation.snapshot().current.key);
    expect(tab.title).toBe('Midway sign-in');
    const pty = ptys[0];
    vi.advanceTimersByTime(1_000);
    expect(pty.write).not.toHaveBeenCalled();          // no output yet: still starting
    pty.emit('Last login: …\r\n');
    vi.advanceTimersByTime(300);
    pty.emit('% ');                                     // more output resets the quiet spell
    vi.advanceTimersByTime(300);
    expect(pty.write).not.toHaveBeenCalled();
    vi.advanceTimersByTime(150);
    expect(pty.write).toHaveBeenCalledTimes(1);
    expect(pty.write).toHaveBeenCalledWith('mwinit -f');
    pty.emit('more');
    vi.advanceTimersByTime(10_000);
    expect(pty.write).toHaveBeenCalledTimes(1);
  });

  it('a shell that never goes quiet still gets the command at the cap', () => {
    openShellWithCommand(open, 'mwinit -f');
    const pty = ptys[0];
    for (let i = 0; i < 20; i++) { pty.emit('x'); vi.advanceTimersByTime(250); }
    expect(pty.write).toHaveBeenCalledWith('mwinit -f');
  });

  it('never writes Enter or another control character', () => {
    openShellWithCommand(open, 'aws sso login\r\n\x03\x1b[A');
    vi.advanceTimersByTime(6_000);
    const written = (ptys[0].write as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(written).toBe('aws sso login [A');
    // eslint-disable-next-line no-control-regex
    expect(written).not.toMatch(/[\x00-\x1f\x7f]/);
  });

  it('writes nothing to a shell that exited or was closed before it started', () => {
    const a = openShellWithCommand(open, 'mwinit -f');
    ptys[0].exit();
    const b = openShellWithCommand(open, 'mwinit -f');
    terminalStore.closeTerminal(b);
    vi.advanceTimersByTime(6_000);
    expect(ptys[0].write).not.toHaveBeenCalled();
    expect(ptys[1].write).not.toHaveBeenCalled();
    expect(terminalStore.get(a)?.status).toBe('exited');
  });
});

describe('who can type a command', () => {
  it('an agent ui_command can never reach the terminal-run or settings events', async () => {
    const { UI_COMMAND_TABLE } = await import('../utils/uiCommands');
    const { TERMINAL_RUN_EVENT, FIX_SIGN_IN_EVENT } = await import('../components/common/settingsEvents');
    const events = Object.values(UI_COMMAND_TABLE).map((e) => e.event);
    expect(events).not.toContain(TERMINAL_RUN_EVENT);
    expect(events).not.toContain(FIX_SIGN_IN_EVENT);   // it runs the same action
  });
});
