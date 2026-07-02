/**
 * AttentionSection — the 🔔 "需要你" queue in the Radar sidebar (Run 1 redesign).
 *
 * Renders the aggregated attention items (paused pipelines / failed jobs /
 * waiting tabs). Click semantics are dispatched by item kind:
 *   - paused → onItemClick(resume message + decision context)  [inject to input]
 *   - job    → onItemClick(triage message)                     [inject to input]
 *   - waiting→ onSelectTab(tabId)                              [switch tab]
 *
 * Empty queue → renders null (the whole section disappears, per D3 empty-hide).
 * Running pipelines are NOT here — they live in the bottom PipelinesBar.
 */
import { CollapsibleSection } from './shared/CollapsibleSection';
import type { AttentionItem, ItemClickHandler } from './types';

interface AttentionSectionProps {
  items: AttentionItem[];
  /** Inject a message into the current chat input (paused / job items). */
  onItemClick?: ItemClickHandler;
  /** Switch to another tab (waiting items). */
  onSelectTab?: (tabId: string) => void;
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
      label="需要你"
      count={items.length}
      defaultExpanded={true}
      accent="rgba(245,166,35,0.5)"
    >
      <div className="space-y-1 px-1 py-1">
        {items.map((item) => (
          <button
            key={`${item.kind}-${item.id}`}
            type="button"
            onClick={() => handleClick(item)}
            className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer"
          >
            {item.kind === 'paused' && (
              <>
                <span className="shrink-0 text-red-400 text-[13px] leading-5">⏸</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-[var(--color-text)]">
                    {item.title}
                  </span>
                  {item.reason && (
                    <span className="mt-0.5 block text-[11px] leading-snug text-[var(--color-text-muted)]">
                      「{item.reason}」
                    </span>
                  )}
                  <span className="mt-1 block text-[10.5px] font-semibold text-[var(--color-accent)]">
                    → resume 并回答
                  </span>
                </span>
              </>
            )}
            {item.kind === 'job' && (
              <>
                <span className="shrink-0 text-red-400 text-[13px] leading-5">⚠</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-[var(--color-text)]">
                    {item.title} 连续失败 {item.failures} 次
                  </span>
                  <span className="mt-1 block text-[10.5px] font-semibold text-[var(--color-accent)]">
                    → 排查
                  </span>
                </span>
              </>
            )}
            {item.kind === 'waiting' && (
              <>
                <span className="shrink-0 text-blue-400 text-[13px] leading-5">💬</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] text-[var(--color-text)]">
                    后台 {item.title} 在等你回答
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-[var(--color-text-muted)]">
                    {item.question}
                  </span>
                  <span className="mt-1 block text-[10.5px] font-semibold text-[var(--color-accent)]">
                    → 去回答
                  </span>
                </span>
              </>
            )}
          </button>
        ))}
      </div>
    </CollapsibleSection>
  );
}
