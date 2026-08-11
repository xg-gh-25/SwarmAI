/**
 * TSCC context button + slide-out panel for the ChatInput bottom row.
 *
 * Renders a ``psychology`` Material Symbol button that toggles a tabbed
 * context panel (``SystemPromptModule``): Files / Recall / Security / Prompt.
 *
 * PANEL GEOMETRY (the reason this is not a plain popover):
 *   The panel is ANCHORED at its bottom-right corner to the TSCC button and
 *   grows toward the TOP-LEFT, flying out with a scale+fade animation whose
 *   transform-origin is bottom-right. It is bounded to a SAFE region so it can
 *   never overlap the chrome or leave the window:
 *     - left  edge ≥ LEFT_NAV (150px) + margin  → never covers the left nav
 *     - top   edge ≥ CHAT_TOP (80px)  + margin  → never covers the chat tab bar
 *     - right/bottom clamped inside the window   → never overflows the app window
 *   The panel's width/height are the min of its preferred size and the space
 *   actually available in that safe region, so content scrolls INSIDE rather
 *   than pushing past a boundary. Recomputed on open + scroll + resize.
 *
 * Rendered via ``createPortal`` to ``document.body`` to escape overflow-hidden
 * ancestors. Dismissal: click-outside, Escape, tab-switch (sessionId change).
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import type { SystemPromptMetadata } from '../../../types';
import { SystemPromptModule } from './TSCCModules';

export interface TSCCPopoverButtonProps {
  sessionId: string | null;
  metadata: SystemPromptMetadata | null;
}

// Safe-bound constants — mirror LayoutContext (LEFT_SIDEBAR_WIDTH=150,
// CHAT_CONTENT_TOP=80). Kept as local consts to avoid a cross-module import;
// if the layout constants change, update here.
const LEFT_NAV_RIGHT = 150;
const CHAT_TOP = 80;
const MARGIN = 10; // breathing room from each chrome edge
const PREFERRED_W = 420;
const PREFERRED_H = 480;
const MIN_W = 300;
const MIN_H = 260;

interface PanelBox {
  right: number; // px from viewport right edge
  bottom: number; // px from viewport bottom edge
  width: number;
  height: number;
}

export function TSCCPopoverButton({ sessionId, metadata }: TSCCPopoverButtonProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const isDisabled = sessionId === null;

  const prevSessionIdRef = useRef<string | null>(sessionId);
  useEffect(() => {
    if (prevSessionIdRef.current !== sessionId) {
      setIsOpen(false);
    }
    prevSessionIdRef.current = sessionId;
  }, [sessionId]);

  // Click-outside + Escape dismissal — listeners only while open.
  useEffect(() => {
    if (!isOpen) return;
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        popoverRef.current?.contains(target) ||
        buttonRef.current?.contains(target)
      ) {
        return;
      }
      setIsOpen(false);
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsOpen(false);
    };
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  // Compute the safe box: anchored bottom-right to the button, grown toward
  // top-left, clamped so it never crosses the nav / tab bar / window edges.
  const [box, setBox] = useState<PanelBox | null>(null);

  const recompute = useCallback(() => {
    const btn = buttonRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Anchor: bottom-right corner sits just above the button's top edge,
    // right-aligned to the button's right edge.
    const right = Math.max(MARGIN, vw - rect.right);
    const bottom = Math.max(MARGIN, vh - rect.top + 8);

    // Available space growing up-and-left from that anchor, respecting chrome.
    const availW = vw - right - (LEFT_NAV_RIGHT + MARGIN);
    const availH = vh - bottom - (CHAT_TOP + MARGIN);

    // If the safe region cannot fit a USABLE minimum panel, do NOT render a
    // collapsed one. The prior fix guaranteed "never overflow the safe region"
    // but at a tiny window that produced width/height = 0 → an invisible-but-
    // present dialog (focusable, screen-reader-announced, but nothing drawn), which
    // is worse to debug than a visible overflow (adversarial finding #2, re-review).
    // Clearing box → the `&& box` render gate below keeps the panel closed until the
    // window is big enough. NaN/degenerate-viewport (innerWidth ~0 during Tauri
    // startup frames) also lands here and is skipped, preserving the last good box.
    if (!(availW >= MIN_W) || !(availH >= MIN_H)) {
      setBox(null);
      return;
    }

    // Clamp to preferred size, but NEVER exceed the safe region (the overlap/
    // overflow guarantee wins over comfort). MIN is now a floor we KNOW fits
    // (guarded above), so the outer cap is a no-op in practice but kept for safety.
    const width = Math.min(Math.max(MIN_W, Math.min(PREFERRED_W, availW)), availW);
    const height = Math.min(Math.max(MIN_H, Math.min(PREFERRED_H, availH)), availH);

    setBox({ right, bottom, width, height });
  }, []);

  const handleToggle = () => {
    setIsOpen((prev) => {
      if (!prev) recompute();
      return !prev;
    });
  };

  useEffect(() => {
    if (!isOpen) return;
    const on = () => recompute();
    window.addEventListener('resize', on);
    window.addEventListener('scroll', on, true);
    return () => {
      window.removeEventListener('resize', on);
      window.removeEventListener('scroll', on, true);
    };
  }, [isOpen, recompute]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-label="TSCC context"
        disabled={isDisabled}
        onClick={isDisabled ? undefined : handleToggle}
        className={`
          w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0
          transition-colors
          ${isDisabled
            ? 'text-[var(--color-text-muted)]/50 cursor-not-allowed'
            : isOpen
              ? 'text-[var(--color-primary)] bg-[var(--color-hover)] cursor-pointer'
              : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] cursor-pointer'
          }
        `}
      >
        <span className="material-symbols-outlined text-[18px]">psychology</span>
      </button>

      {isOpen && sessionId && box && createPortal(
        <div
          ref={popoverRef}
          role="dialog"
          aria-label="TSCC context panel"
          style={{
            position: 'fixed',
            right: box.right,
            bottom: box.bottom,
            width: box.width,
            height: box.height,
            zIndex: 9999,
          }}
          className="
            animate-tscc-panel flex flex-col
            bg-[var(--color-card)] border border-[var(--color-border)]
            rounded-xl shadow-2xl overflow-hidden
          "
        >
          <div className="flex-1 overflow-y-auto p-3">
            <SystemPromptModule sessionId={sessionId} metadata={metadata} />
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
