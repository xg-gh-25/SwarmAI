/**
 * Tests for the shared Modal component — the enter/exit ANIMATION state machine
 * (run_9775fcd8) plus the fullscreen mode-badge + layout branch.
 *
 * The animation contract (aligned to layout-A10 mockup):
 *  - `isOpen=false` does NOT instantly unmount — the modal stays in the DOM
 *    through a 300ms exit backstop (EXIT_MS) so the close transition can play.
 *  - `return null` gates on internal `isRendering`, NOT `isOpen`.
 *  - Esc + body-scroll-lock gate on `isOpen` (intent), not `isRendering`.
 *  - re-entrancy: isOpen flipping true mid-exit cancels the pending unmount.
 *  - mode badge + fullscreen layout render ONLY for size="fullscreen".
 *
 * Uses fake timers to drive the exit backstop deterministically. rAF is stubbed
 * to run synchronously so the enter class applies without a real paint loop.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import Modal, { FS_WIDTH } from './Modal';
import { setNavSource, clearNavSource } from '../layout/navSource';
import { observeChatArea } from '../layout/chatAreaBounds';

// Pristine window size captured before any test mutates it. Several tests set
// window.innerWidth/innerHeight to exercise the viewport clamp / fallback; the
// afterEach restore below prevents that from leaking into later innerWidth-
// sensitive tests (a real pollution bug caught 2026-08-02).
const ORIG_INNER_WIDTH = window.innerWidth;
const ORIG_INNER_HEIGHT = window.innerHeight;

function setWindowSize(w: number, h: number): void {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true });
  Object.defineProperty(window, 'innerHeight', { value: h, configurable: true, writable: true });
}

// Run rAF callbacks synchronously (double-rAF enter would otherwise never fire
// under fake timers). Each call resolves on the next microtask-ish tick.
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    return setTimeout(() => cb(performance.now()), 0) as unknown as number;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>));
});

afterEach(() => {
  cleanup();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.style.overflow = '';
  (window as unknown as { __modalLockCount?: number }).__modalLockCount = 0;
  clearNavSource(); // module-global — reset so spout tests don't cross-pollute
  setWindowSize(ORIG_INNER_WIDTH, ORIG_INNER_HEIGHT); // no test leaks window size
});

function noop() {}

describe('Modal — mount/unmount lifecycle', () => {
  it('renders content when isOpen=true', () => {
    render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.getByText('body')).toBeInTheDocument();
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();
  });

  it('renders nothing when opened false from the start', () => {
    render(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.queryByTestId('modal-scrim')).toBeNull();
  });

  it('does NOT unmount immediately on close — stays through the exit backstop, then unmounts', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    // Still mounted right after close (exit transition playing) AND visually
    // transitioning OUT — the exit target class must be applied, not just "still there".
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim).toBeInTheDocument();
    expect(scrim.className).toContain('opacity-0');

    // After the 300ms backstop it unmounts
    act(() => { vi.advanceTimersByTime(310); });
    expect(screen.queryByTestId('modal-scrim')).toBeNull();
  });

  it('applies the entered (.open) visual state after the enter rAF frames', () => {
    render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    // Enter transition target applied after rAF ticks
    act(() => { vi.advanceTimersByTime(1); });
    expect(scrim.className).toContain('opacity-100');
  });
});

describe('Modal — re-entrancy (open → close → open < backstop)', () => {
  it('cancels the pending unmount when reopened mid-exit (does not vanish)', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    act(() => { vi.advanceTimersByTime(100); }); // partway through exit

    // Reopen before the 300ms backstop fires
    rerender(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    act(() => { vi.advanceTimersByTime(300); }); // the OLD timer would have unmounted here

    // Still present — the stale unmount was cancelled
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();
  });
});

describe('Modal — Esc + scroll lock (gate on isOpen)', () => {
  it('calls onClose on Escape while open', () => {
    const onClose = vi.fn();
    render(<Modal isOpen onClose={onClose} title="T"><div>body</div></Modal>);
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT call onClose on Escape during the exit window (listener gated on isOpen)', () => {
    const onClose = vi.fn();
    const { rerender } = render(<Modal isOpen onClose={onClose} title="T"><div>body</div></Modal>);
    rerender(<Modal isOpen={false} onClose={onClose} title="T"><div>body</div></Modal>);
    // still mounted (exit playing) but Esc listener was removed with isOpen=false
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(onClose).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(310); });
  });

  it('locks body scroll while open and restores it after full unmount', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(document.body.style.overflow).toBe('hidden');

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    // lock released on isOpen=false (ref-count → 0)
    expect(document.body.style.overflow).toBe('');
    act(() => { vi.advanceTimersByTime(310); });
  });
});

describe('Modal — fullscreen mode badge', () => {
  it('renders the mode badge for size=fullscreen', () => {
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="WORKSPACE"><div>b</div></Modal>);
    const badge = screen.getByTestId('modal-mode-badge');
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toBe('WORKSPACE');
  });

  it('shows the ESC key-hint in the fullscreen header', () => {
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="EVAL"><div>b</div></Modal>);
    expect(screen.getByText('ESC')).toBeInTheDocument();
  });

  it('does NOT render a mode badge for a non-fullscreen modal (small modals unchanged)', () => {
    render(<Modal isOpen onClose={noop} title="T" size="md" mode="SHOULD_NOT_SHOW"><div>b</div></Modal>);
    expect(screen.queryByTestId('modal-mode-badge')).toBeNull();
    expect(screen.queryByText('SHOULD_NOT_SHOW')).toBeNull();
  });

  it('does NOT render a badge for fullscreen when mode is omitted', () => {
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen"><div>b</div></Modal>);
    expect(screen.queryByTestId('modal-mode-badge')).toBeNull();
  });
});

describe('Modal — fullscreen card-detail panel geometry (A11)', () => {
  it('scrim clears the leftNav + tab bar (NOT inset-0) so it never covers them', () => {
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    // fullscreen scrim is offset from the top-left, not full-viewport
    expect(scrim.style.left).toBe('150px'); // LEFT_SIDEBAR_WIDTH
    expect(scrim.style.top).toBe('80px');   // CHAT_CONTENT_TOP
    expect(scrim.className).not.toContain('inset-0');
  });

  it('fullscreen scrim is NOT overflow-hidden — the window-bounded scrim + panel geometry bound it; clipping would eat the panel shadow', () => {
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    // Overflow is prevented structurally by the clamped/window-bounded scrim rect
    // (not by clipping) so the panel's soft drop-shadow/glow stays visible
    // (Gate-2 run_0ce60215). The scrim must NOT clip.
    expect(scrim.className).not.toContain('overflow-hidden');
  });

  it('null-rect FALLBACK is window-bounded (width/height from viewport), never right:0/bottom:0 unbounded', () => {
    // No chat-area observed → Modal uses the fallback box. It must be bounded by
    // the viewport (width = innerWidth - left, height = innerHeight - top), so the
    // panel inside it can never overflow the window (the 2026-08-02 bug).
    setWindowSize(1200, 800);
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim.style.width).toBe('1050px');  // 1200 - 150 (PANEL_LEFT)
    expect(scrim.style.height).toBe('720px');  // 800 - 80 (PANEL_TOP)
    expect(scrim.style.right).toBe('');        // NOT viewport-anchored
    expect(scrim.style.bottom).toBe('');
  });

  it('null-rect FALLBACK re-bounds on window resize (stays viewport-bounded)', () => {
    // The fallback reads window size from resize-reactive state, so a resize while
    // the rect is still unobserved must re-size the scrim (not stay a stale
    // render-time snapshot). afterEach restores window size — no manual cleanup.
    setWindowSize(1200, 800);
    render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim.style.width).toBe('1050px');  // 1200 - 150 (PANEL_LEFT)

    act(() => {
      setWindowSize(900, 600);
      window.dispatchEvent(new Event('resize'));
    });

    expect(scrim.style.width).toBe('750px');   // 900 - 150 → re-bounded, not stale
    expect(scrim.style.height).toBe('520px');  // 600 - 80
  });

  it('a non-fullscreen modal keeps full-viewport inset-0 (unchanged)', () => {
    render(<Modal isOpen onClose={noop} title="T" size="md"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim.className).toContain('inset-0');
    expect(scrim.style.left).toBe(''); // no positional override
  });

  it('all width tiers are RESPONSIVE clamp(min, preferred%, max) — adapts to chat-area, no per-device hardcode', () => {
    // Widths are CSS clamp() so the panel adapts to the chat-area width on
    // laptop/large/ultrawide with NO per-device breakpoint. The % resolves (in a
    // real WKWebView) against the panel's containing block = the scrim (= chat-area
    // rect). We assert the exported contract, NOT card.style.width, because jsdom's
    // CSSOM cannot round-trip a clamp() value through element.style (it rejects it
    // → stores '') — a harness limitation, not a code issue (run_5b5c3f7d).
    expect(FS_WIDTH.s).toBe('clamp(360px, 34%, 560px)');
    expect(FS_WIDTH.m).toBe('clamp(440px, 46%, 760px)');
    expect(FS_WIDTH.l).toBe('clamp(600px, 62%, 1080px)');
    expect(FS_WIDTH.xl).toBe('clamp(760px, 70%, 1200px)');
    // every tier IS a clamp() and NONE is the old full-coverage 100% / bare px
    for (const v of Object.values(FS_WIDTH)) {
      expect(v).toMatch(/^clamp\(\d+px, \d+%, \d+px\)$/);
    }
  });

  it('xl is capped (max 1200px, preferred 70%) so a chat strip always remains — never the old full-coverage 100%', () => {
    // The regression this run fixes: xl used to be width:'100%' → the panel covered
    // the whole chat area minus a 40px gap. It is now clamp(760px, 70%, 1200px) — the
    // 1200px cap + 70% preferred leave the chat visible to the right on a wide window.
    expect(FS_WIDTH.xl).not.toBe('100%');
    const m = FS_WIDTH.xl.match(/^clamp\((\d+)px, (\d+)%, (\d+)px\)$/);
    expect(m).not.toBeNull();
    const [, min, pct, max] = m!.map(Number);
    expect(min).toBeLessThan(max);       // well-formed clamp
    expect(pct).toBeLessThan(100);       // never prefers full width
    expect(max).toBeLessThanOrEqual(1200); // hard cap so chat stays visible
    // the wiring: the fullscreen panel applies width: FS_WIDTH[fullscreenWidth]
    // (asserted structurally by the render tests above; the value contract here).
  });

  it('fullscreen panel still carries the maxWidth backstop (never exceeds the chat area)', () => {
    // maxWidth is the hard backstop RELATIVE to the scrim (= chat-area rect): scrim
    // width minus both 20px gaps. Even if a clamp min ever exceeded a narrow chat
    // area, maxWidth clamps the panel so it can never overflow (run_a95e266a).
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X" fullscreenWidth="s"><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.maxWidth).toBe('calc(100% - 40px)');
  });

  it('fullscreen scrim has NO backdrop-blur (chat behind stays legible); small dialogs KEEP the blur', () => {
    // XG: "modal 后面那个毛玻璃层...我们要看到后面的 chat window". The blur is removed
    // from the fullscreen scrim only; centered small dialogs keep it (run_5b5c3f7d).
    const fs = render(<Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>);
    const fsScrim = fs.container.querySelector('[data-testid="modal-scrim"]') as HTMLElement;
    expect(fsScrim.className).not.toContain('backdrop-blur');
    fs.unmount();

    const sm = render(<Modal isOpen onClose={noop} title="T" size="md"><div>b</div></Modal>);
    const smScrim = sm.container.querySelector('[data-testid="modal-scrim"]') as HTMLElement;
    expect(smScrim.className).toContain('backdrop-blur');
  });

  it('DEFAULT fullscreen = definite full height (bottom anchored) so flex children do not collapse', () => {
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    // definite height: bottom is anchored (Gate-2 CRITICAL fix — AutoSizer children need real height)
    expect(card.style.bottom).toBe('20px');
    expect(card.style.maxHeight).toBe(''); // NOT content-clamped in default mode
  });

  it('fullscreenAutoHeight = content-driven height clamped by maxHeight (no bottom anchor)', () => {
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X" fullscreenAutoHeight><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.bottom).toBe('');
    // maxHeight relative to the scrim (= chat-area rect): scrim height minus both gaps.
    expect(card.style.maxHeight).toBe('calc(100% - 40px)');
  });

  it('bounds the scrim to the observed chat-area rect (NOT the viewport) — never overflows the radar', () => {
    // jsdom has no ResizeObserver — stub it (observeChatArea publishes the initial
    // rect synchronously, so the RO callback need not fire for this assertion).
    const origRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {} unobserve() {} disconnect() {}
    };
    // Drive chatAreaBounds with a known rect (the message column between leftNav
    // and the dynamic-width radar). observeChatArea publishes synchronously.
    // Viewport larger than the fixture (right 1050, bottom 680) so the
    // width+height clamp in measure() is a no-op here — this test isolates the
    // "scrim = rect, not viewport" contract. The clamps themselves are covered by
    // chatAreaBounds.test.ts. Set explicitly (not leaked from a prior test) so it
    // holds under the afterEach window-size restore.
    setWindowSize(2000, 2000);
    const stub = document.createElement('div');
    stub.getBoundingClientRect = () =>
      ({ left: 150, top: 80, width: 900, height: 600, right: 1050, bottom: 680, x: 150, y: 80, toJSON: () => {} } as DOMRect);
    const stop = observeChatArea(stub);
    try {
      const { container } = render(
        <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
      );
      const scrim = container.querySelector('[data-testid="modal-scrim"]') as HTMLElement;
      // scrim uses the RECT's width/height, not viewport right:0/bottom:0
      expect(scrim.style.left).toBe('150px');
      expect(scrim.style.top).toBe('80px');
      expect(scrim.style.width).toBe('900px');
      expect(scrim.style.height).toBe('600px');
      expect(scrim.style.right).toBe(''); // not viewport-anchored anymore
    } finally {
      stop();
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = origRO;
    }
  });

  it('grows from the left (spat-out origin) — falls back to left center when no source card', () => {
    // No nav card was clicked in this render path → navSource is null → the panel
    // uses the left-center fallback transform-origin and draws NO spout.
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.transformOrigin).toBe('left center');
    expect(container.querySelector('[data-testid="modal-spout"]')).toBeNull();
  });

  it('when opened FROM a nav card, draws a spout + anchors origin at the card y', () => {
    // Simulate a nav card click publishing its position (A10Card does this).
    setNavSource({ top: 380, height: 40 } as DOMRect); // centerY = 400
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
    );
    // The spout is DRAWN when opened from a card. Its exact panel-local y is now
    // MEASURED against the panel's real getBoundingClientRect().top after layout
    // (fixes the mis-alignment from the old hardcoded-PANEL_TOP math) — jsdom has
    // no real layout (all rects are 0), so we assert presence + that a top was set,
    // not a precise px (the real-position path isn't observable in jsdom).
    const spout = container.querySelector('[data-testid="modal-spout"]') as HTMLElement;
    expect(spout).not.toBeNull();
    expect(spout.style.top).not.toBe(''); // a measured value was applied
  });

  it('computes spout Y from the UNTRANSFORMED chatRect (aligned, not the mid-transform panel rect)', () => {
    // run_9f8b6c21: the spout panel-local Y = card-center-y − chatRect.top − NUB/2,
    // read from chatAreaBounds (a ResizeObserver on the message column) — NOT from
    // panelRef.getBoundingClientRect() which, measured mid enter-transform (scale/
    // translate), returned a compressed/shifted rect → the mis-alignment bug.
    // Mutation guard: revert to the panelRef path and jsdom's all-zero rects make
    // this exact-px assertion RED.
    const origRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe() {} unobserve() {} disconnect() {}
    };
    setWindowSize(2000, 2000);
    const stub = document.createElement('div');
    stub.getBoundingClientRect = () =>
      ({ left: 150, top: 80, width: 900, height: 600, right: 1050, bottom: 680, x: 150, y: 80, toJSON: () => {} } as DOMRect);
    const stop = observeChatArea(stub);
    try {
      setNavSource({ top: 380, height: 40 } as DOMRect, '#5fc99a'); // centerY = 400
      const { container } = render(
        <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
      );
      act(() => { vi.runOnlyPendingTimers(); }); // let the double-rAF enter settle
      const spout = container.querySelector('[data-testid="modal-spout"]') as HTMLElement;
      expect(spout).not.toBeNull();
      // panel top === scrim top === chatRect.top (80). local = 400 − 80 − 20/2 = 310.
      expect(spout.style.top).toBe('310px');
    } finally {
      stop();
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = origRO;
    }
  });

  it('aligns the panel accent (--panel-accent) to the source card region tint', () => {
    // A10Card publishes rect + its region tint; the panel border/ring/spout/header
    // underline all read --panel-accent (run_5634980e).
    setNavSource({ top: 380, height: 40 } as DOMRect, '#5fc99a'); // cognition green
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.getPropertyValue('--panel-accent')).toBe('#5fc99a');
  });

  it('falls back to the theme accent (--color-primary) when the source has no tint', () => {
    setNavSource({ top: 380, height: 40 } as DOMRect); // no tint arg
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X"><div>b</div></Modal>,
    );
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.getPropertyValue('--panel-accent')).toBe('var(--color-primary)');
  });

  it('consumes the source on open — a second (non-card) open draws no spout', () => {
    setNavSource({ top: 380, height: 40 } as DOMRect);
    const first = render(<Modal isOpen onClose={noop} title="A" size="fullscreen" mode="X"><div>b</div></Modal>);
    expect(first.container.querySelector('[data-testid="modal-spout"]')).not.toBeNull();
    first.unmount();
    // second modal opens WITHOUT a fresh card click → source was consumed → no spout
    const second = render(<Modal isOpen onClose={noop} title="B" size="fullscreen" mode="Y"><div>b</div></Modal>);
    expect(second.container.querySelector('[data-testid="modal-spout"]')).toBeNull();
  });
});
