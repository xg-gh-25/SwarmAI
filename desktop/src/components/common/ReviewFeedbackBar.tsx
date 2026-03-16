/**
 * ReviewFeedbackBar — Footer bar shown in review mode.
 *
 * Displays the number of review comments and a "Send Feedback" button
 * that formats all comments and injects them into the chat input via
 * the `swarm:inject-chat-input` custom event.
 *
 * Key exports:
 * - `ReviewFeedbackBar` (default) — Footer bar component
 */

import { useCallback } from 'react';

interface ReviewFeedbackBarProps {
  /** Number of review comments. */
  commentCount: number;
  /** Pre-formatted feedback text. */
  feedbackText: string;
  /** Called after feedback is sent (to exit review mode). */
  onFeedbackSent: () => void;
  /** Called to clear all comments. */
  onClearComments: () => void;
}

export default function ReviewFeedbackBar({
  commentCount,
  feedbackText,
  onFeedbackSent,
  onClearComments,
}: ReviewFeedbackBarProps) {
  const handleSendFeedback = useCallback(() => {
    if (!feedbackText) return;

    // Inject formatted feedback into chat input
    window.dispatchEvent(
      new CustomEvent('swarm:inject-chat-input', {
        detail: { text: feedbackText, focus: true },
      }),
    );

    onFeedbackSent();
  }, [feedbackText, onFeedbackSent]);

  return (
    <div
      className="flex items-center justify-between px-4 py-2 border-t border-[var(--color-border)] bg-amber-500/5 shrink-0"
      data-testid="review-feedback-bar"
    >
      <div className="flex items-center gap-2 text-xs">
        <span className="text-amber-600 dark:text-amber-400 font-medium flex items-center gap-1">
          <span className="material-symbols-outlined text-sm">rate_review</span>
          Review Mode
        </span>
        <span className="text-[var(--color-text-muted)]">
          {commentCount === 0
            ? 'Click line numbers to add comments'
            : `${commentCount} comment${commentCount !== 1 ? 's' : ''}`}
        </span>
      </div>

      <div className="flex items-center gap-2">
        {commentCount > 0 && (
          <button
            onClick={onClearComments}
            className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            data-testid="clear-comments-btn"
          >
            Clear All
          </button>
        )}
        <button
          onClick={handleSendFeedback}
          disabled={commentCount === 0}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-1"
          data-testid="send-feedback-btn"
        >
          <span className="material-symbols-outlined text-sm">send</span>
          Send Feedback
        </button>
      </div>
    </div>
  );
}
