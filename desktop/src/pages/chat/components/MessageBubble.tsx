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

import { memo } from 'react';
import type { Message } from '../../../types';
import type { ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { UserMessageView } from './UserMessageView';
import { AssistantMessageView } from './AssistantMessageView';

export interface MessageBubbleProps {
  message: Message;
  /** The bubble's list key (TabView `bubbleKeys`): scopes the turn's view memory. */
  viewKey?: string;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  onEscalationSelect?: (escalationId: string, optionLabel: string) => void;
  pendingToolUseId?: string;
  pendingPermissionRequestId?: string;
  isStreaming?: boolean;
  /** The context compactions of this message's turn, shown with it (TabView). */
  compactions?: Message[];
  sessionId?: string;
  isLastAssistant?: boolean;
  /** Steer segments (pages/chat/steerChain): see AssistantMessageView. */
  continued?: boolean;
  afterSteer?: boolean;
  headerLive?: boolean;
  contextWarning?: ContextWarning | null;
  /** Called when user cancels a queued message. Only relevant for user messages with isQueued=true. */
  onCancelQueued?: () => void;
  /** Re-send a user message the backend never accepted (sendState 'failed'). */
  onRetrySend?: () => void;
  /** Called when user clicks Continue to request agent continuation */
  onContinue?: () => void;
  /** Called by the assistant's local terminal-error card when retry is available. */
  onRetryError?: () => void;
  /** Read-only render (History preview): force all interactive blocks
   *  (AskUserQuestion / permission / escalation) to a non-actionable state,
   *  regardless of their historical pending status. */
  readOnly?: boolean;
}

function MessageBubbleImpl({
  message,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  compactions,
  sessionId,
  isLastAssistant,
  continued,
  afterSteer,
  headerLive,
  contextWarning,
  onCancelQueued,
  onRetrySend,
  onContinue,
  onRetryError,
  readOnly,
  viewKey,
}: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <UserMessageView
        message={message}
        onCancelQueued={message.isQueued ? onCancelQueued : undefined}
        onRetrySend={message.sendState === 'failed' ? onRetrySend : undefined}
        sessionId={sessionId}
      />
    );
  }

  return (
    <AssistantMessageView
      message={message}
      viewKey={viewKey}
      onAnswerQuestion={onAnswerQuestion}
      onPermissionDecision={onPermissionDecision}
      onEscalationSelect={onEscalationSelect}
      pendingToolUseId={pendingToolUseId}
      pendingPermissionRequestId={pendingPermissionRequestId}
      isStreaming={isStreaming}
      compactions={compactions}
      sessionId={sessionId}
      isLastAssistant={isLastAssistant}
      continued={continued}
      afterSteer={afterSteer}
      headerLive={headerLive}
      contextWarning={contextWarning}
      onContinue={onContinue}
      onRetryError={onRetryError}
      readOnly={readOnly}
    />
  );
}

/**
 * Memoized export. Skips re-render when props are referentially unchanged.
 *
 * This is the primary defense against per-token full-list re-render during
 * streaming: ``MessageStore.updateLast`` produces a new array reference but
 * keeps the same object reference for every NON-streaming message, so shallow
 * prop comparison short-circuits all historical bubbles — only the streaming
 * bubble (whose ``message`` ref changes each token) re-renders.
 *
 * Effective ONLY when callers pass stable props:
 * - callbacks must be stable (useCallback / latest-ref) — see ChatPage
 * - ``contextWarning`` should be scoped to the last assistant (it mutates
 *   periodically during streaming and would otherwise break every bubble)
 */
export const MessageBubble = memo(MessageBubbleImpl);
