import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';

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
}

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
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const unmountTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `isRendering` keeps the modal in the DOM through its exit transition.
  // `entered` drives the `.open` visual state (opacity/transform target).
  const [isRendering, setIsRendering] = useState(isOpen);
  const [entered, setEntered] = useState(false);

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

  if (!isRendering) return null;

  return (
    <div
      ref={overlayRef}
      className={clsx(
        'fixed inset-0 z-50 flex bg-black/50 backdrop-blur-sm',
        'transition-opacity duration-[220ms] ease-out',
        entered ? 'opacity-100' : 'opacity-0',
        isFullscreen
          ? 'items-stretch'  // card is absolutely positioned via inset-6
          : 'justify-center items-center p-4',
      )}
      data-testid="modal-scrim"
      onMouseDown={(e) => {
        // Only close when clicking directly on the overlay background
        if (e.target === overlayRef.current) {
          onClose();
        }
      }}
    >
      <div
        className={clsx(
          'flex flex-col bg-[var(--color-card)] border shadow-2xl',
          'transition-[transform,opacity] duration-[260ms] ease-[cubic-bezier(.16,1,.3,1)]',
          entered ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 translate-y-[22px] scale-[0.96]',
          isFullscreen
            ? [
                // Four-edge 24px inset floating card (mockup .floater)
                'absolute inset-6 rounded-[18px] border-[var(--color-border)]',
                'shadow-[0_40px_120px_rgba(0,0,0,.65)] ring-1 ring-[rgba(110,168,254,.12)]',
              ]
            : ['w-full rounded-xl border-[var(--color-border)] max-h-[90vh]', sizeClasses[size]],
        )}
        // Stop event propagation to prevent overlay close when clicking inside modal
        onMouseDown={(e) => e.stopPropagation()}
      >
        {isFullscreen ? (
          /* Fullscreen header — mockup .fl-head: 50px, dark bg, mode badge, ESC hint */
          <div className="flex items-center gap-3 h-[50px] px-5 border-b border-[var(--color-border)] shrink-0 bg-[#0c0d12] rounded-t-[18px]">
            {mode && (
              <span
                className="font-mono text-[10px] uppercase tracking-wide text-[var(--color-accent)] bg-[rgba(110,168,254,.12)] px-2 py-[3px] rounded-md"
                data-testid="modal-mode-badge"
              >
                {mode}
              </span>
            )}
            <h2 className="font-semibold text-[15px] text-[var(--color-text)]">{title}</h2>
            <div className="ml-auto flex items-center gap-3">
              <span className="hidden sm:flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-faint)]">
                <kbd className="border border-[var(--color-border)] rounded px-1.5 py-0.5">ESC</kbd>
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
