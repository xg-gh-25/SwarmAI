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
import { dispatchUiCommand, UI_COMMAND_TABLE, UI_COMMAND_RESULT_EVENT } from '../uiCommands';
import { ALL_SHOW_EVENTS, BACK_TO_CHAT_EVENT } from '../../components/layout/useExclusiveOverlay';

afterEach(() => vi.restoreAllMocks());

/** The dispatched UI events, without the command-result reports (tested below). */
const navCalls = (spy: { mock: { calls: unknown[][] } }) =>
  spy.mock.calls.map((c) => c[0] as CustomEvent).filter((e) => e.type !== UI_COMMAND_RESULT_EVENT);

describe('UI_COMMAND_TABLE', () => {
  it('every command is a swarm:* event; targets are window except open-canvas-file (document)', () => {
    const cmds = Object.keys(UI_COMMAND_TABLE);
    expect(cmds.length).toBeGreaterThanOrEqual(10);
    for (const [cmd, entry] of Object.entries(UI_COMMAND_TABLE)) {
      // open-canvas-file rides the document-target swarm:open-file (all open-file
      // dispatchers listen on document); every other command is window-target.
      expect(entry.target).toBe(cmd === 'open-canvas-file' ? 'document' : 'window');
      expect(entry.event.startsWith('swarm:')).toBe(true);
      expect(cmd).not.toContain('swarm:'); // cmd id is bare, not the event name
    }
  });

  it('excludes destructive / dropped commands', () => {
    for (const banned of ['open-file', 'open-terminal-here', 'inject-chat-input', 'toast', 'nav-activate', 'show-library']) {
      expect(UI_COMMAND_TABLE[banned]).toBeUndefined();
    }
  });

  // The "shared list" guarantee: show-* is DERIVED from the LeftNav SSOT, so a
  // card add/rename/remove auto-syncs the table — no hand-copied list to drift.
  it('DERIVES every show-* command from the ALL_SHOW_EVENTS SSOT', () => {
    for (const event of ALL_SHOW_EVENTS) {
      const cmd = event.slice('swarm:'.length); // 'swarm:show-todo' → 'show-todo'
      expect(UI_COMMAND_TABLE[cmd]).toEqual({ event, target: 'window' });
    }
    // Bidirectional: every show-* in the table traces back to the SSOT — no
    // stale show-* entry survives a card removal.
    const ssot = new Set<string>(ALL_SHOW_EVENTS);
    for (const [cmd, entry] of Object.entries(UI_COMMAND_TABLE)) {
      if (cmd.startsWith('show-')) {
        expect(ssot.has(entry.event)).toBe(true);
      }
    }
  });

  it('the Canvas layout commands are explicit, window-target, never show-*', () => {
    expect(UI_COMMAND_TABLE['fold-canvas']).toEqual({ event: 'swarm:canvas-fold', target: 'window' });
    expect(UI_COMMAND_TABLE['canvas-tab-artifacts']).toEqual({ event: 'swarm:canvas-tab-artifacts', target: 'window' });
    expect(UI_COMMAND_TABLE['canvas-tab-changes']).toEqual({ event: 'swarm:canvas-tab-changes', target: 'window' });
  });

  it('keeps open-canvas + back-to-chat as explicit non-overlay commands', () => {
    // These are NOT in ALL_SHOW_EVENTS (open-canvas has no overlay; back-to-chat
    // is the close event) — they must be present regardless of the SSOT.
    expect(UI_COMMAND_TABLE['open-canvas']).toEqual({ event: 'swarm:open-canvas', target: 'window' });
    expect(UI_COMMAND_TABLE['back-to-chat']).toEqual({ event: BACK_TO_CHAT_EVENT, target: 'window' });
    // back-to-chat wires the imported constant, not a hand-typed string.
    expect(UI_COMMAND_TABLE['back-to-chat'].event).toBe(BACK_TO_CHAT_EVENT);
  });
});

describe('dispatchUiCommand', () => {
  it('dispatches the correct swarm:* event on window for an allowlisted cmd', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const ok = dispatchUiCommand('open-canvas');
    expect(ok).toBe(true);
    expect(navCalls(spy)).toHaveLength(1);
    expect(navCalls(spy)[0].type).toBe('swarm:open-canvas');
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

  it('derives event+target from its OWN table — never trusts a wire event/target (crux)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    // The security crux is UNCHANGED: dispatchUiCommand derives event+target purely
    // from UI_COMMAND_TABLE keyed by cmd. The optional 2nd arg carries ONLY a data
    // `path` (for open-canvas-file), never an event name or target — a backend still
    // cannot pick the event or flip the target.
    dispatchUiCommand('open-canvas');
    expect(navCalls(spy)).toHaveLength(1);
    const ev = navCalls(spy)[0];
    expect(ev.type).toBe('swarm:open-canvas');
    // nothing from the wire: only the frontend-made result id (Gate-2 LOW).
    expect(Object.keys(ev.detail).sort()).toEqual(['by', 'cmdId']);
  });

  // ── open-canvas origin-tab stamp (run_10c51cac): forwards ONLY {tabId} ──
  it('open-canvas forwards the caller-supplied originTabId as detail.tabId (cross-tab bleed fix)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const ok = dispatchUiCommand('open-canvas', undefined, 'tab-A');
    expect(ok).toBe(true);
    const ev = navCalls(spy)[0];
    expect(ev.type).toBe('swarm:open-canvas');
    // ONLY the origin tab + the result id — no path, nothing from the wire (crux intact).
    expect(ev.detail).toEqual({ by: 'agent', tabId: 'tab-A', cmdId: expect.any(String) });
  });

  it('open-canvas with NO originTabId dispatches detail-less (user-click path → active-tab fallback)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const ok = dispatchUiCommand('open-canvas'); // no originTabId
    expect(ok).toBe(true);
    const ev = navCalls(spy)[0];
    expect(ev.type).toBe('swarm:open-canvas');
    expect(ev.detail.tabId).toBeUndefined(); // useCanvasHost falls back to the active tab
  });

  // ── open-canvas-file (run_c0550cc2): the ONE path-carrying command ──
  it('open-canvas-file dispatches swarm:open-file on DOCUMENT with the path in detail', () => {
    const winSpy = vi.spyOn(window, 'dispatchEvent');
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    const ok = dispatchUiCommand('open-canvas-file', 'Knowledge/Designs/x.md');
    expect(ok).toBe(true);
    // document-target (all open-file listeners are on document), NOT window.
    expect(docSpy).toHaveBeenCalledTimes(1);
    const ev = docSpy.mock.calls[0][0] as CustomEvent;
    expect(ev.type).toBe('swarm:open-file');
    expect(ev.detail).toEqual({ path: 'Knowledge/Designs/x.md', by: 'agent', cmdId: expect.any(String) });
    // NOT dispatched on window (only its pending result report is).
    expect(navCalls(winSpy)).toHaveLength(0);
  });

  it('a pure-nav command IGNORES a supplied path (payload only per-cmd)', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    dispatchUiCommand('open-canvas', 'should/be/ignored.md');
    const ev = navCalls(spy)[0];
    expect(ev.detail.path).toBeUndefined(); // open-canvas never carries a path
  });

  it('open-canvas-file with NO path is a no-op fail (needs a target file)', () => {
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const ok = dispatchUiCommand('open-canvas-file'); // no path
    expect(ok).toBe(false);
    expect(docSpy).not.toHaveBeenCalled();
  });

  it('SECURITY: open-canvas-file rejects absolute / traversal paths by default (no allowAbs)', () => {
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    for (const bad of ['/etc/passwd', '/Users/gawan/.aws/credentials', '~/.ssh/id_rsa', '../../etc/passwd']) {
      expect(dispatchUiCommand('open-canvas-file', bad)).toBe(false);
    }
    expect(docSpy).not.toHaveBeenCalled();
    // a workspace-relative path still opens
    expect(dispatchUiCommand('open-canvas-file', 'Knowledge/Designs/x.md')).toBe(true);
    expect(docSpy).toHaveBeenCalledTimes(1);
  });

  it('SECURITY: allowAbs=true relaxes ONLY leading-/, never ~ or .. (run_cbaecb86)', () => {
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    // ~ and .. stay rejected EVEN with allowAbs=true (escape/traversal, no session nuance)
    expect(dispatchUiCommand('open-canvas-file', '~/.ssh/id_rsa', undefined, true)).toBe(false);
    expect(dispatchUiCommand('open-canvas-file', '../../etc/passwd', undefined, true)).toBe(false);
    expect(dispatchUiCommand('open-canvas-file', 'foo/../../etc/passwd', undefined, true)).toBe(false);
    expect(docSpy).not.toHaveBeenCalled();
    // an absolute path IS admitted when allowAbs=true (local-desktop owner)
    expect(dispatchUiCommand('open-canvas-file', '/Users/gawan/x.md', undefined, true)).toBe(true);
    expect(docSpy).toHaveBeenCalledTimes(1);
  });

  it('SECURITY: allowAbs falsy (channel) still rejects absolute paths — C041', () => {
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    // explicit false AND undefined must both reject an abs path
    expect(dispatchUiCommand('open-canvas-file', '/etc/passwd', undefined, false)).toBe(false);
    expect(dispatchUiCommand('open-canvas-file', '/etc/passwd', undefined, undefined)).toBe(false);
    expect(docSpy).not.toHaveBeenCalled();
  });

  it('FAIL-CLOSED on a raw swarm:* string passed as cmd', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(dispatchUiCommand('swarm:open-terminal-here')).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('dispatchUiCommand — Canvas command results (canvas-proprioception §5)', () => {
  const results = (spy: { mock: { calls: unknown[][] } }) =>
    spy.mock.calls.map((c) => c[0] as CustomEvent).filter((e) => e.type === UI_COMMAND_RESULT_EVENT).map((e) => e.detail);

  it('a forwarded Canvas command reports pending with the same id the event carries', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    const docSpy = vi.spyOn(document, 'dispatchEvent');
    dispatchUiCommand('open-canvas-file', 'a.md', 'tab-A');
    const [r] = results(spy);
    expect(r).toMatchObject({ cmd: 'open-canvas-file', result: 'pending', tabId: 'tab-A' });
    expect((docSpy.mock.calls[0][0] as CustomEvent).detail.cmdId).toBe(r.id);
  });

  it('a path rejected on the spot reports ignored with a reason', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    dispatchUiCommand('open-canvas-file', '../x', 'tab-A');
    dispatchUiCommand('open-canvas-file', '/etc/passwd', 'tab-A');
    dispatchUiCommand('open-canvas-file', undefined, 'tab-A');
    expect(results(spy).map((r) => [r.result, r.reason])).toEqual([
      ['ignored', 'path must be workspace-relative'],
      ['ignored', 'absolute paths are not allowed in this session'],
      ['ignored', 'no path given'],
    ]);
  });

  it('non-Canvas commands report nothing', () => {
    const spy = vi.spyOn(window, 'dispatchEvent');
    dispatchUiCommand('show-todo');
    dispatchUiCommand('back-to-chat');
    expect(results(spy)).toEqual([]);
  });
});

describe('show-brain-hub with a Brain', () => {
  it('opens that Brain: the name rides the event and waits for the Hub to mount', async () => {
    const { takeOverlayTarget } = await import('../overlayTarget');
    const spy = vi.spyOn(window, 'dispatchEvent');
    expect(dispatchUiCommand('show-brain-hub', undefined, undefined, false, 'payments-api')).toBe(true);
    const ev = spy.mock.calls.map((c) => c[0] as CustomEvent).find((e) => e.type === 'swarm:show-brain-hub')!;
    expect(ev.detail).toEqual({ brain: 'payments-api' });
    expect(takeOverlayTarget('brain-hub')).toBe('payments-api');
    expect(takeOverlayTarget('brain-hub')).toBeUndefined();   // taken once
  });

  it('a name that is not a project name is dropped: the Hub opens on its home', async () => {
    const { takeOverlayTarget } = await import('../overlayTarget');
    for (const bad of ['../x', '/abs', '', 7, 'a/b']) {
      const spy = vi.spyOn(window, 'dispatchEvent');
      dispatchUiCommand('show-brain-hub', undefined, undefined, false, bad);
      const ev = spy.mock.calls.map((c) => c[0] as CustomEvent).find((e) => e.type === 'swarm:show-brain-hub')!;
      expect(ev.detail).toBeNull();
      expect(takeOverlayTarget('brain-hub')).toBeUndefined();
      spy.mockRestore();
    }
  });
});
