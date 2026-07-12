/**
 * AC1 focus-race regression test for TerminalTab.
 *
 * The bug: term.focus() lived ONLY in the height-gated [active] effect. On first
 * panel-open the TerminalTab mounts active=true in the SAME commit the flex
 * panel mounts, so getBoundingClientRect() returns height 0, the guard fails,
 * and focus is skipped. The ResizeObserver/safeFit recovery path re-fit but
 * never focused → the terminal was sized-but-unfocused → keystrokes never
 * reached term.onData → "can't type" while output streamed fine.
 *
 * The fix: safeFit focuses once when the tab is active AND now sized (guarded by
 * a one-shot focusedRef so later resize ticks don't steal focus).
 *
 * This test drives the exact race: mount with height 0 (no focus), then fire the
 * ResizeObserver with a real size (settle) and assert focus() is called exactly
 * once. Mutation check: reverting the safeFit focus makes this go RED (focus
 * count 0), proving it guards the real behavior — not vacuous.
 *
 * We mock @xterm/xterm + addon-fit (jsdom can't render a terminal) and provide a
 * capturing ResizeObserver so the settle tick is deterministic.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

// ---- Capture the xterm Terminal instance so we can spy focus() ----
const focusSpy = vi.fn();
const fitSpy = vi.fn();
let lastTerm: { focus: typeof focusSpy; cols: number; rows: number } | null = null;

vi.mock('@xterm/xterm', () => {
  class Terminal {
    focus = focusSpy;
    cols = 80;
    rows = 24;
    loadAddon = vi.fn();
    open = vi.fn();
    write = vi.fn();
    dispose = vi.fn();
    onData = vi.fn(() => ({ dispose: vi.fn() }));
    buffer = { active: { length: 0, getLine: () => null } };
    constructor() {
      lastTerm = this as unknown as typeof lastTerm;
    }
  }
  return { Terminal };
});

vi.mock('@xterm/addon-fit', () => {
  class FitAddon {
    fit = fitSpy;
  }
  return { FitAddon };
});

vi.mock('@xterm/xterm/css/xterm.css', () => ({}));

// ---- Capturing ResizeObserver: store the callback so the test drives it ----
let resizeCb: (() => void) | null = null;
class FakeResizeObserver {
  constructor(cb: () => void) {
    resizeCb = cb;
  }
  observe = vi.fn();
  disconnect = vi.fn();
}

import TerminalTab from './TerminalTab';
import type { TerminalTab as TerminalTabModel } from '../../stores/TerminalStore';

function makeTab(): TerminalTabModel {
  return {
    id: 'term-1',
    title: 'zsh',
    pty: {
      pid: 1,
      onData: vi.fn(() => ({ dispose: vi.fn() })),
      onExit: vi.fn(() => ({ dispose: vi.fn() })),
      write: vi.fn(),
      resize: vi.fn(),
      kill: vi.fn(),
    },
    status: 'running',
  };
}

describe('TerminalTab — AC1 focus race', () => {
  let rectSize: { width: number; height: number };

  beforeEach(() => {
    focusSpy.mockClear();
    fitSpy.mockClear();
    resizeCb = null;
    lastTerm = null;
    rectSize = { width: 0, height: 0 }; // start height 0 (the race condition)
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    // getBoundingClientRect reads the mutable rectSize so the test controls it.
    Element.prototype.getBoundingClientRect = vi.fn(function () {
      return { width: rectSize.width, height: rectSize.height, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('focuses the active terminal on the ResizeObserver settle when the first mount saw height 0', () => {
    render(<TerminalTab tab={makeTab()} active={true} />);

    // First mount: height 0 → the [active] effect + initial safeFit both skip
    // focus (container not sized yet). This reproduces the bug's precondition.
    expect(focusSpy).not.toHaveBeenCalled();
    expect(resizeCb).toBeTypeOf('function');

    // Layout settles: container now has real size, ResizeObserver fires.
    rectSize = { width: 800, height: 300 };
    resizeCb!();

    // The fix: safeFit focuses on the settle tick. Without it (bug), 0 calls.
    expect(focusSpy).toHaveBeenCalledTimes(1);
  });

  it('is one-shot: a second resize tick does NOT re-focus (no focus theft)', () => {
    render(<TerminalTab tab={makeTab()} active={true} />);
    rectSize = { width: 800, height: 300 };
    resizeCb!(); // settle → focus #1
    resizeCb!(); // another resize (e.g. user drags panel) → must NOT re-focus
    resizeCb!();
    expect(focusSpy).toHaveBeenCalledTimes(1);
  });

  it('does NOT focus an inactive terminal on settle (only the active one)', () => {
    render(<TerminalTab tab={makeTab()} active={false} />);
    rectSize = { width: 800, height: 300 };
    resizeCb!();
    expect(focusSpy).not.toHaveBeenCalled();
  });

  it('async font preload: terminal wires up (fit+focus) only AFTER document.fonts.load resolves', async () => {
    // Gate-2 MEDIUM: jsdom has no document.fonts, so every other test exercises
    // the SYNCHRONOUS collapse of init(). This test stubs an ASYNC fonts.load
    // that resolves on a later task — the real-browser path — and proves:
    //   (a) init() does NOT construct the terminal until the font resolves
    //       (no Terminal, no ResizeObserver callback, before resolution), and
    //   (b) once resolved, the terminal is built + the settle path focuses.
    // This is the whole point of the drift fix (measure the cell with JBM
    // loaded), so the async ordering must be pinned, not left to jsdom's no-op.
    let resolveFont!: () => void;
    const fontGate = new Promise<void>((r) => { resolveFont = r; });
    const loadSpy = vi.fn(() => fontGate.then(() => []));
    vi.stubGlobal('document', document); // keep real document
    (document as unknown as { fonts: unknown }).fonts = { load: loadSpy };

    try {
      rectSize = { width: 800, height: 300 }; // sized from the start
      render(<TerminalTab tab={makeTab()} active={true} />);

      // Before the font resolves: init() is parked on the await → NO terminal,
      // NO ResizeObserver wired, NO focus.
      await Promise.resolve();
      expect(loadSpy).toHaveBeenCalledWith("11px 'JetBrains Mono'");
      expect(lastTerm).toBeNull();
      expect(resizeCb).toBeNull();
      expect(focusSpy).not.toHaveBeenCalled();

      // Font resolves → init() resumes, constructs the terminal, wires RO, and
      // safeFit focuses the active+sized surface.
      resolveFont();
      await fontGate;
      await Promise.resolve(); // let init()'s continuation run
      expect(lastTerm).not.toBeNull();
      expect(resizeCb).toBeTypeOf('function');
      expect(focusSpy).toHaveBeenCalledTimes(1);
    } finally {
      delete (document as unknown as { fonts?: unknown }).fonts;
    }
  });

  it('re-focuses on switch-back even if the [active] effect reads height 0 (re-arm)', () => {
    // Adversarial MED: after the first focus, switching away and back must
    // re-focus. If the [active] effect on re-activation reads height 0 (layout
    // not flushed), the re-arm (focusedRef=false in the else branch) lets the
    // settle tick re-focus. Mount at height 0 (beforeEach default) to match the
    // real bug precondition and avoid a mount-while-sized double-focus.
    const tab = makeTab();
    const { rerender } = render(<TerminalTab tab={tab} active={true} />);
    rectSize = { width: 800, height: 300 };
    resizeCb!(); // settle → focus #1
    expect(focusSpy).toHaveBeenCalledTimes(1);

    // Switch away (inactive), then back with height 0 (display toggle not yet
    // laid out) → the [active] effect can't focus and re-arms the one-shot.
    rerender(<TerminalTab tab={tab} active={false} />);
    rectSize = { width: 0, height: 0 };
    rerender(<TerminalTab tab={tab} active={true} />);
    // A later resize settles with real size → the re-armed one-shot re-focuses.
    rectSize = { width: 800, height: 300 };
    resizeCb!();
    expect(focusSpy).toHaveBeenCalledTimes(2);
  });
});
