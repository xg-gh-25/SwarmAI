/**
 * Compact TSCC icon button with popover for the ChatInput bottom row.
 *
 * Renders a ``psychology`` Material Symbol button that toggles a popover
 * containing the ``SystemPromptModule`` — a single module showing context
 * file metadata, token counts, and a "View Full Prompt" action.
 *
 * Key exports:
 * - ``TSCCPopoverButton`` — The button + popover component
 * - ``TSCCPopoverButtonProps`` — Props interface
 *
 * Click-outside / Escape dismissal is handled via document-level
 * ``mousedown`` and ``keydown`` listeners scoped to ``isOpen``.
 * Tab-switch auto-close watches ``sessionId`` via a ref
 * and closes the popover when the sessionId changes or becomes undefined.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import type { SystemPromptMetadata } from '../../../types';
import { SystemPromptModule } from './TSCCModules';

export interface TSCCPopoverButtonProps {
  sessionId: string | null;
  metadata: SystemPromptMetadata | null;
}

export function TSCCPopoverButton({ sessionId, metadata }: TSCCPopoverButtonProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const isDisabled = sessionId === null;

  // Track previous sessionId for tab-switch auto-close
  const prevSessionIdRef = useRef<string | null>(sessionId);

  // Auto-close popover when sessionId changes (tab switch) or becomes null
  useEffect(() => {
    if (prevSessionIdRef.current !== sessionId) {
      setIsOpen(false);
    }
    prevSessionIdRef.current = sessionId;
  }, [sessionId]);

  // Click-outside and Escape dismissal — listeners attached only when open
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
      if (e.key === 'Escape') {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  // Compute fixed position from button rect so popover escapes overflow-hidden ancestors
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});

  const updatePopoverPosition = useCallback(() => {
    const btn = buttonRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    // Position above the button, aligned to its left edge
    setPopoverStyle({
      position: 'fixed',
      bottom: window.innerHeight - rect.top + 8,
      left: Math.max(8, rect.left), // clamp to viewport left edge
      zIndex: 9999,
    });
  }, []);

  const handleToggle = () => {
    setIsOpen((prev) => {
      if (!prev) updatePopoverPosition();
      return !prev;
    });
  };

  // Re-position on scroll/resize while open
  useEffect(() => {
    if (!isOpen) return;
    const reposition = () => updatePopoverPosition();
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [isOpen, updatePopoverPosition]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-label="TSCC context"
        disabled={isDisabled}
        onClick={isDisabled ? undefined : handleToggle}
        className={`
          w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0
          transition-colors
          ${isDisabled
            ? 'text-[var(--color-text-muted)]/50 cursor-not-allowed'
            : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] cursor-pointer'
          }
        `}
      >
        <span className="material-symbols-outlined text-[18px]">
          psychology
        </span>
      </button>

      {isOpen && sessionId && createPortal(
        <div
          ref={popoverRef}
          style={popoverStyle}
          className="
            w-80 max-h-[400px] overflow-y-auto
            bg-[var(--color-card)] border border-[var(--color-border)]
            rounded-lg shadow-xl
            p-3 space-y-3
          "
        >
          <SystemPromptModule sessionId={sessionId} metadata={metadata} />
        </div>,
        document.body
      )}
    </div>
  );
}
