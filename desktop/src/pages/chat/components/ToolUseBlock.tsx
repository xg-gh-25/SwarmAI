import { useState, useMemo } from 'react';
import { TOOL_INPUT_COLLAPSE_LENGTH } from '../constants';

interface ToolUseBlockProps {
  name: string;
  input: Record<string, unknown>;
}

/**
 * Collapsible Tool Use Input Component
 */
export function ToolUseBlock({ name, input }: ToolUseBlockProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  // Memoize expensive JSON serialization
  const { content, shouldCollapse, hiddenChars } = useMemo(() => {
    const content = JSON.stringify(input, null, 2);
    return {
      content,
      shouldCollapse: content.length > TOOL_INPUT_COLLAPSE_LENGTH,
      hiddenChars: content.length - TOOL_INPUT_COLLAPSE_LENGTH,
    };
  }, [input]);

  const displayContent = shouldCollapse && !isExpanded
    ? content.slice(0, TOOL_INPUT_COLLAPSE_LENGTH) + '...'
    : content;

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-sm">terminal</span>
          <span className="text-sm font-medium text-[var(--color-text)]">Tool Call: {name}</span>
        </div>
      </div>
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
          <code>{displayContent}</code>
        </pre>
        {shouldCollapse && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            aria-expanded={isExpanded}
            className="mt-2 text-xs text-primary hover:text-primary-hover transition-colors flex items-center gap-1"
          >
            <span className="material-symbols-outlined text-sm">
              {isExpanded ? 'expand_less' : 'expand_more'}
            </span>
            {isExpanded ? 'Show less' : `Show more (${hiddenChars} more chars)`}
          </button>
        )}
      </div>
    </div>
  );
}
