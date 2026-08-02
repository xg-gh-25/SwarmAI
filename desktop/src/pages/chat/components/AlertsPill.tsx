/**
 * AlertsPill — the 🔔 "Needs You" pill in the chat tab row.
 *
 * Replaces the bare bell: a LABELLED pill so the user knows what it is at a
 * glance (run_843962a5 — a bare bell gave no hint what it was). Sits in ChatHeader's right
 * action cluster, next to (not inside) the tab strip. Clicking opens a rich
 * popover anchored to the pill that reuses the shared <AttentionList> — the same
 * cards the Radar sidebar renders (paused pipelines / failed jobs / waiting
 * tabs), each actionable (inject-to-input / switch-tab).
 *
 * Data is passed in via props (`items` = the attention queue polled ONCE at
 * ChatPage) — this component does NOT poll, so there is no second 30s poll.
 *
 * States:
 *   - items.length === 0  → CALM: neutral grey pill, no red count badge.
 *   - items.length  >  0  → ALERT: red-tinted pill + pulsing count badge.
 */
import { useState, useRef, useEffect } from 'react';
import { AttentionList } from './RightSidebar/AttentionList';
import type { AttentionItem, ItemClickHandler } from './RightSidebar/types';

interface AlertsPillProps {
  items: AttentionItem[];
  /** Inject a message into the current chat input (paused / job items). */
  onItemClick?: ItemClickHandler;
  /** Switch to another tab (waiting items). */
  onSelectTab?: (tabId: string) => void;
  /**
   * Popover anchor direction.
   * - `left-flyout` (default): opens to the RIGHT of the pill, top-aligned —
   *   used by the left-sidebar top slot (run_2bdc68ad). z-[60] so the flyout
   *   sits ABOVE the fullscreen overlay scrim (Modal z-50).
   * - `right`: opens BELOW-right (the legacy ChatHeader position).
   */
  placement?: 'left-flyout' | 'right';
}

export function AlertsPill({ items, onItemClick, onSelectTab, placement = 'left-flyout' }: AlertsPillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const count = items.length;
  const calm = count === 0;

  // Close on click-outside + Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative flex-shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={calm ? 'Alerts — nothing needs you' : `Alerts — ${count} item(s) need you`}
        aria-expanded={open}
        title={calm ? 'Nothing needs you' : `${count} item(s) need you — decisions / job failures / external requests`}
        className={[
          'flex items-center gap-1.5 h-7 px-2 rounded-lg text-xs font-semibold transition-colors',
          calm
            ? 'bg-[var(--color-card)] border border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            : 'bg-red-500/10 border border-red-500/35 text-red-300 hover:bg-red-500/20 hover:border-red-500/60',
        ].join(' ')}
      >
        <span className="material-symbols-outlined text-[15px] leading-none">notifications</span>
        <span className="whitespace-nowrap">Needs You</span>
        {!calm && (
          <span className="min-w-[16px] h-4 px-1 rounded-lg bg-red-500 text-white text-[9.5px] font-bold font-mono flex items-center justify-center animate-pulse">
            {count}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Alerts"
          className={[
            'absolute w-[340px] rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-2xl overflow-hidden',
            placement === 'left-flyout'
              // sidebar flyout: to the RIGHT of the pill, top-aligned. z-[60]
              // clears the fullscreen overlay scrim (Modal z-50) so the flyout
              // is reachable even with a domain overlay open.
              ? 'left-[calc(100%+8px)] top-0 z-[60]'
              : 'right-0 top-[calc(100%+8px)] z-50',
          ].join(' ')}
        >
          {/* Header — self-explaining: says WHAT this surfaces */}
          <div className="px-3.5 py-3 border-b border-[var(--color-border)] bg-red-500/5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px] font-semibold text-[var(--color-text)]">Needs You</span>
              {!calm && (
                <span className="font-mono text-[10px] font-bold text-red-400 bg-red-500/15 rounded px-1.5 py-0.5 whitespace-nowrap">
                  {count} pending
                </span>
              )}
            </div>
            <div className="mt-0.5 text-[10.5px] leading-snug text-[var(--color-text-dim)]">
              Stuck conversations, failing jobs, and external requests surface here
            </div>
          </div>

          {calm ? (
            <div className="px-4 py-6 text-center text-[12px] text-[var(--color-text-muted)]">
              <div className="material-symbols-outlined text-[22px] opacity-40">check_circle</div>
              <div className="mt-1">Nothing needs you right now</div>
            </div>
          ) : (
            <div className="max-h-[360px] overflow-y-auto py-1">
              <AttentionList
                items={items}
                onItemClick={(message, context) => {
                  onItemClick?.(message, context);
                  setOpen(false);
                }}
                onSelectTab={(tabId) => {
                  onSelectTab?.(tabId);
                  setOpen(false);
                }}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
