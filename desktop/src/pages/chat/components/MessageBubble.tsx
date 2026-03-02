/**
 * MessageBubble — thin dispatcher that routes rendering by message role.
 *
 * Branches on `message.role`:
 * - `'user'`      → delegates to UserMessageView (minimal text bubble)
 * - `'assistant'`  → delegates to AssistantMessageView (branded SwarmAI layout)
 *
 * All layout, avatar, header, and content rendering logic lives in the
 * sub-components. This file only owns the props interface and the role switch.
 *
 * @exports MessageBubble      — The dispatcher component
 * @exports MessageBubbleProps  — Props interface (unchanged for backward compat)
 *
 * Validates: Requirements 1.1, 1.2, 2.1, 3.1, 3.2, 6.1, 6.2
 */

import type { Message } from '../../../types';
import { UserMessageView } from './UserMessageView';
import { AssistantMessageView } from './AssistantMessageView';

export interface MessageBubbleProps {
  message: Message;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

export function MessageBubble({
  message,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
}: MessageBubbleProps) {
  if (message.role === 'user') {
    return <UserMessageView message={message} />;
  }

  return (
    <AssistantMessageView
      message={message}
      onAnswerQuestion={onAnswerQuestion}
      pendingToolUseId={pendingToolUseId}
      isStreaming={isStreaming}
    />
  );
}
