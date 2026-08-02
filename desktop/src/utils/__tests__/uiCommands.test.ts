/**
 * Tests for the agent UI-action (ACT) dispatch bridge — proprioception Run 2.
 *
 * SECURITY CRUX (Gate-1 BLOCK 1): the frontend owns the cmd→{event,target} table
 * and derives the dispatched event + target from ITS OWN table keyed by `cmd`. It
 * MUST NOT trust any backend-supplied event/target on the wire — otherwise a buggy
 * or compromised backend could name `swarm:open-terminal-here` and the enum
 * protection would be theater. These tests prove:
 *  - an allowlisted cmd dispatches the CORRECT swarm:* event on the CORRECT target;
 *  - an unknown / destructive cmd dispatches NOTHING (fail-closed);
 *  - a backend-supplied event/target field is IGNORED (frontend uses its own table).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { dispatchUiCommand, UI_COMMAND_TABLE } from '../uiCommands';

afterEach(() => vi.restoreAllMocks());

describe('UI_COMMAND_TABLE', () => {
  it('every command is window-target and a swarm:* event (Run 2 scope)', () => {
    const cmds = Object.keys(UI_COMMAND_TABLE);
    expect(cmds.length).toBeGreaterThanOrEqual(10);
    for (const [cmd, entry] of Object.entries(UI_COMMAND_TABLE)) {
      expect(entry.target).toBe('window');
      expect(entry.event.startsWith('swarm:')).toBe(true);
      expect(cmd).not.toContain('swarm:'); // cmd id is bare, not the event name
    }
  });

  it('excludes destructive / dropped commands', () => {
    for (const banned of ['open-file', 'open-terminal-here', 'inject-chat-input', 'toast', 'nav-activate', 'show-library']) {
      expect(UI_COMMAND_TABLE[banned]).toBeUndefined();
    }
  });
});

describe('dispatchUiCommand', () => {
  it('dispatches the correct swarm:* event on window for an allowlisted cmd', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const ok = dispatchUiCommand('open-canvas');
    expect(ok).toBe(true);
    expect(spy).toHaveBeenCalledTimes(1);
    const ev = spy.mock.calls[0][0] as CustomEvent;
    expect(ev.type).toBe('swarm:open-canvas');
  });

  it('dispatches show-* overlays on window', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    dispatchUiCommand('show-todo');
    expect((spy.mock.calls[0][0] as CustomEvent).type).toBe('swarm:show-todo');
  });

  it('FAIL-CLOSED: unknown cmd dispatches nothing and returns false', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const ok = dispatchUiCommand('definitely-not-a-command');
    expect(ok).toBe(false);
    expect(spy).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
  });

  it('FAIL-CLOSED: destructive cmd never dispatches (even if it arrives)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(dispatchUiCommand('open-terminal-here')).toBe(false);
    expect(dispatchUiCommand('inject-chat-input')).toBe(false);
    expect(dispatchUiCommand('open-file')).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it('derives event+target from its OWN table — signature accepts ONLY cmd (crux)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    // dispatchUiCommand takes cmd ONLY — there is no parameter through which a
    // backend could supply an event name or target. It dispatches swarm:open-canvas
    // on window, derived purely from UI_COMMAND_TABLE['open-canvas'].
    dispatchUiCommand('open-canvas');
    expect(spy).toHaveBeenCalledTimes(1);
    const ev = spy.mock.calls[0][0] as CustomEvent;
    expect(ev.type).toBe('swarm:open-canvas');
    // payload-less by design — no detail forwarded (Gate-2 LOW).
    expect(ev.detail).toBeNull();
  });

  it('FAIL-CLOSED on a raw swarm:* string passed as cmd', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(dispatchUiCommand('swarm:open-terminal-here')).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });
});
