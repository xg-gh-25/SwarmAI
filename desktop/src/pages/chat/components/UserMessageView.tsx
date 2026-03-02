/**
 * User message view component for the chat message area.
 *
 * Renders user messages as minimal text bubbles with a light background,
 * no avatar icon, and no timestamp. Implements 5-line truncation with
 * CSS `line-clamp-5` and a "Show more" / "Show less" expansion toggle.
 *
 * Key exports:
 * - ``UserMessageView``       — Main component for rendering user messages
 * - ``UserMessageViewProps``   — Props interface accepting a Message object
 *
 * Overflow detection uses a ResizeObserver on the text container to
 * re-evaluate clamping when the chat area width changes (e.g., sidebar
 * toggle or window resize), satisfying Requirement 8.1.
 */
import { useState, useRef, useEffect, useCallback } from 'react';
import type { Message } from '../../../types';
import MarkdownRenderer from '../../../components/common/MarkdownRenderer';
// USER_MESSAGE_MAX_LINES (= 5) from constants defines the truncation threshold.
// Tailwind requires static class names, so we use `line-clamp-5` directly.

export interface UserMessageViewProps {
  message: Message;
}

/**
 * Renders a user message as a minimal text bubble.
 *
 * - Light background, no avatar, no timestamp (Requirements 1.1, 1.2)
 * - 5-line truncation with expand/collapse toggle (Requirements 1.3, 1.4, 1.5)
 * - ResizeObserver-based overflow re-evaluation (Requirement 8.1)
 */
export function UserMessageView({ message }: UserMessageViewProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isClamped, setIsClamped] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  /**
   * Checks whether the text container overflows beyond the clamped height.
   * Called on mount and whenever the container resizes (e.g., sidebar toggle).
   */
  const checkOverflow = useCallback(() => {
    const el = contentRef.current;
    if (!el) return;

    if (isExpanded) {
      // When expanded, keep isClamped as true since it was clamped before expanding.
      return;
    }

    // Use rAF to ensure the DOM has painted with line-clamp applied
    requestAnimationFrame(() => {
      if (!contentRef.current) return;
      setIsClamped(contentRef.current.scrollHeight > contentRef.current.clientHeight + 1);
    });
  }, [isExpanded]);

  useEffect(() => {
    const el = contentRef.current;
    if (!el) return;

    // Initial overflow check
    checkOverflow();

    // Observe resize to re-evaluate clamping (Requirement 8.1)
    const observer = new ResizeObserver(() => {
      checkOverflow();
    });
    observer.observe(el);

    return () => {
      observer.disconnect();
    };
  }, [checkOverflow]);

  // Extract text content blocks from the message
  const textBlocks = message.content.filter(
    (block): block is { type: 'text'; text: string } => block.type === 'text'
  );

  if (textBlocks.length === 0) {
    return null;
  }

  const combinedText = textBlocks.map((b) => b.text).join('\n');

  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] bg-blue-500/10 dark:bg-blue-500/15 rounded-lg px-3 py-2 text-right">
        <div
          ref={contentRef}
          className={isExpanded ? 'text-left' : 'text-left line-clamp-5 overflow-hidden'}
        >
          <MarkdownRenderer content={combinedText} />
        </div>

        {isClamped && (
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setIsExpanded((prev) => !prev)}
              aria-expanded={isExpanded}
              className="mt-1 text-xs text-primary hover:text-primary-hover
                         cursor-pointer transition-colors"
            >
              {isExpanded ? 'Show less' : 'Show more'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
