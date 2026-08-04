/**
 * OverlayHost — the single mounting + geometry authority for every fullscreen surface
 * (OverlayHost subsystem, design 2026-08-04). Rendered ONCE inside the `relative`
 * MainContentArea (ThreeColumnLayout MainChatPanel). Reads `activeOverlay` from
 * OverlayContext, looks up the registered OverlaySpec, and wraps the spec's content in
 * the scrim + panel + header + spout chrome.
 *
 * ── Why this kills the zoom bug at the root (D5) ─────────────────────────────────────
 * The legacy fullscreen path lived in Modal: `position: fixed` + a rect measured by
 * `chatAreaBounds` (getBoundingClientRect, post-zoom px) written back as INLINE px on
 * the fixed scrim. Under `<html style.zoom=Z>` WebKit multiplied those px by Z again
 * → the 6-times-patched overflow double-count. OverlayHost instead uses
 * `position: absolute; inset: 0` of the in-flow MainContentArea. `inset:0` reads NO
 * coordinate — the browser sizes the scrim to the parent's content box, and CSS `zoom`
 * scales parent+child uniformly. There is no measured px to double-count, so the bug
 * is STRUCTURALLY impossible, not patched. `chatAreaBounds` is deleted (M5).
 *
 * ── Spout geometry (zoom-safe) ──────────────────────────────────────────────────────
 * The spout points at the LeftNav card that opened the surface — a card OUTSIDE the
 * host. Both the card rect and the host rect are read via getBoundingClientRect at open
 * (SAME post-zoom coordinate space), and the spout's panel-local Y is their DIFFERENCE
 * (hostRect.top − cardCenterY), so the zoom factor cancels in the subtraction. No
 * `--app-zoom-inv` math needed — the cancellation is inherent to same-space subtraction.
 *
 * Chrome (enter/exit state machine, Esc, ref-counted scroll-lock, header, spout nub) is
 * ported faithfully from Modal's fullscreen branch — only the POSITIONING model changes.
 */
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { useOverlay } from '../../contexts/OverlayContext';
import type { OverlayId } from './overlayIds';
import { getOverlaySpec, getOverlayCtxBridge } from './overlayRegistry';

// Responsive width tiers — CSS clamp(min, preferred%, max), resolved by the browser
// against the panel's containing block (the scrim = MainContentArea). Ported verbatim
// from Modal.FS_WIDTH so migrated surfaces keep identical widths.
export const OVERLAY_WIDTH = {
  s: 'clamp(360px, 34%, 560px)',
  m: 'clamp(440px, 46%, 760px)',
  l: 'clamp(600px, 62%, 1080px)',
  xl: 'clamp(760px, 70%, 1200px)',
} as const;

const PANEL_GAP = 20;   // left/right/bottom breathing gap inside the scrim
const NUB = 20;         // spout square size
const EXIT_MS = 300;    // exit-transition backstop (> the 280ms card transform)

export function OverlayHost() {
  const { activeOverlay, closeOverlay, agentId } = useOverlay();
  const spec = getOverlaySpec(activeOverlay);

  const scrimRef = useRef<HTMLDivElement>(null);
  const unmountTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `renderedId` keeps the surface mounted through its exit transition; `entered`
  // drives the .open visual state. Mirrors Modal's isRendering/entered machine.
  const [renderedId, setRenderedId] = useState<OverlayId | null>(activeOverlay);
  const [entered, setEntered] = useState(false);
  // The source card's viewport center-y, captured at open (null = no spout).
  const [spoutCenterY, setSpoutCenterY] = useState<number | null>(null);
  const [spoutY, setSpoutY] = useState<number | null>(null);
  const [tint, setTint] = useState<string | null>(null);

  const isOpen = activeOverlay != null;

  // ---- Enter / exit state machine (ported from Modal) ----
  useEffect(() => {
    if (unmountTimer.current) {
      clearTimeout(unmountTimer.current);
      unmountTimer.current = null;
    }

    if (isOpen && spec) {
      setRenderedId(activeOverlay);
      // Capture the spout origin: re-derive the source card's live rect from its
      // testid (replaces navSource — D4). Same-space as the host rect (read in the
      // layout effect below), so zoom cancels in the subtraction.
      if (spec.sourceCardTestId) {
        const card = document.querySelector<HTMLElement>(`[data-testid="${spec.sourceCardTestId}"]`);
        if (card) {
          const r = card.getBoundingClientRect();
          setSpoutCenterY(r.top + r.height / 2);
        } else {
          setSpoutCenterY(null);
        }
      } else {
        setSpoutCenterY(null);
        setSpoutY(null);
      }
      setTint(spec.tint ?? null);

      let raf2 = 0;
      const raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setEntered(true));
      });
      return () => {
        cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
      };
    }

    // Closing: play exit, then unmount after the backstop.
    setEntered(false);
    if (renderedId != null) {
      unmountTimer.current = setTimeout(() => {
        setRenderedId(null);
        unmountTimer.current = null;
      }, EXIT_MS);
    }
    return undefined;
    // renderedId excluded — including it would re-arm the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOverlay]);

  // ---- Esc + ref-counted body-scroll-lock (gate on isOpen, ported from Modal) ----
  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeOverlay();
    };
    document.addEventListener('keydown', handleEscape);
    const w = window as unknown as { __modalLockCount?: number };
    w.__modalLockCount = (w.__modalLockCount ?? 0) + 1;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleEscape);
      w.__modalLockCount = Math.max(0, (w.__modalLockCount ?? 1) - 1);
      if (w.__modalLockCount === 0) document.body.style.overflow = '';
    };
  }, [isOpen, closeOverlay]);

  useEffect(() => () => { if (unmountTimer.current) clearTimeout(unmountTimer.current); }, []);

  // Spout panel-local Y: (source card center-y) − (host/scrim top). Both are
  // getBoundingClientRect reads in the SAME post-zoom space, so their difference is
  // zoom-invariant — no --app-zoom-inv needed. The scrim IS the panel's positioning
  // parent and the panel is top:0 inside it, so scrim.top == panel.top.
  useLayoutEffect(() => {
    if (!isOpen || spoutCenterY == null) {
      if (spoutCenterY == null) setSpoutY(null);
      return;
    }
    const scrim = scrimRef.current;
    if (!scrim) return;
    const scrimRect = scrim.getBoundingClientRect();
    const panelHeight = Math.max(0, scrimRect.height - PANEL_GAP);
    const local = spoutCenterY - scrimRect.top - NUB / 2;
    setSpoutY(Math.max(8, Math.min(local, panelHeight - NUB - 8)));
  }, [isOpen, spoutCenterY, renderedId]);

  if (renderedId == null) return null;
  // During the exit transition activeOverlay is already null but we still render the
  // last surface; fall back to the last spec for content until unmount.
  const renderSpec = spec ?? getOverlaySpec(renderedId);
  if (!renderSpec) return null;

  const width = OVERLAY_WIDTH[renderSpec.width ?? 'l'];

  return (
    <div
      ref={scrimRef}
      data-testid="overlay-host-scrim"
      className={clsx(
        // absolute inset:0 of the relative MainContentArea — covers ONLY the chat
        // content area (leftNav is outside this parent), zero measured coords.
        // NO overflow-hidden: the panel is bounded inside by construction (left/right/
        // bottom gaps + maxWidth), so its 80px drop-shadow stays visible (the shadow-
        // clip constraint Modal.tsx documents).
        'absolute inset-0 z-50',
        'transition-opacity duration-[220ms] ease-out',
        entered ? 'opacity-100' : 'opacity-0',
      )}
      style={{ background: 'rgba(0,0,0,0.15)' }}
      onMouseDown={(e) => { if (e.target === scrimRef.current) closeOverlay(); }}
    >
      <div
        className={clsx(
          'absolute rounded-[18px] flex flex-col bg-[var(--color-card)] border shadow-2xl',
          'transition-[transform,opacity] duration-[280ms] ease-[cubic-bezier(.16,1,.3,1)]',
          'border-[color-mix(in_srgb,var(--panel-accent)_45%,var(--color-border))]',
          'shadow-[-8px_24px_80px_rgba(0,0,0,.6)] ring-1 ring-[color-mix(in_srgb,var(--panel-accent)_18%,transparent)]',
          entered ? 'opacity-100 translate-x-0 scale-100' : 'opacity-0 -translate-x-[34px] scale-[0.9]',
        )}
        style={{
          left: PANEL_GAP,
          top: 0,
          width,
          minWidth: 320,
          maxWidth: `calc(100% - ${PANEL_GAP * 2}px)`,
          transformOrigin: spoutY != null ? `left ${spoutY + 10}px` : 'left center',
          ['--panel-accent' as string]: tint ?? 'var(--color-primary)',
          ...(renderSpec.autoHeight
            ? { maxHeight: `calc(100% - ${PANEL_GAP * 2}px)` }
            : { bottom: PANEL_GAP }),
        }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {spoutY != null && (
          <div
            data-testid="overlay-host-spout"
            aria-hidden
            className="absolute w-[20px] h-[20px] rotate-45 rounded-bl-[5px] bg-[var(--color-card)] z-[1]
              border-l-[1.5px] border-b-[1.5px] border-[var(--panel-accent)]
              shadow-[-3px_3px_8px_rgba(0,0,0,.35)]"
            style={{ left: -11, top: spoutY }}
          />
        )}
        {/* Header — ported from Modal fullscreen branch */}
        <div className="relative flex items-center gap-3 h-[50px] px-5 shrink-0 rounded-t-[18px] bg-gradient-to-b from-[var(--color-bg-chrome)] to-[var(--color-card)] border-b border-[var(--color-border)] before:absolute before:inset-x-0 before:bottom-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-[var(--panel-accent)] before:to-transparent before:opacity-50">
          {renderSpec.mode && (
            <span
              className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--panel-accent)] bg-[color-mix(in_srgb,var(--panel-accent)_14%,transparent)] ring-1 ring-[color-mix(in_srgb,var(--panel-accent)_25%,transparent)] px-2 py-[3px] rounded-md"
              data-testid="overlay-host-mode-badge"
            >
              {renderSpec.mode}
            </span>
          )}
          <h2 className="font-semibold text-[15px] tracking-tight text-[var(--color-text)]">{renderSpec.title}</h2>
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden sm:flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-faint)]">
              <kbd className="border border-[var(--color-border)] rounded px-1.5 py-0.5 bg-[var(--color-bg)]">ESC</kbd>
              to close
            </span>
            <button
              onClick={closeOverlay}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              aria-label="Close"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>
        {/* Content — the registry render fn; host provides definite-height clip.
            ctx = host-owned close + ChatPage-owned tab handlers (via the bridge).
            Suspense boundary is REQUIRED: G2 lazy()-splits the heavy surfaces
            (BrainHub/Settings/Eval), and a lazy component rendered without a boundary
            throws "suspended on synchronous input" (run_06c49540, Gate 1). Eager
            surfaces never suspend, so the fallback only shows during a lazy chunk fetch. */}
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <Suspense
            fallback={
              <div
                className="flex-1 flex items-center justify-center text-sm text-[var(--color-text-muted)]"
                data-testid="overlay-loading"
              >
                Loading…
              </div>
            }
          >
            {/* ctx = host-owned close + reactive agentId (from OverlayContext, so an
                open surface re-renders on agent switch) + ref-backed dispatch handlers
                (from the non-reactive module bridge — stable, never stale). */}
            {renderSpec.render({ close: closeOverlay, agentId, ...getOverlayCtxBridge() })}
          </Suspense>
        </div>
      </div>
    </div>
  );
}
