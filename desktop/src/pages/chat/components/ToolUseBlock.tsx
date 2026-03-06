/**
 * Simplified tool-use block — fallback for edge cases where MergedToolBlock
 * is not used. Renders a single-line summary label with no expand/collapse.
 *
 * @exports ToolUseBlock — The fallback component
 */

import { getToolIcon } from './MergedToolBlock';

interface ToolUseBlockProps {
  name: string;
  summary: string;
  category?: string;
}

export function ToolUseBlock({ name, summary, category }: ToolUseBlockProps) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-[var(--color-hover)]">
      <span className="material-symbols-outlined text-primary text-sm">{getToolIcon(category)}</span>
      <span className="text-sm text-[var(--color-text-muted)] truncate">
        {summary || name || 'Unknown tool'}
      </span>
    </div>
  );
}
