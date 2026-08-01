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
import Modal from './Modal';
import { setNavSource, clearNavSource } from '../layout/navSource';
import { observeChatArea } from '../layout/chatAreaBounds';

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

  it('a non-fullscreen modal keeps full-viewport inset-0 (unchanged)', () => {
    render(<Modal isOpen onClose={noop} title="T" size="md"><div>b</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim.className).toContain('inset-0');
    expect(scrim.style.left).toBe(''); // no positional override
  });

  it('applies the content-adaptive width for the declared fullscreenWidth profile', () => {
    const { container } = render(
      <Modal isOpen onClose={noop} title="T" size="fullscreen" mode="X" fullscreenWidth="s"><div>b</div></Modal>,
    );
    // the card is the scrim's child div carrying the width style
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    expect(card.style.width).toBe('380px'); // FS_WIDTH.s
    // maxWidth is now RELATIVE to the scrim (= the chat-area rect): scrim width
    // minus both gaps. The panel can never exceed the chat area whatever the
    // window/radar size — the scrim, not the viewport, is the bound (run_a95e266a).
    expect(card.style.maxWidth).toBe('calc(100% - 40px)');
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
    const stub = document.createElement('div');
    stub.getBoundingClientRect = () =>
      ({ left: 150, top: 80, width: 900, height: 700, right: 1050, bottom: 780, x: 150, y: 80, toJSON: () => {} } as DOMRect);
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
      expect(scrim.style.height).toBe('700px');
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
    const spout = container.querySelector('[data-testid="modal-spout"]') as HTMLElement;
    expect(spout).not.toBeNull();
    // panel-local y = centerY(400) - panelTop(80, fallback) - 10 = 310 → spout top:310px
    // (20px nub → offset 10; no chatRect observed in the test → falls back to PANEL_TOP)
    expect(spout.style.top).toBe('310px');
    const card = container.querySelector('[data-testid="modal-scrim"] > div') as HTMLElement;
    // origin follows the source: left {spoutY + 10}px = 320px
    expect(card.style.transformOrigin).toBe('left 320px');
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
