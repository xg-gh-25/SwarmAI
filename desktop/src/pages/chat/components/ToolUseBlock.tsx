import { useState, useMemo } from 'react';

interface ToolUseBlockProps {
  name: string;
  input: Record<string, unknown>;
}

/**
 * Collapsible tool-use block, collapsed by default to a single summary line.
 *
 * Collapsed: terminal icon + tool name + chevron on a light-gray row.
 * Expanded: full header bar + JSON content + copy button.
 *
 * JSON serialization is deferred until the block is expanded to avoid
 * unnecessary work for collapsed blocks (Requirement 4.1–4.4, 7.2).
 */
export function ToolUseBlock({ name, input }: ToolUseBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  // Defer JSON serialization — only compute when expanded
  const content = useMemo(
    () => (isExpanded ? JSON.stringify(input, null, 2) : ''),
    [input, isExpanded],
  );

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isExpanded) {
    // Collapsed view — single clickable summary line
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        aria-expanded={false}
        className="flex items-center gap-2 w-full px-3 py-1.5 rounded-md bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors"
      >
        <span className="material-symbols-outlined text-primary text-sm">terminal</span>
        <span className="text-sm text-[var(--color-text-muted)] truncate flex-1">{name}</span>
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">expand_more</span>
      </button>
    );
  }

  // Expanded view — full header bar + JSON content + copy button
  return (
    <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded(false)}
        aria-expanded={true}
        className="flex items-center justify-between w-full px-4 py-2 bg-[var(--color-hover)] text-left hover:brightness-95 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-sm">terminal</span>
          <span className="text-sm font-medium text-[var(--color-text)]">Tool Call: {name}</span>
        </div>
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">expand_less</span>
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
          <code>{content}</code>
        </pre>
      </div>
    </div>
  );
}
