import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import clsx from 'clsx';
import { LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { readNavSource, clearNavSource } from '../layout/navSource';
import { readChatAreaRect, subscribeChatArea, type ChatAreaRect } from '../layout/chatAreaBounds';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | 'fullscreen';
  /**
   * Optional mono "mode" badge shown in the header — ONLY rendered in the
   * `fullscreen` branch (aligns to the layout-A10 mockup's `.fl-head .mode`).
   * Small modals ignore it. e.g. "WORKSPACE", "EVAL", "BRAIN".
   */
  mode?: string;
  /**
   * Content-adaptive WIDTH profile for the `fullscreen` card-detail panel
   * (A11, run_a4ea7a83). Each consumer declares how wide its detail content
   * prefers to be (height is governed separately by `fullscreenAutoHeight` —
   * default full chat-area height). Ignored by non-fullscreen modals. Default 'l'.
   *  s ≈ 380 · m ≈ 620 · l ≈ 880 · xl → clamped to the chat-area max.
   */
  fullscreenWidth?: 's' | 'm' | 'l' | 'xl';
  /**
   * Fullscreen HEIGHT mode. Default false = the panel fills the chat area height
   * (definite height) — REQUIRED by consumers whose content is a full-height
   * flex app (SwarmWS explorer/AutoSizer, Settings, Eval) that would collapse to
   * 0px under a shrink-to-fit parent. Set true ONLY for content-flow panels
   * (placeholders, doc-style detail) that should shrink to their content and
   * scroll past the chat-area max. Ignored by non-fullscreen modals.
   */
  fullscreenAutoHeight?: boolean;
}

// Card-detail panel geometry (A11). The panel floats INSIDE the chat area:
// left clears the leftNav, top clears the TopBar + tab bar, right/bottom keep a
// gap so the chat shows through behind a light scrim. Sourced from the shared
// LAYOUT_CONSTANTS so it never drifts from the real leftNav width / chat top.
const PANEL_LEFT = LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH; // 150
const PANEL_TOP = LAYOUT_CONSTANTS.CHAT_CONTENT_TOP;    // 80
const PANEL_GAP = 20;                                    // right/bottom breathing gap
const FS_WIDTH = { s: 380, m: 620, l: 880, xl: '100%' } as const; // xl fills to max-width

const sizeClasses = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
  fullscreen: '',  // fullscreen positions via absolute inset (see below), no max-width
};

// Exit-transition duration backstop. MUST be > the longest CSS transition below
// (card transform 260ms). We use a deterministic timeout (NOT transitionend) to
// drive unmount — transitionend fires per-property (transform AND opacity) and
// bubbles from children, which would unmount early / double-fire (Gate-1 #2).
const EXIT_MS = 300;

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  size = 'md',
  mode,
  fullscreenWidth = 'l',
  fullscreenAutoHeight = false,
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const unmountTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `isRendering` keeps the modal in the DOM through its exit transition.
  // `entered` drives the `.open` visual state (opacity/transform target).
  const [isRendering, setIsRendering] = useState(isOpen);
  const [entered, setEntered] = useState(false);
  // Spit-out origin. `spoutCenterY` = the source card's VIEWPORT center-y (as
  // captured by navSource); null = non-card open (no spout). `spoutY` = that point
  // converted to PANEL-LOCAL coords, measured against the panel's REAL top after
  // layout (not a hardcoded constant — that was the mis-alignment bug). null until
  // measured.
  const [spoutCenterY, setSpoutCenterY] = useState<number | null>(null);
  const [spoutY, setSpoutY] = useState<number | null>(null);
  // The source card's region tint — drives the panel accent (border/ring/spout/
  // header underline) so the panel reads as "spat out from THAT region".
  // null = neutral fallback (--color-primary, the theme accent).
  const [panelTint, setPanelTint] = useState<string | null>(null);
  // Live chat-message-area rect — the fullscreen panel bounds itself to THIS
  // (not the viewport), so it never overlaps the dynamic-width Radar. Subscribed
  // so radar-drag / hide / window-resize re-bounds the open panel (run_a95e266a).
  const [chatRect, setChatRect] = useState<ChatAreaRect | null>(readChatAreaRect());
  // Live window size — only consumed by the null-rect FALLBACK below (first paint
  // before ChatPage registers the chat rect). Kept reactive so a resize during that
  // transient window still yields a viewport-bounded scrim. The observed-rect path
  // reacts separately via subscribeChatArea's own resize listener.
  const [windowSize, setWindowSize] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));

  const isFullscreen = size === 'fullscreen';

  // ---- Enter / exit state machine (Gate-1 #1/#2/#3) ----
  useEffect(() => {
    // Always clear any pending unmount first — this cancels a stale exit timer
    // when isOpen flips back true mid-exit (re-entrancy, Gate-1 #1).
    if (unmountTimer.current) {
      clearTimeout(unmountTimer.current);
      unmountTimer.current = null;
    }

    if (isOpen) {
      setIsRendering(true);
      // Capture the spit-out origin at open (fullscreen only). Convert the card's
      // viewport centerY into a position inside the panel: the panel's top edge is
      // the chat-area rect's top (rect.top), so panel-local y = centerY - rect.top.
      // Offset by half the spout size (10 for the 20px nub) to center it; clamp into
      // the panel height so it never overflows. null source (non-card open) → no spout.
      if (isFullscreen) {
        const src = readNavSource();
        setChatRect(readChatAreaRect());
        if (src) {
          // Store the card's viewport center-y; the panel-local spout position is
          // measured against the panel's REAL top after layout (see layout effect).
          setSpoutCenterY(src.centerY);
          setPanelTint(src.tint ?? null);
        } else {
          setSpoutCenterY(null);
          setSpoutY(null);
          setPanelTint(null);
        }
        // Consume it: a subsequent open NOT triggered by a nav card (credential
        // banner, chat hero, deep-link) then finds null → draws no spout, instead
        // of mis-pointing at a stale card position (Gate-1 #6).
        clearNavSource();
      }
      // Mount at opacity:0, then add `.open` on a LATER frame so the browser
      // paints the initial state first and the enter transition actually plays.
      // Double-rAF: a single rAF can batch with the mount commit (Gate-1 #3).
      let raf2 = 0;
      const raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setEntered(true));
      });
      return () => {
        cancelAnimationFrame(raf1);
        if (raf2) cancelAnimationFrame(raf2);
      };
    }

    // Closing: play exit (remove `.open`), then unmount after the backstop.
    setEntered(false);
    if (isRendering) {
      unmountTimer.current = setTimeout(() => {
        setIsRendering(false);
        unmountTimer.current = null;
      }, EXIT_MS);
    }
    return undefined;
    // isRendering intentionally excluded — including it would re-arm the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // ---- Esc + body-scroll-lock — gate on isOpen (intent), NOT isRendering,
  // so a modal mid-exit doesn't keep the lock/listener alive (Gate-1 #4). ----
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEscape);

    // Ref-counted scroll lock: multiple modals (one closing while another opens)
    // must not prematurely unlock (Gate-1 #4a).
    const w = window as unknown as { __modalLockCount?: number };
    w.__modalLockCount = (w.__modalLockCount ?? 0) + 1;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handleEscape);
      w.__modalLockCount = Math.max(0, (w.__modalLockCount ?? 1) - 1);
      if (w.__modalLockCount === 0) document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  // Final cleanup: clear any timer on unmount.
  useEffect(() => {
    return () => {
      if (unmountTimer.current) clearTimeout(unmountTimer.current);
    };
  }, []);

  // Track the live chat-area rect while a fullscreen panel is open, so radar-drag /
  // hide / window-resize re-bounds it. Seed on open; unsubscribe on close. Also
  // track window size so the null-rect fallback stays viewport-bounded across a
  // resize before the chat rect is first observed.
  useEffect(() => {
    if (!isFullscreen || !isOpen) return;
    setChatRect(readChatAreaRect());
    setWindowSize({ w: window.innerWidth, h: window.innerHeight });
    const onResize = () => setWindowSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    const unsubscribe = subscribeChatArea(setChatRect);
    return () => {
      window.removeEventListener('resize', onResize);
      unsubscribe();
    };
  }, [isFullscreen, isOpen]);

  // Position the spout by MEASURING the panel's real top after layout — the card's
  // viewport center-y minus the panel's actual top = panel-local y. This avoids any
  // hardcoded-constant assumption about where the chat area starts (the source of
  // the mis-alignment). Re-runs when the panel mounts or the chat rect shifts.
  useLayoutEffect(() => {
    if (!isFullscreen || spoutCenterY == null || !panelRef.current) {
      if (spoutCenterY == null) setSpoutY(null);
      return;
    }
    const p = panelRef.current.getBoundingClientRect();
    const NUB = 20; // spout square size
    const local = spoutCenterY - p.top - NUB / 2;
    // clamp so the nub stays fully inside the panel's height
    setSpoutY(Math.max(8, Math.min(local, p.height - NUB - 8)));
  }, [isFullscreen, isOpen, spoutCenterY, chatRect, entered]);

  if (!isRendering) return null;

  return (
    <div
      ref={overlayRef}
      className={clsx(
        'z-50 bg-black/50 backdrop-blur-sm',
        'transition-opacity duration-[220ms] ease-out',
        entered ? 'opacity-100' : 'opacity-0',
        isFullscreen
          // A11 card-detail panel: the scrim covers ONLY the chat area (clears the
          // leftNav + tab bar), so leftNav/tabs are never dimmed or covered.
          // NOTE: no overflow-hidden here — the scrim is now window-bounded (its
          // rect is clamped in chatAreaBounds.measure + the fallback is
          // viewport-sized), and the panel is bounded INSIDE the scrim by
          // construction (left:20 + maxWidth:calc(100%-40) + bottom:20), so it
          // cannot overflow. overflow-hidden would clip the panel's soft drop
          // shadow/glow (80px blur extends past the scrim gap) — a visual
          // regression — for a clip that the clamp already makes unnecessary
          // (Gate-2 run_0ce60215).
          ? 'fixed'
          : 'fixed inset-0 flex justify-center items-center p-4',
      )}
      style={
        isFullscreen
          // Scrim covers ONLY the live chat-message area (not the viewport), so it
          // never dims/covers the Radar sidebar or the leftNav (run_a95e266a).
          // Falls back to a WINDOW-BOUNDED box if the rect isn't observed yet (first
          // paint before ChatPage registers): width/height from the viewport, NOT
          // right:0/bottom:0 — an unbounded fallback let the panel overflow the
          // window right+bottom (2026-08-02). width/height come from `windowSize`
          // state (resize-reactive), so this fallback re-bounds on a resize too.
          // Both the observed rect (clamped in chatAreaBounds.measure) and this
          // fallback stay within the viewport.
          ? (chatRect
              ? { left: chatRect.left, top: chatRect.top, width: chatRect.width, height: chatRect.height, background: 'rgba(0,0,0,0.35)' }
              : {
                  left: PANEL_LEFT,
                  top: PANEL_TOP,
                  width: Math.max(0, windowSize.w - PANEL_LEFT),
                  height: Math.max(0, windowSize.h - PANEL_TOP),
                  background: 'rgba(0,0,0,0.35)',
                })
          : undefined
      }
      data-testid="modal-scrim"
      onMouseDown={(e) => {
        // Only close when clicking directly on the overlay background
        if (e.target === overlayRef.current) {
          onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        className={clsx(
          'flex flex-col bg-[var(--color-card)] border shadow-2xl',
          'transition-[transform,opacity] duration-[280ms] ease-[cubic-bezier(.16,1,.3,1)]',
          isFullscreen
            ? [
                // Floating card-detail panel, anchored top-left of the chat area,
                // grows toward bottom-right. transform-origin is set inline to the
                // source card's y (spoutY) so it looks "spat out" from that card.
                // Border + ring align to the source region's accent (--panel-accent).
                'absolute rounded-[18px]',
                'border-[color-mix(in_srgb,var(--panel-accent)_45%,var(--color-border))]',
                'shadow-[-8px_24px_80px_rgba(0,0,0,.6)] ring-1 ring-[color-mix(in_srgb,var(--panel-accent)_18%,transparent)]',
                entered ? 'opacity-100 translate-x-0 scale-100' : 'opacity-0 -translate-x-[34px] scale-[0.9]',
              ]
            : [
                'w-full rounded-xl border-[var(--color-border)] max-h-[90vh]',
                entered ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 translate-y-[22px] scale-[0.96]',
                sizeClasses[size],
              ],
        )}
        style={
          isFullscreen
            ? {
                // The scrim IS the chat-area rect, so the panel positions RELATIVE
                // to it: a LEFT gap clears the leftNav edge (spout drawn here), and
                // maxWidth = the scrim's own width minus both gaps — so the panel can
                // NEVER exceed the chat area, whatever the window/radar size.
                left: PANEL_GAP,
                top: 0,
                width: FS_WIDTH[fullscreenWidth],
                minWidth: 320,
                maxWidth: `calc(100% - ${PANEL_GAP * 2}px)`,
                // spit-out from the source card's y (falls back to left-center)
                transformOrigin: spoutY != null ? `left ${spoutY + 10}px` : 'left center',
                // region accent — border/ring/spout/underline align to this.
                // Fallback = --color-primary (the theme accent). NOTE: the pre-
                // existing header used var(--color-accent) which is UNDEFINED in
                // index.css (only --color-primary exists) — that made the old
                // header underline/badge silently colorless; fixed here (R7).
                ['--panel-accent' as string]: panelTint ?? 'var(--color-primary)',
                // HEIGHT: default = DEFINITE full chat-area height (bottom anchored),
                // so full-height flex children (AutoSizer/explorer/dashboards) get a
                // real height and don't collapse to 0. autoHeight = content-driven,
                // clamped by max, for doc-flow panels.
                // heights are relative to the scrim (= chat-area rect): default
                // fills to a bottom gap; autoHeight clamps to the scrim height − gaps.
                ...(fullscreenAutoHeight
                  ? { maxHeight: `calc(100% - ${PANEL_GAP * 2}px)` }
                  : { bottom: PANEL_GAP }),
              }
            : undefined
        }
        // Stop event propagation to prevent overlay close when clicking inside modal
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Spout — a clear region-colored arrow drawn in the LEFT gap, pointing
            back at the nav card that opened this panel. A 20px square rotated 45°
            straddling the panel's left edge; only its left+bottom sides carry the
            accent border → reads as a triangle emerging from the gap toward the
            leftNav. Panel-colored fill hides the two inner sides. (run_5634980e) */}
        {isFullscreen && spoutY != null && (
          <div
            data-testid="modal-spout"
            aria-hidden
            className="absolute w-[20px] h-[20px] rotate-45 rounded-bl-[5px] bg-[var(--color-card)] z-[1]
              border-l-[1.5px] border-b-[1.5px] border-[var(--panel-accent)]
              shadow-[-3px_3px_8px_rgba(0,0,0,.35)]"
            style={{ left: -11, top: spoutY }}
          />
        )}
        {isFullscreen ? (
          /* Fullscreen header — 50px, subtle gradient + accent underline, mode
             badge, ESC hint. Theme-variable driven (no hardcoded hex). */
          <div className="relative flex items-center gap-3 h-[50px] px-5 shrink-0 rounded-t-[18px] bg-gradient-to-b from-[var(--color-bg-chrome)] to-[var(--color-card)] border-b border-[var(--color-border)] before:absolute before:inset-x-0 before:bottom-0 before:h-px before:bg-gradient-to-r before:from-transparent before:via-[var(--panel-accent)] before:to-transparent before:opacity-50">
            {mode && (
              <span
                className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--panel-accent)] bg-[color-mix(in_srgb,var(--panel-accent)_14%,transparent)] ring-1 ring-[color-mix(in_srgb,var(--panel-accent)_25%,transparent)] px-2 py-[3px] rounded-md"
                data-testid="modal-mode-badge"
              >
                {mode}
              </span>
            )}
            <h2 className="font-semibold text-[15px] tracking-tight text-[var(--color-text)]">{title}</h2>
            <div className="ml-auto flex items-center gap-3">
              <span className="hidden sm:flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-faint)]">
                <kbd className="border border-[var(--color-border)] rounded px-1.5 py-0.5 bg-[var(--color-bg)]">ESC</kbd>
                to close
              </span>
              <button
                onClick={onClose}
                className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                aria-label="Close"
              >
                <span className="material-symbols-outlined">close</span>
              </button>
            </div>
          </div>
        ) : (
          /* Non-fullscreen header — UNCHANGED (small modals unaffected, decision A) */
          <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
            <h2 className="text-lg font-semibold text-[var(--color-text)]">{title}</h2>
            <button
              onClick={onClose}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        )}

        {/* Content — fills remaining height; fullscreen delegates scroll to children.
            overflow-hidden is REQUIRED: it hard-clips to the card and gives the child
            a definite height to resolve h-full/flex-1 against. Without it the child
            expands to natural height, overflows below the card, and the inner scroll
            container never engages (content cut off at bottom). Body padding for
            fullscreen matches mockup .fl-body (22px/26px). */}
        <div
          className={clsx(
            'flex-1 min-h-0',
            isFullscreen ? 'flex flex-col overflow-hidden' : 'overflow-y-auto p-6',
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
