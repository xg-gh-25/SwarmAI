/**
 * Hot News section — trending items from Chinese platforms + intl.
 *
 * Title click → populate ChatInput. Platform badge click → open browser.
 * Shared across WelcomeScreen and RadarSidebar.
 */

import { useState } from 'react';
import type { HotNewsItem } from '../../../../services/system';
import type { ItemClickHandler } from '../RightSidebar/types';
import { buildHotContext } from './BriefingUtils';
import { openExternal } from '../../../../utils/openExternal';

const REGION_FLAG: Record<string, string> = {
  cn: '🇨🇳',
  intl: '🌐',
};

interface HotNewsSectionProps {
  items: HotNewsItem[];
  onItemClick?: ItemClickHandler;
  compact?: boolean;
  defaultVisible?: number;
}

export function HotNewsSection({ items, onItemClick, compact, defaultVisible }: HotNewsSectionProps) {
  const limit = defaultVisible ?? (compact ? 4 : 6);
  const [expanded, setExpanded] = useState(false);

  if (items.length === 0) return null;

  const visible = expanded ? items : items.slice(0, limit);
  const remaining = items.length - limit;

  return (
    <div>
      <div className="space-y-0.5">
        {visible.map((item, i) => {
          const flag = REGION_FLAG[item.region] ?? REGION_FLAG.intl;

          return (
            <div
              key={`${item.title}-${i}`}
              className="flex items-center gap-2 px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors group"
            >
              <span className="shrink-0 text-[11px]">{flag}</span>
              <button
                type="button"
                onClick={() => onItemClick?.(`Tell me about: ${item.title}`, buildHotContext(item))}
                className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1 text-left cursor-pointer hover:underline"
              >
                {item.title}
              </button>
              {item.platform && (
                item.url ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      openExternal(item.url);
                    }}
                    className="shrink-0 text-[10px] text-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded hover:text-[var(--color-text)] transition-colors cursor-pointer"
                    title={`Open on ${item.platform}`}
                  >
                    {item.platform}
                  </button>
                ) : (
                  <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded">
                    {item.platform}
                  </span>
                )
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
