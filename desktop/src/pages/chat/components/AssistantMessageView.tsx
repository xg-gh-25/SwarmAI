/**
 * AssistantMessageView — renders assistant messages with branded layout.
 *
 * This sub-component handles the full rendering of assistant-role messages:
 * - Branded header via AssistantHeader (🐝 SwarmAI · timestamp)
 * - Left-aligned content blocks with no avatar indentation
 * - Red border error wrapper when message.isError is true
 * - max-w-3xl readability constraint on content
 *
 * Key layout change: content starts at the left margin directly below the
 * header line — no avatar column or indentation gap.
 *
 * @exports AssistantMessageView      — The view React component
 * @exports AssistantMessageViewProps  — Props interface
 *
 * Validates: Requirements 2.1, 3.1, 3.2, 3.3, 6.1, 6.2
 */

import React, { useState, useCallback } from 'react';
import type { Message } from '../../../types';
import { ContentBlockRenderer } from './ContentBlockRenderer';
import { AssistantHeader } from './AssistantHeader';

export interface AssistantMessageViewProps {
  /** The assistant message to render */
  message: Message;
  /** Callback when the user answers an ask_user_question block */
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** The tool_use ID currently awaiting a user answer */
  pendingToolUseId?: string;
  /** Whether the assistant message is still streaming */
  isStreaming?: boolean;
}

export const AssistantMessageView: React.FC<AssistantMessageViewProps> = ({
  message,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
}) => {
  const [copied, setCopied] = useState(false);

  /** Extract plain text from all content blocks for clipboard copy. */
  const extractMessageText = useCallback((): string => {
    return message.content
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('\n');
  }, [message.content]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(extractMessageText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [extractMessageText]);

  const contentBlocks = message.content.map((block, index) => {
    // Use block-specific IDs for stable keys to prevent state mix-ups
    // when multiple tool blocks are rendered consecutively
    const key = block.type === 'tool_use' ? `tu-${block.id || index}`
      : block.type === 'tool_result' ? `tr-${block.toolUseId || index}`
      : `cb-${index}`;
    return (
      <ContentBlockRenderer
        key={key}
        block={block}
        onAnswerQuestion={onAnswerQuestion}
        pendingToolUseId={pendingToolUseId}
        isStreaming={isStreaming}
      />
    );
  });

  return (
    <div className="group/msg max-w-3xl">
      <AssistantHeader
        timestamp={message.timestamp}
        isStreaming={isStreaming}
      />

      {message.isError ? (
        <div
          className="border border-red-500/60 bg-red-500/10 rounded-lg p-3"
          role="alert"
          aria-label="Error message"
        >
          <div className="space-y-3">{contentBlocks}</div>
        </div>
      ) : (
        <div className="space-y-3">
          {contentBlocks}
          {/* Streaming cursor — blinking caret at end of streaming content */}
          {isStreaming && (
            <span className="inline-block w-2 h-4 bg-primary/70 rounded-sm animate-pulse align-text-bottom" aria-hidden="true" />
          )}
        </div>
      )}

      {/* Copy message button — appears on hover below content, hidden while streaming */}
      {!isStreaming && extractMessageText().length > 0 && (
        <div className="opacity-0 group-hover/msg:opacity-100 transition-opacity mt-1">
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1 px-2 py-0.5 text-xs text-[var(--color-text-muted)]
                       hover:text-[var(--color-text)] rounded transition-colors"
            title={copied ? 'Copied!' : 'Copy message'}
          >
            <span className="material-symbols-outlined text-sm">
              {copied ? 'check' : 'content_copy'}
            </span>
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
      )}
    </div>
  );
};

export default AssistantMessageView;
