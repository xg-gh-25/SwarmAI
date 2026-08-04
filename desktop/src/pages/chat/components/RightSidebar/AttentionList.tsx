/**
 * AttentionList — the presentational card list for the 🔔 "Needs You" queue.
 *
 * Extracted from AttentionSection (run_843962a5, Gate-1 fix B) so BOTH the Radar
 * sidebar's AttentionSection AND the ChatHeader Alerts popover render IDENTICAL
 * cards from ONE source (no duplication — C046). This component owns the card
 * rendering (paused/job/waiting via KIND_TAG + actionLabel), the See-more fold,
 * AND the "acting" state machine — so any consumer (section wrapper or popover)
 * gets working click→acting feedback for free without threading state.
 *
 * Click semantics by kind (unchanged from the original AttentionSection):
 *   - paused → onItemClick(resume message + decision context)  [inject to input]
 *   - job    → onItemClick(triage message)                     [inject to input]
 *   - waiting→ onSelectTab(tabId)                              [switch tab]
 *
 * Clicking does NOT immediately remove the item — the item is a projection of a
 * backend source (pipeline status / job failures / tab waiting_input) and only
 * disappears when the next poll sees the source condition resolved. To give
 * immediate feedback in that window, a clicked item enters an "acting" state
 * (greyed out + action text → resuming…/opening…/switching…) with a 35s
 * auto-expiry (longer than the 30s poll so a successfully-actioned item stays
 * "acting" until the poll removes it).
 *
 * Empty items → renders null (the caller decides whether to show a wrapper).
 */
import { useState, useRef, useEffect } from 'react';
import type { AttentionItem, ItemClickHandler } from './types';

interface AttentionListProps {
  items: AttentionItem[];
  /** Inject a message into the current chat input (paused / job items). */
  onItemClick?: ItemClickHandler;
  /** Switch to another tab (waiting items). */
  onSelectTab?: (tabId: string) => void;
  /** How many items show before the "See more" fold (default 3). */
  seeMoreLimit?: number;
}

/** How long an item stays visually "acting" before auto-clearing. Must be
 *  > the useRadarAttention poll interval (30s) so a resolved item stays acting
 *  until the poll removes it, rather than flashing back to un-acted. */
const ACTING_EXPIRY_MS = 35_000;

/** Default number of items shown before the "See more" fold. Keeps the queue
 *  scannable — the top N are what matter; the rest are one click away. The
 *  count shown by the caller always reflects the TOTAL, so nothing is hidden
 *  silently. */
const DEFAULT_SEE_MORE_LIMIT = 3;

const CARD_CLS =
  'flex w-full items-start gap-2 rounded px-2 py-1 text-left transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer';
const TITLE_CLS = 'block truncate text-[12.5px] text-[var(--color-text)]';
// --color-accent is not a defined theme token → falls back to --color-primary (the
// theme-reactive accent). Without the fallback this text rendered with no color (DoD-D).
const ACTION_CLS = 'mt-0.5 block text-[10.5px] font-semibold text-[var(--color-accent,var(--color-primary))]';

/** Category tag pill per kind — same visual family as the Changes NEW/UPD pills. */
const KIND_TAG: Record<AttentionItem['kind'], { label: string; cls: string }> = {
  paused: { label: 'PIPELINE', cls: 'text-amber-400 bg-amber-400/10' },
  job: { label: 'JOB', cls: 'text-red-400 bg-red-400/10' },
  waiting: { label: 'TAB', cls: 'text-blue-400 bg-blue-400/10' },
};

function TagPill({ kind }: { kind: AttentionItem['kind'] }) {
  const t = KIND_TAG[kind];
  return (
    <span className={`shrink-0 rounded px-1 text-[9px] font-bold tracking-wide ${t.cls}`}>
      {t.label}
    </span>
  );
}

/** Action label per kind, swapped for a progress label while acting. */
function actionLabel(kind: AttentionItem['kind'], acting: boolean): string {
  if (acting) {
    return kind === 'paused' ? 'resuming…' : kind === 'job' ? 'opening…' : 'switching…';
  }
  return kind === 'paused' ? '→ Resume & answer' : kind === 'job' ? '→ Investigate' : '→ Go answer';
}

/**
 * Paused-pipeline card. Title + tag + action always visible; the decision
 * `reason` is collapsed behind a chevron (sibling button + stopPropagation so
 * toggling never fires the resume action). `isActing` greys the card and swaps
 * the action label to "resuming…".
 */
function PausedCard({
  item,
  onAction,
  isActing,
}: {
  item: Extract<AttentionItem, { kind: 'paused' }>;
  onAction: () => void;
  isActing: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasReason = !!item.reason;

  return (
    <div className={`flex items-start ${isActing ? 'opacity-50' : ''}`}>
      <button type="button" onClick={onAction} className={CARD_CLS}>
        <span className="shrink-0 text-red-400 text-[13px] leading-5">⏸</span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 min-w-0">
            <TagPill kind="paused" />
            <span className={TITLE_CLS}>{item.title}</span>
          </span>
          {expanded && hasReason && (
            <span className="mt-0.5 block max-h-[180px] overflow-y-auto whitespace-pre-wrap text-[11px] leading-snug text-[var(--color-text-muted)]">
              {item.reason}
            </span>
          )}
          <span className={ACTION_CLS}>{actionLabel('paused', isActing)}</span>
        </span>
      </button>
      {hasReason && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="shrink-0 self-start rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] transition-colors"
          aria-label={expanded ? 'Collapse decision detail' : 'Expand decision detail'}
          aria-expanded={expanded}
          title={expanded ? 'Hide detail' : 'Show detail'}
        >
          <span
            className="material-symbols-outlined text-[16px] block transition-transform duration-150"
            style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)' }}
          >
            expand_more
          </span>
        </button>
      )}
    </div>
  );
}

export function AttentionList({
  items,
  onItemClick,
  onSelectTab,
  seeMoreLimit = DEFAULT_SEE_MORE_LIMIT,
}: AttentionListProps) {
  // "acting" state: item keys the user has clicked, shown as a progress state
  // until the poll removes the item. Keyed by `${kind}-${id}`.
  const [actingIds, setActingIds] = useState<Set<string>>(new Set());
  // Whether the fold is open (show all items vs just the top seeMoreLimit).
  const [showAll, setShowAll] = useState(false);
  const timeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const keyOf = (item: AttentionItem) => `${item.kind}-${item.id}`;

  // Clear all pending expiry timers on unmount (no leaked timers).
  useEffect(() => {
    const timers = timeoutsRef.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  // Reconcile acting-state against the live items on every poll: once an item
  // leaves the queue (its source condition resolved), drop its acting key +
  // timer. Without this, a reused `${kind}-${id}` (e.g. a job that fails again
  // <35s after being clicked+cleared) would render spuriously "acting".
  useEffect(() => {
    const liveKeys = new Set(items.map(keyOf));
    // Cancel timers for keys no longer present.
    timeoutsRef.current.forEach((timer, key) => {
      if (!liveKeys.has(key)) {
        clearTimeout(timer);
        timeoutsRef.current.delete(key);
      }
    });
    setActingIds((prev) => {
      let changed = false;
      const next = new Set<string>();
      prev.forEach((key) => {
        if (liveKeys.has(key)) next.add(key);
        else changed = true;
      });
      return changed ? next : prev; // avoid a no-op re-render
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  if (items.length === 0) return null;

  const markActing = (key: string) => {
    setActingIds((prev) => new Set(prev).add(key)); // new Set → triggers re-render
    const existing = timeoutsRef.current.get(key);
    if (existing) clearTimeout(existing);
    timeoutsRef.current.set(
      key,
      setTimeout(() => {
        setActingIds((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
        timeoutsRef.current.delete(key);
      }, ACTING_EXPIRY_MS),
    );
  };

  const handleClick = (item: AttentionItem) => {
    markActing(keyOf(item));
    if (item.kind === 'paused') {
      onItemClick?.(
        `Resume the paused pipeline "${item.title}" (${item.id}) — it stopped at ${item.stage}.`,
        item.reason ? `Decision needed: ${item.reason}` : undefined,
      );
    } else if (item.kind === 'job') {
      onItemClick?.(
        `The "${item.title}" job has failed ${item.failures} time(s) in a row — investigate why.`,
        item.lastError ? `Last error: ${item.lastError}` : undefined,
      );
    } else {
      onSelectTab?.(item.id);
    }
  };

  const visibleItems = showAll ? items : items.slice(0, seeMoreLimit);
  const hiddenCount = items.length - visibleItems.length;

  return (
    <div className="space-y-0.5 px-1 py-0.5">
      {visibleItems.map((item) => {
        const key = keyOf(item);
        const acting = actingIds.has(key);
        if (item.kind === 'paused') {
          return (
            <PausedCard
              key={key}
              item={item}
              onAction={() => handleClick(item)}
              isActing={acting}
            />
          );
        }
        return (
          <button
            key={key}
            type="button"
            onClick={() => handleClick(item)}
            className={`${CARD_CLS} ${acting ? 'opacity-50' : ''}`}
          >
            {item.kind === 'job' && (
              <>
                <span className="shrink-0 text-red-400 text-[13px] leading-5">⚠</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <TagPill kind="job" />
                    <span className={TITLE_CLS}>{item.title} failed {item.failures}x</span>
                  </span>
                  {item.lastError && (
                    <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-muted)]">
                      {item.lastError}
                    </span>
                  )}
                  <span className={ACTION_CLS}>{actionLabel('job', acting)}</span>
                </span>
              </>
            )}
            {item.kind === 'waiting' && (
              <>
                <span className="shrink-0 text-blue-400 text-[13px] leading-5">💬</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <TagPill kind="waiting" />
                    <span className={TITLE_CLS}>{item.title} is waiting for you</span>
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-muted)]">
                    {item.question}
                  </span>
                  <span className={ACTION_CLS}>{actionLabel('waiting', acting)}</span>
                </span>
              </>
            )}
          </button>
        );
      })}

      {(hiddenCount > 0 || showAll) && items.length > seeMoreLimit && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="flex w-full items-center justify-center gap-0.5 rounded px-2 py-0.5 text-[10.5px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] transition-colors"
          aria-expanded={showAll}
        >
          {showAll ? 'See less' : `See ${hiddenCount} more`}
          <span
            className="material-symbols-outlined text-[14px] transition-transform duration-150"
            style={{ transform: showAll ? 'rotate(180deg)' : 'rotate(0deg)' }}
          >
            expand_more
          </span>
        </button>
      )}
    </div>
  );
}
