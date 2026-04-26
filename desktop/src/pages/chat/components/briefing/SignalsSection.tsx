/**
 * Signals section — tech signals from RSS/GitHub/HN.
 *
 * Title click → populate ChatInput. Source badge click → open browser.
 * Shared across WelcomeScreen and RadarSidebar.
 */

import { useState } from 'react';
import type { BriefingSignal } from '../../../../services/system';
import type { ItemClickHandler } from '../RightSidebar/types';
import { buildSignalContext } from './BriefingUtils';
import { openExternal } from '../../../../utils/openExternal';

const URGENCY_BORDER: Record<string, string> = {
  high: 'border-l-red-400',
  medium: 'border-l-yellow-400',
  low: 'border-l-[var(--color-text-secondary)]',
};

interface SignalsSectionProps {
  items: BriefingSignal[];
  onItemClick?: ItemClickHandler;
  /** Compact mode for sidebar (fewer items, no summary) */
  compact?: boolean;
  /** Max items before "more" toggle (default 3 compact, 5 full) */
  defaultVisible?: number;
}

export function SignalsSection({ items, onItemClick, compact, defaultVisible }: SignalsSectionProps) {
  const limit = defaultVisible ?? (compact ? 3 : 5);
  const [expanded, setExpanded] = useState(false);

  if (items.length === 0) return null;

  const visible = expanded ? items : items.slice(0, limit);
  const remaining = items.length - limit;

  return (
    <div>
      <div className={compact ? 'space-y-0.5' : 'space-y-1'}>
        {visible.map((signal, i) => {
          const borderCls = URGENCY_BORDER[signal.urgency] ?? URGENCY_BORDER.medium;

          return (
            <div
              key={signal.title || i}
              className={`${compact ? '' : `border-l-2 ${borderCls} pl-2.5`} py-1 group hover:bg-[var(--color-bg-hover)] rounded${compact ? '' : '-r'} px-1 transition-colors`}
            >
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => onItemClick?.(`Tell me about: ${signal.title}`, buildSignalContext(signal))}
                  className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1 text-left cursor-pointer hover:underline"
                >
                  {signal.title}
                </button>
                {signal.source && (
                  signal.sourceUrl ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        openExternal(signal.sourceUrl);
                      }}
                      className="shrink-0 text-[10px] text-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded hover:text-[var(--color-text)] transition-colors cursor-pointer"
                      title={`Open ${signal.source}`}
                    >
                      {signal.source}
                    </button>
                  ) : (
                    <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded">
                      {signal.source}
                    </span>
                  )
                )}
              </div>
              {!compact && signal.summary && (
                <p className="text-[11px] text-[var(--color-text-secondary)] mt-0.5 line-clamp-2 leading-relaxed">
                  {signal.summary}
                </p>
              )}
            </div>
          );
        })}
      </div>
      {remaining > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] mt-1 cursor-pointer transition-colors px-1"
        >
          {expanded ? '▴ Show less' : `▾ ${remaining} more`}
        </button>
      )}
    </div>
  );
}
