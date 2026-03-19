/**
 * Merged tool call + result block — renders a tool_use summary with its
 * corresponding tool_result inline as a single visual unit.
 *
 * Inspired by Kiro's inline tool rendering pattern where each tool call
 * and its result are displayed as one row instead of two separate blocks.
 *
 * Display modes:
 * 1. Pending (streaming) — summary + spinner
 * 2. Short result (≤200 chars, not truncated) — summary + inline result
 * 3. Long/truncated result — summary + collapsible section
 * 4. Error — summary + error icon + inline error content
 * 5. Implicitly complete (no result, not streaming) — summary + check icon
 *
 * @exports MergedToolBlock      — The merged component
 * @exports INLINE_RESULT_LIMIT  — 200 chars threshold for inline display
 * @exports getToolIcon          — Returns Material Symbols icon name for a tool category
 */

import { useState, useMemo } from 'react';
import { copyToClipboard } from '../../../utils/clipboard';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';

/** Character threshold below which results are shown inline without toggle. */
export const INLINE_RESULT_LIMIT = 200;

/** Map tool category to a Material Symbols icon name. */
const CATEGORY_ICONS: Record<string, string> = {
  bash: 'terminal',
  read: 'description',
  write: 'edit_note',
  search: 'search',
  web_fetch: 'language',
  web_search: 'travel_explore',
  tool_search: 'extension',
  skill: 'handyman',
  list_dir: 'folder_open',
  todowrite: 'checklist',
  agent: 'smart_toy',
  fallback: 'build',
};

/** Returns the Material Symbols icon name for a given tool category. */
export function getToolIcon(category?: string): string {
  return CATEGORY_ICONS[category ?? 'fallback'] ?? CATEGORY_ICONS.fallback;
}

/** Categories whose summaries contain a file path worth making clickable. */
const FILE_PATH_CATEGORIES = new Set(['read', 'write']);

/**
 * Known summary prefixes that precede a file path.
 * Matched in order; first match wins. The path is everything after the prefix.
 */
const PATH_PREFIXES = ['Writing to ', 'Reading ', 'Editing '];

/**
 * Extract a file path from a tool summary string for read/write/edit tools.
 * Returns { before, path } or null if no path found.
 */
function extractFilePath(summary: string): { before: string; path: string } | null {
  for (const prefix of PATH_PREFIXES) {
    if (summary.startsWith(prefix)) {
      const path = summary.slice(prefix.length).trim();
      if (path && (path.includes('/') || path.includes('.'))) {
        return { before: prefix, path };
      }
    }
  }
  return null;
}

interface MergedToolBlockProps {
  name: string;
  summary: string;
  toolUseId: string;
  category?: string;
  resultContent?: string;
  resultTruncated?: boolean;
  resultIsError?: boolean;
  isPending: boolean;
  /** @deprecated No longer used — kept for backward compatibility. */
  isStreaming?: boolean;
}

export function MergedToolBlock({
  name,
  summary,
  toolUseId: _toolUseId,
  category,
  resultContent,
  resultTruncated,
  resultIsError,
  isPending,
}: MergedToolBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const hasResult = resultContent !== undefined;
  const isError = !!resultIsError;
  const isShortResult =
    hasResult &&
    !isError &&
    !resultTruncated &&
    (resultContent?.length ?? 0) <= INLINE_RESULT_LIMIT;
  const isLongResult = hasResult && !isShortResult && !isError;

  // Status icon on the summary line.
  // Tool results are not reliably streamed by the Claude Code SDK —
  // the agentic loop executes tools internally and may not emit
  // ToolResultBlock for every ToolUseBlock. So "no result" after
  // streaming ends means implicitly complete, not orphaned.
  const statusIcon = isPending
    ? 'progress_activity'
    : resultIsError
      ? 'error'
      : 'check_circle';

  const statusColor = isPending
    ? 'text-[var(--color-text-muted)] animate-spin'
    : resultIsError
      ? 'text-red-500'
      : 'text-status-online';

  const handleCopy = () => {
    if (resultContent) {
      copyToClipboard(resultContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleToggle = () => setIsExpanded((prev) => !prev);

  // Extract clickable file path from summary for read/write/edit tools
  const fileParts = useMemo(() => {
    if (!FILE_PATH_CATEGORIES.has(category ?? '')) return null;
    return extractFilePath(summary);
  }, [summary, category]);

  const handlePathClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (fileParts) {
      document.dispatchEvent(
        new CustomEvent(OPEN_FILE_EVENT, { detail: { path: fileParts.path } }),
      );
    }
  };

  return (
    <div className="rounded-lg overflow-hidden">
      {/* Summary line */}
      <div
        className={`flex items-center gap-2 px-3 py-1.5 rounded-md bg-[var(--color-hover)] ${
          isLongResult ? 'cursor-pointer hover:brightness-95 transition-colors' : ''
        }`}
        onClick={isLongResult ? handleToggle : undefined}
        role={isLongResult ? 'button' : undefined}
        aria-expanded={isLongResult ? isExpanded : undefined}
      >
        <span className="material-symbols-outlined text-primary text-sm">{getToolIcon(category)}</span>
        <span className="text-sm text-[var(--color-text-muted)] truncate flex-1">
          {fileParts ? (
            <>
              {fileParts.before}
              <span
                onClick={handlePathClick}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') handlePathClick(e as unknown as React.MouseEvent); }}
                className="text-primary cursor-pointer hover:underline decoration-primary/50"
                title={`Open ${fileParts.path}`}
              >
                {fileParts.path}
              </span>
            </>
          ) : (
            summary || name || 'Unknown tool'
          )}
        </span>
        <span className={`material-symbols-outlined text-sm ${statusColor}`}>
          {statusIcon}
        </span>
        {isLongResult && (
          <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
            {isExpanded ? 'expand_less' : 'expand_more'}
          </span>
        )}
      </div>

      {/* Short result — inline, no toggle */}
      {isShortResult && resultContent && (
        <div className="px-3 py-1 ml-7">
          <pre className="text-xs text-[var(--color-text-muted)] whitespace-pre-wrap break-words">
            {resultContent}
          </pre>
        </div>
      )}

      {/* Error result — always inline with red styling */}
      {isError && resultContent && (
        <div className="px-3 py-1 ml-7">
          <pre className="text-xs text-red-400 whitespace-pre-wrap break-words">
            {resultContent}
          </pre>
        </div>
      )}

      {/* Long/truncated result — collapsible */}
      {isLongResult && !resultIsError && isExpanded && (
        <div className="p-3 ml-7 relative bg-[var(--color-card)] border-t border-[var(--color-border)]">
          <button
            onClick={handleCopy}
            className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] rounded transition-colors"
          >
            <span className="material-symbols-outlined text-sm">
              {copied ? 'check' : 'content_copy'}
            </span>
            {copied ? 'Copied!' : 'Copy'}
          </button>
          <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words pr-16">
            {resultContent}
          </pre>
          {resultTruncated && (
            <span className="text-xs text-[var(--color-text-muted)] italic mt-1 block">
              Content truncated
            </span>
          )}
        </div>
      )}
    </div>
  );
}
