/**
 * Collapsible tool-result block, collapsed by default to a single summary line.
 *
 * Mirrors the ToolUseBlock collapse/expand pattern:
 * - Collapsed: check_circle (or error) icon + "Tool Result" label + chevron
 *   on a light-gray row.
 * - Expanded: full <pre><code> content block with copy button inside a card.
 *
 * Handles undefined/null content gracefully via `content ?? ''`.
 *
 * @see ToolUseBlock for the sibling collapse pattern.
 * Requirements: 4.5, 7.2
 */

import { useState } from 'react';

interface ToolResultBlockProps {
  content?: string;
  isError: boolean;
}

export function ToolResultBlock({ content, isError }: ToolResultBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const resolvedContent = content ?? '';

  const handleCopy = () => {
    navigator.clipboard.writeText(resolvedContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const statusIcon = isError ? 'error' : 'check_circle';
  const statusColor = isError
    ? 'text-red-500'
    : 'text-status-online';

  if (!isExpanded) {
    // Collapsed view — single clickable summary line
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        aria-expanded={false}
        className="flex items-center gap-2 w-full px-3 py-1.5 rounded-md bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors"
      >
        <span className={`material-symbols-outlined text-sm ${statusColor}`}>
          {statusIcon}
        </span>
        <span className="text-sm text-[var(--color-text-muted)] truncate flex-1">
          Tool Result
        </span>
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
          expand_more
        </span>
      </button>
    );
  }

  // Expanded view — full card with content + copy button
  return (
    <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded(false)}
        aria-expanded={true}
        className="flex items-center justify-between w-full px-4 py-2 bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className={`material-symbols-outlined text-sm ${statusColor}`}>
            {statusIcon}
          </span>
          <span className="text-sm font-medium text-[var(--color-text)]">
            Tool Result
          </span>
        </div>
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
          expand_less
        </span>
      </button>
      <div className="p-4 relative">
        <button
          onClick={handleCopy}
          className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] rounded transition-colors"
        >
          <span className="material-symbols-outlined text-sm">
            {copied ? 'check' : 'content_copy'}
          </span>
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <pre className="text-sm text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words">
          <code>{resolvedContent}</code>
        </pre>
      </div>
    </div>
  );
}
