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
const MARGIN = 12; // breathing room from each chrome edge
// Responsive-LARGE: the panel fills most of the safe region (bounded by the
// left-nav on the left and the tab-bar on top) rather than a small fixed box —
// the mockup's summary strip + charts + recall cards need room to breathe. We
// cap at a comfortable max so it doesn't stretch absurdly wide on a huge monitor,
// but on a normal window it grows to ~72% width / ~82% height of the safe region.
const MAX_W = 720;
const MAX_H = 720;
const MIN_W = 340;
const MIN_H = 300;
const FILL_W = 0.72; // fraction of available safe width to occupy
const FILL_H = 0.82; // fraction of available safe height to occupy

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

    // Degenerate/startup viewport (innerWidth ~0 during Tauri boot frames, or a
    // window narrower than the chrome itself) → skip, keep the last good box.
    if (!Number.isFinite(availW) || !Number.isFinite(availH) || availW < 120 || availH < 120) {
      return;
    }

    // Responsive-LARGE sizing: occupy a generous fraction of the safe region,
    // clamped to [MIN, MAX] AND hard-capped at the region itself so it can NEVER
    // overlap the left-nav / tab-bar / window edge. On a small window it shrinks
    // to fit (down to MIN or the region, whichever is smaller) and scrolls inside
    // — it is always VISIBLE, never hidden and never overflowing.
    const width = Math.min(availW, Math.max(MIN_W, Math.min(MAX_W, Math.round(availW * FILL_W))));
    const height = Math.min(availH, Math.max(MIN_H, Math.min(MAX_H, Math.round(availH * FILL_H))));

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
          <div className="flex-1 min-h-0 p-3">
            <SystemPromptModule sessionId={sessionId} metadata={metadata} />
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
