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

import React from 'react';
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
    <div className="max-w-3xl">
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
        <div className="space-y-3">{contentBlocks}</div>
      )}
    </div>
  );
};

export default AssistantMessageView;
