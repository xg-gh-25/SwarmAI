/**
 * ActivityFeed — collapsible summary of tool actions per assistant message.
 *
 * Parses tool_use content blocks from a message to show what files were
 * created, modified, read, searched, or what commands were run.
 * Collapsed by default — user clicks to expand.
 *
 * No new data fetching — reads existing message.content blocks.
 * No cross-tab state — everything is per-message.
 */

import { useState, useMemo } from 'react';
import type { ContentBlock } from '../../../types';

interface ActivityItem {
  type: 'created' | 'modified' | 'ran' | 'read' | 'searched';
  icon: string;
  label: string;
  path: string;
  iconColor: string;
}

/** Map SDK tool names to activity types. */
function extractActivities(blocks: ContentBlock[]): ActivityItem[] {
  const items: ActivityItem[] = [];
  const seen = new Set<string>();

  for (const block of blocks) {
    if (block.type !== 'tool_use') continue;

    const name = block.name;
    const summary = block.summary || '';

    // Extract file path from summary (best-effort parsing)
    const pathMatch = summary.match(/`([^`]+\.[a-z]{1,6})`/) || summary.match(/(\S+\.[a-z]{1,6})/);
    const path = pathMatch ? pathMatch[1] : '';

    let item: ActivityItem | null = null;

    if (name === 'Write' || name === 'write') {
      const key = `created:${path}`;
      if (!seen.has(key)) {
        seen.add(key);
        item = { type: 'created', icon: 'add_circle', label: 'CREATED', path, iconColor: '#3fb950' };
      }
    } else if (name === 'Edit' || name === 'edit') {
      const key = `modified:${path}`;
      if (!seen.has(key)) {
        seen.add(key);
        item = { type: 'modified', icon: 'edit', label: 'MODIFIED', path, iconColor: '#d29922' };
      }
    } else if (name === 'Read' || name === 'read') {
      const key = `read:${path}`;
      if (!seen.has(key)) {
        seen.add(key);
        item = { type: 'read', icon: 'visibility', label: 'READ', path, iconColor: '#7d8590' };
      }
    } else if (name === 'Bash' || name === 'bash') {
      // Extract command from summary
      const cmdMatch = summary.match(/`([^`]+)`/) || summary.match(/ran (.+)/i);
      const cmd = cmdMatch ? cmdMatch[1] : summary.slice(0, 60);
      const key = `ran:${cmd}`;
      if (!seen.has(key)) {
        seen.add(key);
        item = { type: 'ran', icon: 'terminal', label: 'RAN', path: cmd, iconColor: '#bc8cff' };
      }
    } else if (name === 'Grep' || name === 'grep' || name === 'Glob' || name === 'glob') {
      const key = `searched:${summary.slice(0, 40)}`;
      if (!seen.has(key)) {
        seen.add(key);
        item = { type: 'searched', icon: 'search', label: 'SEARCHED', path: summary.slice(0, 60), iconColor: '#58a6ff' };
      }
    }

    if (item) items.push(item);
  }

  return items;
}

interface ActivityFeedProps {
  blocks: ContentBlock[];
}

export function ActivityFeed({ blocks }: ActivityFeedProps) {
  const [isOpen, setIsOpen] = useState(false);
  const activities = useMemo(() => extractActivities(blocks), [blocks]);

  if (activities.length === 0) return null;

  return (
    <div className="mt-1">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`inline-flex items-center gap-1 text-[11px] text-[var(--color-text-dim)] cursor-pointer px-2 py-0.5 rounded border border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text-muted)] hover:border-[var(--color-border)] transition-all font-normal`}
      >
        <span
          className="material-symbols-outlined text-[13px] transition-transform duration-150"
          style={{ transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)' }}
        >
          chevron_right
        </span>
        Activity
        <span className="text-[10px] bg-[var(--color-hover)] text-[var(--color-text-muted)] px-1.5 rounded-full font-medium">
          {activities.length}
        </span>
      </button>

      {isOpen && (
        <div className="mt-1 border border-[var(--color-border)] rounded-lg overflow-hidden bg-[var(--color-card)]">
          {activities.map((item, i) => (
            <div
              key={`${item.type}-${i}`}
              className="flex items-center gap-2 px-2.5 py-1 text-[11.5px] text-[var(--color-text-muted)] border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)] hover:text-[var(--color-text-secondary)] transition-colors cursor-default"
            >
              <span className="material-symbols-outlined text-[14px] flex-shrink-0" style={{ color: item.iconColor }}>
                {item.icon}
              </span>
              <span className="text-[9.5px] text-[var(--color-text-dim)] w-[50px] flex-shrink-0 font-medium uppercase tracking-[0.3px]">
                {item.label}
              </span>
              <span className="font-mono text-[11px] text-[var(--color-explorer-accent)] truncate flex-1">
                {item.path}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default ActivityFeed;
