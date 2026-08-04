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
