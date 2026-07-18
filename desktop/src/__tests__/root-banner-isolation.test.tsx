/**
 * CLASS-DEFENSE (defense-in-depth): a crash in one App-root passive banner must
 * degrade to that banner disappearing — NOT take down the whole app.
 *
 * App.tsx wraps each passive global banner (CredentialBanner, BackendUpgradeBanner,
 * UpdateNotification) in its own <ErrorBoundary fallback={null}>. This test proves
 * the isolation contract that wrap relies on: a throwing child renders as null and
 * its SIBLINGS keep rendering (the crash does not propagate up to the app-level
 * boundary that would show a full-screen "Something went wrong").
 *
 * This is the forced-execution companion (R28) to the static source-scan in
 * root-mounted-no-shell-context.test.ts: the static test PREVENTS the known trigger
 * (useLayout at root); this test guarantees the BLAST RADIUS is contained even if a
 * banner crashes for some other reason.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ErrorBoundary } from '../components/common/ErrorBoundary';

function Boom(): React.ReactElement {
  throw new Error('banner exploded at render');
}

describe('root banner isolation — a crashing banner does not take down siblings', () => {
  it('a throwing child in <ErrorBoundary fallback={null}> renders null and siblings survive', () => {
    // Silence the intentional React error-boundary console.error for this render.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <div>
          <ErrorBoundary fallback={null}>
            <Boom />
          </ErrorBoundary>
          <div data-testid="sibling">still here</div>
        </div>,
      );
      // The crashing banner degraded to null...
      expect(screen.queryByText('banner exploded at render')).toBeNull();
      // ...and the sibling (the rest of the app) is unaffected.
      expect(screen.getByTestId('sibling')).toBeTruthy();
      // ...and the app-level full-screen fallback did NOT fire.
      expect(screen.queryByText('Something went wrong')).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });

  it('componentDidCatch logs the crash (the null fallback is observable, not silent)', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <ErrorBoundary fallback={null}>
          <Boom />
        </ErrorBoundary>,
      );
      // ErrorBoundary.componentDidCatch console.errors — so a swallowed banner
      // crash still leaves a trace for debugging (not a silent disappearance).
      expect(spy).toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });

  // Footgun guard: `'fallback' in props` must NOT treat an EXPLICIT
  // `fallback={undefined}` as "render nothing" — otherwise a default-variant
  // caller writing `fallback={cond ? <X/> : undefined}` would, on error, silently
  // render empty and SWALLOW the crash to a blank screen. undefined === "no
  // fallback" (identical to omitting the prop → full-screen ErrorFallback); only
  // null / a real node is a deliberate render instruction.
  it('fallback={undefined} falls through to the full-screen ErrorFallback (not blank)', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <ErrorBoundary fallback={undefined}>
          <Boom />
        </ErrorBoundary>,
      );
      // Must escalate to the visible error UI, NOT swallow to empty.
      expect(screen.getByText('Something went wrong')).toBeTruthy();
    } finally {
      spy.mockRestore();
    }
  });

  it('fallback={null} still renders nothing (banner-isolation contract preserved)', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      render(
        <ErrorBoundary fallback={null}>
          <Boom />
        </ErrorBoundary>,
      );
      // null is a deliberate render-nothing — must NOT fall through to ErrorFallback.
      expect(screen.queryByText('Something went wrong')).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
});
