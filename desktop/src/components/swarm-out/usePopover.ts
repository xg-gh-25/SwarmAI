/**
 * One small anchored popover for the Swarm Out dashboard's tags (Autonomy Judge and
 * the run-trace tags). Shared so every tag behaves the same:
 *  - placed after it renders, from its measured size: below the tag when it fits,
 *    otherwise above, never over the tag; focus moves into it;
 *  - Escape closes it (a layer on the shared stack, hooks/useLayer) and focus returns
 *    to the tag;
 *  - an outside click, a scroll anywhere outside it or a window resize closes it
 *    (a fixed-position popover would otherwise stay put while its row scrolls away).
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useEscape } from '../../hooks/useLayer';

export function usePopover() {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const tagRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const popId = useId();

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    setPos(null);
    if (refocus) tagRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    if (!open || !tagRef.current || !popRef.current) return;
    const r = tagRef.current.getBoundingClientRect();
    const h = popRef.current.offsetHeight;
    const w = popRef.current.offsetWidth;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 12));
    const fitsBelow = r.bottom + 6 + h <= window.innerHeight - 8;
    const top = fitsBelow ? r.bottom + 6 : Math.max(8, r.top - h - 6);
    setPos({ left, top });
    popRef.current.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (popRef.current?.contains(e.target as Node) || tagRef.current?.contains(e.target as Node)) return;
      close(false);
    };
    const onMove = (e: Event) => {
      if (popRef.current && e.target instanceof Node && popRef.current.contains(e.target)) return;
      close(false);
    };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, close]);
  useEscape(open, () => close(true), popRef);

  const toggle = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (open) close(false);
    else setOpen(true);
  }, [open, close]);

  /** Props for the trigger button. */
  const triggerProps = {
    ref: tagRef,
    type: 'button' as const,
    onClick: toggle,
    'aria-expanded': open,
    'aria-haspopup': 'dialog' as const,
    'aria-controls': open ? popId : undefined,
  };

  /** Props for the popover root (render it in a portal to document.body). */
  const popoverProps = {
    ref: popRef,
    id: popId,
    role: 'dialog' as const,
    tabIndex: -1,
    style: pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: 'hidden' as const },
  };

  return { open, triggerProps, popoverProps };
}

/** The popover card's shared look. */
export const POPOVER_CLASS =
  'fixed z-[60] w-[440px] max-w-[calc(100vw-24px)] outline-none rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-xl px-4 pt-3.5 pb-3 text-[var(--color-text)]';
