/**
 * MessageBubble — thin dispatcher that routes rendering by message role.
 *
 * Branches on `message.role`:
 * - `'user'`      -> delegates to UserMessageView (minimal text bubble)
 * - `'assistant'`  -> delegates to AssistantMessageView (branded SwarmAI layout)
 *
 * All layout, avatar, header, and content rendering logic lives in the
 * sub-components. This file only owns the props interface and the role switch.
 *
 * Props `sessionId`, `isLastAssistant`, and `contextWarning` are threaded
 * through to AssistantMessageView so it can conditionally render the
 * Save-to-Memory button on the last assistant message and the Compact
 * Context button when a context warning is active.
 *
 * @exports MessageBubble      — The dispatcher component
 * @exports MessageBubbleProps  — Props interface
 *
 * Validates: Requirements 1.1, 1.2, 2.1, 3.1, 3.2, 6.1, 6.2
 */

import type { Message } from '../../../types';
import type { ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { UserMessageView } from './UserMessageView';
import { AssistantMessageView } from './AssistantMessageView';

export interface MessageBubbleProps {
  message: Message;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  pendingToolUseId?: string;
  pendingPermissionRequestId?: string;
  isStreaming?: boolean;
  sessionId?: string;
  isLastAssistant?: boolean;
  contextWarning?: ContextWarning | null;
  /** Called when user cancels a queued message. Only relevant for user messages with isQueued=true. */
  onCancelQueued?: () => void;
}

export function MessageBubble({
  message,
  onAnswerQuestion,
  onPermissionDecision,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  sessionId,
  isLastAssistant,
  contextWarning,
  onCancelQueued,
}: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <UserMessageView
        message={message}
        onCancelQueued={message.isQueued ? onCancelQueued : undefined}
      />
    );
  }

  return (
    <AssistantMessageView
      message={message}
      onAnswerQuestion={onAnswerQuestion}
      onPermissionDecision={onPermissionDecision}
      pendingToolUseId={pendingToolUseId}
      pendingPermissionRequestId={pendingPermissionRequestId}
      isStreaming={isStreaming}
      sessionId={sessionId}
      isLastAssistant={isLastAssistant}
      contextWarning={contextWarning}
    />
  );
}
