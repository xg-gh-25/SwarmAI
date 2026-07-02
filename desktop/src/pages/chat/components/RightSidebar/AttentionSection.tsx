/**
 * AttentionSection — the 🔔 "Needs You" queue in the Radar sidebar.
 *
 * Renders the aggregated attention items (paused pipelines / failed jobs /
 * waiting tabs). Click semantics are dispatched by item kind:
 *   - paused → onItemClick(resume message + decision context)  [inject to input]
 *   - job    → onItemClick(triage message)                     [inject to input]
 *   - waiting→ onSelectTab(tabId)                              [switch tab]
 *
 * A paused card defaults to just Title + action; its (often long) checkpoint
 * reason is COLLAPSED behind a chevron — click the chevron to expand the full
 * reason, click the card body to run the action. Job/waiting cards are always
 * single-line (no reason, no chevron).
 *
 * Empty queue → renders null (the whole section disappears, per D3 empty-hide).
 * Running pipelines are NOT here — they live in the bottom PipelinesBar.
 */
import { useState } from 'react';
import { CollapsibleSection } from './shared/CollapsibleSection';
import type { AttentionItem, ItemClickHandler } from './types';

interface AttentionSectionProps {
  items: AttentionItem[];
  /** Inject a message into the current chat input (paused / job items). */
  onItemClick?: ItemClickHandler;
  /** Switch to another tab (waiting items). */
  onSelectTab?: (tabId: string) => void;
}

const CARD_CLS =
  'flex w-full items-start gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer';
const TITLE_CLS = 'block truncate text-[12.5px] text-[var(--color-text)]';
const ACTION_CLS = 'mt-1 block text-[10.5px] font-semibold text-[var(--color-accent)]';

/**
 * Paused-pipeline card: Title + action always visible; the decision `reason`
 * is collapsed by default behind a chevron. The chevron is a SIBLING button
 * (not nested — invalid HTML) and stops propagation so toggling never fires the
 * card's resume action. A paused card with no reason shows no chevron.
 */
function PausedCard({ item, onAction }: { item: Extract<AttentionItem, { kind: 'paused' }>; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const hasReason = !!item.reason;

  return (
    <div className="flex items-start">
      <button type="button" onClick={onAction} className={CARD_CLS}>
        <span className="shrink-0 text-red-400 text-[13px] leading-5">⏸</span>
        <span className="min-w-0 flex-1">
          <span className={TITLE_CLS}>{item.title}</span>
          {expanded && hasReason && (
            <span className="mt-0.5 block max-h-[180px] overflow-y-auto whitespace-pre-wrap text-[11px] leading-snug text-[var(--color-text-muted)]">
              {item.reason}
            </span>
          )}
          <span className={ACTION_CLS}>→ Resume &amp; answer</span>
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

export function AttentionSection({ items, onItemClick, onSelectTab }: AttentionSectionProps) {
  if (items.length === 0) return null;

  const handleClick = (item: AttentionItem) => {
    if (item.kind === 'paused') {
      // Inject a resume message with the decision context. User reviews + sends.
      onItemClick?.(
        `Resume the paused pipeline "${item.title}" (${item.id}) — it stopped at ${item.stage}.`,
        item.reason ? `Decision needed: ${item.reason}` : undefined,
      );
    } else if (item.kind === 'job') {
      onItemClick?.(
        `The "${item.title}" job has failed ${item.failures} time(s) in a row — investigate why.`,
      );
    } else {
      // waiting → switch to that tab; the pending question lives there.
      onSelectTab?.(item.id);
    }
  };

  return (
    <CollapsibleSection
      name="attention"
      icon="notifications"
      label="Needs You"
      count={items.length}
      defaultExpanded={true}
      accent="rgba(245,166,35,0.5)"
    >
      <div className="space-y-1 px-1 py-1">
        {items.map((item) => {
          if (item.kind === 'paused') {
            return (
              <PausedCard
                key={`paused-${item.id}`}
                item={item}
                onAction={() => handleClick(item)}
              />
            );
          }
          return (
            <button
              key={`${item.kind}-${item.id}`}
              type="button"
              onClick={() => handleClick(item)}
              className={CARD_CLS}
            >
              {item.kind === 'job' && (
                <>
                  <span className="shrink-0 text-red-400 text-[13px] leading-5">⚠</span>
                  <span className="min-w-0 flex-1">
                    <span className={TITLE_CLS}>{item.title} failed {item.failures}x</span>
                    <span className={ACTION_CLS}>→ Investigate</span>
                  </span>
                </>
              )}
              {item.kind === 'waiting' && (
                <>
                  <span className="shrink-0 text-blue-400 text-[13px] leading-5">💬</span>
                  <span className="min-w-0 flex-1">
                    <span className={TITLE_CLS}>{item.title} is waiting for you</span>
                    <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-muted)]">
                      {item.question}
                    </span>
                    <span className={ACTION_CLS}>→ Go answer</span>
                  </span>
                </>
              )}
            </button>
          );
        })}
      </div>
    </CollapsibleSection>
  );
}
