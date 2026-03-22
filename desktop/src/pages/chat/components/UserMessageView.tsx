/**
 * User message view component for the chat message area.
 *
 * Renders user messages as minimal text bubbles with a light background,
 * no avatar icon, and no timestamp. Implements 5-line truncation with
 * CSS `line-clamp-5` and a "Show more" / "Show less" expansion toggle.
 *
 * Supports queued message display: when `message.isQueued` is true,
 * renders a "Queued" badge with a cancel button, reduced opacity,
 * and a dashed left border to visually distinguish pending messages.
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
import clsx from 'clsx';
import type { Message } from '../../../types';
import MarkdownRenderer from '../../../components/common/MarkdownRenderer';
// USER_MESSAGE_MAX_LINES (= 5) from constants defines the truncation threshold.
// Tailwind requires static class names, so we use `line-clamp-5` directly.

export interface UserMessageViewProps {
  message: Message;
  /** Called when the user cancels a queued message. Only provided when message.isQueued is true. */
  onCancelQueued?: () => void;
}

/**
 * Renders a user message as a minimal text bubble.
 *
 * - Light background, no avatar, no timestamp (Requirements 1.1, 1.2)
 * - 5-line truncation with expand/collapse toggle (Requirements 1.3, 1.4, 1.5)
 * - ResizeObserver-based overflow re-evaluation (Requirement 8.1)
 * - Queued message badge with cancel button when isQueued is true
 */
export function UserMessageView({ message, onCancelQueued }: UserMessageViewProps) {
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
      <div
        className={clsx(
          'max-w-[75%]',
          message.isQueued && 'opacity-85'
        )}
      >
        <div
          className={clsx(
            'bg-[var(--color-user-bubble-bg)] border border-[var(--color-user-bubble-border)] rounded-[14px_14px_4px_14px] px-3.5 py-2.5 text-right',
            message.isQueued && 'border-l-2 border-l-[var(--color-text-muted)] border-dashed'
          )}
        >
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

        {/* Queued message badge with cancel button */}
        {message.isQueued && (
          <div className="flex items-center gap-1.5 mt-1 text-xs text-[var(--color-text-muted)] justify-end">
            <span className="material-symbols-outlined text-sm">schedule_send</span>
            <span>Queued &mdash; will send when ready</span>
            {onCancelQueued && (
              <button
                onClick={onCancelQueued}
                className="ml-2 hover:text-[var(--color-text)] transition-colors"
                title="Cancel queued message"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
