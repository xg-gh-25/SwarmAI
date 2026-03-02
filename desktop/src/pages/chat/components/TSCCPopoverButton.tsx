/**
 * Compact TSCC icon button with popover for the ChatInput bottom row.
 *
 * Renders a ``psychology`` Material Symbol button that toggles a popover
 * containing the five TSCC cognitive modules. Popover state is managed
 * locally via ``useState`` so it always starts closed on mount.
 *
 * Key exports:
 * - ``TSCCPopoverButton`` — The button + popover component
 * - ``TSCCPopoverButtonProps`` — Props interface
 *
 * Click-outside / Escape dismissal is handled via document-level
 * ``mousedown`` and ``keydown`` listeners scoped to ``isOpen``.
 * Tab-switch auto-close watches ``tsccState?.threadId`` via a ref
 * and closes the popover when the threadId changes or becomes undefined.
 */

import { useState, useRef, useEffect } from 'react';
import type { TSCCState } from '../../../types';
import {
  CurrentContextModule,
  ActiveAgentsModule,
  WhatAIDoingModule,
  ActiveSourcesModule,
  KeySummaryModule,
} from './TSCCModules';

export interface TSCCPopoverButtonProps {
  tsccState: TSCCState | null;
}

export function TSCCPopoverButton({ tsccState }: TSCCPopoverButtonProps) {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const isDisabled = tsccState === null;

  // Track previous threadId for tab-switch auto-close
  const prevThreadIdRef = useRef<string | undefined>(tsccState?.threadId);

  // Auto-close popover when threadId changes (tab switch) or tsccState becomes null
  useEffect(() => {
    const currentThreadId = tsccState?.threadId;
    if (prevThreadIdRef.current !== currentThreadId) {
      setIsOpen(false);
    }
    prevThreadIdRef.current = currentThreadId;
  }, [tsccState?.threadId]);

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

  const handleToggle = () => {
    setIsOpen((prev) => !prev);
  };

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
          flex items-center justify-center w-8 h-8 rounded-lg
          transition-colors
          ${isDisabled
            ? 'opacity-40 cursor-not-allowed'
            : 'hover:bg-[var(--color-hover)] cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
          }
        `}
      >
        <span className="material-symbols-outlined text-lg">
          psychology
        </span>
      </button>

      {isOpen && tsccState && (
        <div
          ref={popoverRef}
          className="
            absolute bottom-full left-0 mb-2
            w-72 max-h-[320px] overflow-y-auto
            bg-[var(--color-surface)] border border-[var(--color-border)]
            rounded-lg shadow-lg
            p-3 space-y-3
            z-50
          "
        >
          <CurrentContextModule tsccState={tsccState} />
          <ActiveAgentsModule tsccState={tsccState} />
          <WhatAIDoingModule tsccState={tsccState} />
          <ActiveSourcesModule tsccState={tsccState} />
          <KeySummaryModule tsccState={tsccState} />
        </div>
      )}
    </div>
  );
}
