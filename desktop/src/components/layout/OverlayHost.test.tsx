/**
 * OverlayHost tests — the geometry CONTRACT that makes the app-zoom!=1 double-count
 * structurally impossible (OverlayHost subsystem, M2, run_fdeaead8).
 *
 * jsdom has NO layout engine, so these lock the *contract* (scrim is `absolute inset:0`
 * of the relative MainContentArea, reads ZERO measured window/rect coords — unlike the
 * legacy `fixed` + chatAreaBounds path). The PIXEL correctness at zoom=1.1 is proven by
 * a LIVE render (build:all + relaunch), which jsdom cannot substitute for — that live
 * gate is the DoD, this suite is the structural regression guard.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { useEffect } from 'react';
import { render, screen, act, cleanup } from '@testing-library/react';
import { OverlayProvider, useOverlay } from '../../contexts/OverlayContext';
import { OverlayHost } from './OverlayHost';
import { registerOverlay } from './overlayRegistry';

// Register a throwaway test surface (does not touch the real registry entries beyond
// adding one id; getOverlaySpec is a Map so this is isolated per id).
registerOverlay({
  id: '__test_surface__',
  title: 'Test Surface',
  mode: 'TEST',
  width: 'm',
  render: () => <div data-testid="test-surface-content">hello</div>,
});

function Harness() {
  const { openOverlay, closeOverlay } = useOverlay();
  return (
    <div>
      <button data-testid="open" onClick={() => openOverlay('__test_surface__')}>open</button>
      <button data-testid="close" onClick={() => closeOverlay()}>close</button>
      <OverlayHost />
    </div>
  );
}

function renderHost() {
  return render(<OverlayProvider><Harness /></OverlayProvider>);
}

afterEach(cleanup);

describe('OverlayHost — mount/unmount driven by activeOverlay', () => {
  it('renders nothing when no surface is active', () => {
    renderHost();
    expect(screen.queryByTestId('overlay-host-scrim')).toBeNull();
  });

  it('renders the registered surface content when opened', () => {
    renderHost();
    act(() => screen.getByTestId('open').click());
    expect(screen.getByTestId('overlay-host-scrim')).toBeInTheDocument();
    expect(screen.getByTestId('test-surface-content')).toBeInTheDocument();
    expect(screen.getByTestId('overlay-host-mode-badge').textContent).toBe('TEST');
  });
});

describe('OverlayHost — fresh mount per open (replaces NewBrain reset-on-reopen hack)', () => {
  // A surface that carries local state; the host must give it a FRESH instance each
  // open (unmount on close → remount on reopen), so no state leaks across opens.
  let mounts = 0;
  function StatefulProbe() {
    // useEffect([]) fires exactly ONCE per real mount — the honest remount probe
    // (a render-body counter would over-count React's re-renders).
    useEffect(() => { mounts += 1; }, []);
    return <div data-testid="stateful-content" />;
  }
  registerOverlay({ id: '__statefulsurface__', title: 'Stateful', render: () => <StatefulProbe /> });

  it('mounts a FRESH surface instance on each open (state cannot leak across opens)', () => {
    vi.useFakeTimers();
    mounts = 0;
    function H() {
      const { openOverlay, closeOverlay } = useOverlay();
      return (
        <div>
          <button data-testid="o" onClick={() => openOverlay('__statefulsurface__')}>o</button>
          <button data-testid="c" onClick={() => closeOverlay()}>c</button>
          <OverlayHost />
        </div>
      );
    }
    render(<OverlayProvider><H /></OverlayProvider>);
    act(() => screen.getByTestId('o').click());
    expect(mounts).toBe(1);
    // Full close→exit→reopen (advance past EXIT_MS so the deferred unmount fires).
    act(() => screen.getByTestId('c').click());
    act(() => { vi.advanceTimersByTime(400); });
    act(() => screen.getByTestId('o').click());
    // A SECOND real mount → fresh instance, no leaked state (the #4b guarantee, now
    // structural via the host lifecycle rather than a per-surface reset hack).
    expect(mounts).toBe(2);
    vi.useRealTimers();
  });
});

describe('OverlayHost — the zoom-safe geometry contract (D5 killed)', () => {
  it('scrim is `absolute inset-0` — NOT `fixed`, and writes NO measured left/top/width/height', () => {
    renderHost();
    act(() => screen.getByTestId('open').click());
    const scrim = screen.getByTestId('overlay-host-scrim');
    // absolute inset-0 of the relative MainContentArea (the whole point: no viewport
    // math). The legacy path was `fixed` + inline chatRect px — that is what
    // double-counted under <html zoom>. This asserts the class is gone.
    expect(scrim.className).toContain('absolute');
    expect(scrim.className).toContain('inset-0');
    expect(scrim.className).not.toContain('fixed');
    // NO measured geometry written inline (only the scrim background tint is inline).
    expect(scrim.style.left).toBe('');
    expect(scrim.style.top).toBe('');
    expect(scrim.style.width).toBe('');
    expect(scrim.style.height).toBe('');
  });

  it('scrim is NOT overflow-hidden (panel 80px shadow must not be clipped)', () => {
    renderHost();
    act(() => screen.getByTestId('open').click());
    const scrim = screen.getByTestId('overlay-host-scrim');
    expect(scrim.className).not.toContain('overflow-hidden');
  });

  it('panel carries the maxWidth backstop relative to the scrim (never exceeds chat area)', () => {
    renderHost();
    act(() => screen.getByTestId('open').click());
    const panel = screen.getByTestId('overlay-host-scrim').querySelector(':scope > div') as HTMLElement;
    expect(panel.style.maxWidth).toBe('calc(100% - 40px)');
    // panel is anchored left (spout gap) + bottom (definite-height default), NOT via
    // any measured viewport coord.
    expect(panel.style.left).toBe('20px');
    expect(panel.style.top).toBe('0px');
  });
});

describe('OverlayHost — spout Y is corrected for app zoom (the ×invZoom conversion)', () => {
  // getBoundingClientRect returns ZOOM-SCALED (visual) px under `<html style.zoom=Z>`
  // (verified: TerminalPanel.tsx counter-zoom comment — the rect is scaled, layout px
  // is not). The spout `top` is a CSS layout value re-scaled by that same zoom at paint,
  // so a raw visual-px difference lands at distance×Z. These tests lock the fix: every
  // grBCR-derived value (card center, scrim top, scrim height→panelHeight) is multiplied
  // by --app-zoom-inv (=1/Z, published inline on <html> by useZoom.applyZoom) to convert
  // visual px → layout px BEFORE computing spoutY, so the CSS top lands correctly.
  const origRect = Element.prototype.getBoundingClientRect;
  afterEach(() => {
    Element.prototype.getBoundingClientRect = origRect;
    document.documentElement.style.removeProperty('--app-zoom-inv');
    cleanup();
  });

  registerOverlay({
    id: '__spout_surface__',
    title: 'Spout Surface',
    sourceCardTestId: 'nav-spout-card',
    render: () => <div data-testid="spout-content" />,
  });

  function mkRect(top: number, height: number): DOMRect {
    return { top, height, left: 0, right: 0, bottom: top + height, width: 0, x: 0, y: top, toJSON() {} } as DOMRect;
  }

  function SpoutHarness() {
    const { openOverlay } = useOverlay();
    return (
      <div>
        <div data-testid="nav-spout-card" />
        <button data-testid="open-spout" onClick={() => openOverlay('__spout_surface__')}>o</button>
        <OverlayHost />
      </div>
    );
  }

  it('converts the visual-px card/scrim reads to layout px (uncapped case)', () => {
    // zoom 0.8 → invZoom 1.25. Card center visual = 80 + 40/2 = 100; scrim top visual = 40.
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const tid = this.getAttribute?.('data-testid');
      if (tid === 'nav-spout-card') return mkRect(80, 40);
      if (tid === 'overlay-host-scrim') return mkRect(40, 800);
      return mkRect(0, 0);
    };
    document.documentElement.style.setProperty('--app-zoom-inv', '1.25');
    render(<OverlayProvider><SpoutHarness /></OverlayProvider>);
    act(() => screen.getByTestId('open-spout').click());
    const spout = screen.getByTestId('overlay-host-spout');
    // corrected: centerY 100×1.25=125, scrimTop 40×1.25=50, local = 125−50−NUB/2(10) = 65.
    // panelHeight = 800×1.25−20 = 980; clamp[8, 980−20−8=952] → 65 (uncapped).
    // (WITHOUT the fix, current code = 100−40−10 = 50 → this test is RED on HEAD.)
    expect(spout.style.top).toBe('65px');
  });

  it('clamps against an invZoom-corrected panelHeight (same coordinate space)', () => {
    // zoom 2.0 → invZoom 0.5. Forces the upper clamp; proves scrim HEIGHT is also converted.
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const tid = this.getAttribute?.('data-testid');
      if (tid === 'nav-spout-card') return mkRect(2000, 0);
      if (tid === 'overlay-host-scrim') return mkRect(40, 400);
      return mkRect(0, 0);
    };
    document.documentElement.style.setProperty('--app-zoom-inv', '0.5');
    render(<OverlayProvider><SpoutHarness /></OverlayProvider>);
    act(() => screen.getByTestId('open-spout').click());
    const spout = screen.getByTestId('overlay-host-spout');
    // corrected: center 2000×0.5=1000, scrimTop 40×0.5=20, local = 1000−20−10 = 970.
    // panelHeight = 400×0.5−20 = 180; upper = 180−20−8 = 152 → clamp → 152.
    // (If panelHeight were left in visual px: 400−20=380, upper 352 → would be 352 — the
    //  mixed-space clamp bug Gate-1 caught. This asserts height is corrected too.)
    expect(spout.style.top).toBe('152px');
  });

  it('is a no-op at zoom=100% (invZoom=1) — no regression at default zoom', () => {
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const tid = this.getAttribute?.('data-testid');
      if (tid === 'nav-spout-card') return mkRect(80, 40);
      if (tid === 'overlay-host-scrim') return mkRect(40, 800);
      return mkRect(0, 0);
    };
    document.documentElement.style.setProperty('--app-zoom-inv', '1');
    render(<OverlayProvider><SpoutHarness /></OverlayProvider>);
    act(() => screen.getByTestId('open-spout').click());
    const spout = screen.getByTestId('overlay-host-spout');
    // invZoom=1 → local = 100−40−10 = 50 (identical to pre-fix behaviour at zoom 1).
    expect(spout.style.top).toBe('50px');
  });
});
