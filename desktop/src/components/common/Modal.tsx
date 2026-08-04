import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl';
}

// The `fullscreen` size + its card-detail geometry (spout / chat-area-bounded scrim /
// navSource / chatAreaBounds) was RETIRED 2026-08-04 (M5): every fullscreen surface
// now renders through the OverlayHost subsystem (contexts/OverlayContext +
// components/layout/OverlayHost), which owns geometry via `absolute inset:0` of the
// in-flow chat area — no viewport measurement, so the app-zoom double-count is
// structurally impossible. Modal is now ONLY the small centered dialog.

const sizeClasses = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  '2xl': 'max-w-2xl',
  '3xl': 'max-w-3xl',
};

// Exit-transition duration backstop. MUST be > the longest CSS transition below
// (card transform 280ms). Deterministic timeout (NOT transitionend) drives unmount —
// transitionend fires per-property + bubbles from children → early/double unmount.
const EXIT_MS = 300;

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  size = 'md',
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const unmountTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `isRendering` keeps the modal in the DOM through its exit transition.
  // `entered` drives the `.open` visual state (opacity/transform target).
  const [isRendering, setIsRendering] = useState(isOpen);
  const [entered, setEntered] = useState(false);

  // ---- Enter / exit state machine (Gate-1 #1/#2/#3) ----
  useEffect(() => {
    // Always clear any pending unmount first — cancels a stale exit timer when
    // isOpen flips back true mid-exit (re-entrancy, Gate-1 #1).
    if (unmountTimer.current) {
      clearTimeout(unmountTimer.current);
      unmountTimer.current = null;
    }

    if (isOpen) {
      setIsRendering(true);
      // Mount at opacity:0, then add `.open` on a LATER frame so the browser paints
      // the initial state first and the enter transition actually plays. Double-rAF:
      // a single rAF can batch with the mount commit (Gate-1 #3).
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
        'z-50 fixed inset-0 flex justify-center items-center p-4 bg-black/50 backdrop-blur-sm',
        'transition-opacity duration-[220ms] ease-out',
        entered ? 'opacity-100' : 'opacity-0',
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
          'transition-[transform,opacity] duration-[280ms] ease-[cubic-bezier(.16,1,.3,1)]',
          'w-full rounded-xl border-[var(--color-border)] max-h-[90vh]',
          entered ? 'opacity-100 translate-y-0 scale-100' : 'opacity-0 translate-y-[22px] scale-[0.96]',
          sizeClasses[size],
        )}
        // Stop event propagation to prevent overlay close when clicking inside modal
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-6">
          {children}
        </div>
      </div>
    </div>
  );
}
