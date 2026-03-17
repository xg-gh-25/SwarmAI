/**
 * Collapsible tool-result block, collapsed by default to a single summary line.
 *
 * Uses a single root element with toggled content to prevent React
 * reconciliation issues when multiple tool blocks are rendered consecutively.
 *
 * @see ToolUseBlock for the sibling collapse pattern.
 * Requirements: 4.5, 7.2
 */

import { useState } from 'react';
import { copyToClipboard } from '../../../utils/clipboard';

interface ToolResultBlockProps {
  content?: string;
  isError: boolean;
  truncated: boolean;
}

export function ToolResultBlock({ content, isError, truncated }: ToolResultBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const resolvedContent = content ?? '';

  const handleCopy = () => {
    copyToClipboard(resolvedContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const statusIcon = isError ? 'error' : 'check_circle';
  const statusColor = isError ? 'text-red-500' : 'text-status-online';

  const handleToggle = () => setIsExpanded((prev) => !prev);

  return (
    <div className={isExpanded
      ? 'bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden'
      : undefined
    }>
      {/* Header / toggle row */}
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={isExpanded}
        className={isExpanded
          ? 'flex items-center justify-between w-full px-4 py-2 bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors'
          : 'flex items-center gap-2 w-full px-3 py-1.5 rounded-md bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors'
        }
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className={`material-symbols-outlined text-sm ${statusColor}`}>
            {statusIcon}
          </span>
          <span className={`text-sm truncate flex-1 ${isExpanded ? 'font-medium text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}`}>
            Tool Result
          </span>
        </div>
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
          {isExpanded ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {/* Expanded content */}
      {isExpanded && (
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
          {truncated && (
            <span className="text-xs text-[var(--color-text-muted)] italic mt-1 block">
              Content truncated
            </span>
          )}
        </div>
      )}
    </div>
  );
}
