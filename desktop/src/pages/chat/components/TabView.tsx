/**
 * TabView — per-tab chat message-list view.
 *
 * Renders a single tab's scrollable message list: the "Load earlier messages"
 * control, the `WelcomeScreen` empty-state, the per-message bubble map
 * (MessageBubble / EvolutionMessage / system divider / ChatErrorMessage), the
 * streaming activity indicators (reconnecting / resuming / running-tool /
 * thinking, the fallback spinner, the SESSION_BUSY recovery spinner, and the
 * sticky bottom indicator), and the `messagesEndRef` scroll anchor.
 *
 * Migration Step 2 (this revision): TabView now subscribes to its OWN per-tab
 * `MessageStore` via `useMessageStore(tabId)` as the primary message source
 * (per-tab isolation + reactivity foundation). The `messages` prop is retained
 * as a transitional fallback only (until Step 4 repoints the remaining
 * ChatPage-level writers through the store and removes the shared mirror). Refs,
 * scroll handlers, and the pagination callback are still passed in from
 * `ChatPage` and are not relocated here (that is task 3.2 / Step 2 cont.).
 *
 * Derived values that are pure functions of `messages` (`lastAssistantIdx`,
 * `lastResumeBoundaryIdx`, and the resolved pending tool-use id) are computed
 * locally so the parent prop surface stays minimal.
 *
 * @exports TabView      — The per-tab message-list view component
 * @exports TabViewProps — Props interface
 *
 * Validates: Requirements 1.1, 2.1
 */
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Message } from '../../../types';
import { useMessageStore } from '../../../stores/useMessageStore';
import type { StreamingActivity, ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS } from '../../../hooks/useChatStreamingLifecycle';
import type { EvolutionEventType } from '../../../services/evolution';
import { Spinner } from '../../../components/common';
import { EvolutionMessage, ChatErrorMessage } from '../../../components/chat';
import type { PendingQuestion } from '../types';
import { resolvePendingToolUseId } from '../utils';
import { MessageBubble } from './MessageBubble';
import { WelcomeScreen } from './WelcomeScreen';

export interface TabViewProps {
  /** Registry key for this tab — used to scope the cancel-queued callback. */
  tabId: string;
  /**
   * Transitional fallback message source. Migration Step 2 (this revision):
   * TabView now subscribes to its OWN per-tab `MessageStore` via
   * `useMessageStore(tabId)` as the primary source. This prop is retained ONLY
   * as a fallback during the transition (until Step 4 / task 5.x repoints all
   * remaining ChatPage-level writers through the store and removes the shared
   * `messages` mirror). When the store is populated it wins; the prop covers
   * any writer that still only updates React state pre-Step-4.
   */
  messages: Message[];
  /** Per-tab session id — threaded to MessageBubble action callbacks. */
  sessionId?: string;
  /** Authoritative per-tab streaming flag. */
  isStreaming: boolean;
  /** React-state pending question (active-tab source). */
  pendingQuestion: PendingQuestion | null;
  /** Per-tab cached pending question (fallback when React state is null). */
  activeTabPendingQuestion: PendingQuestion | null;
  /** Active pending permission request id (or null). */
  pendingPermissionRequestId: string | null;
  /** Per-tab context warning — only the last assistant bubble consumes it. */
  contextWarning: ContextWarning | null;
  /** Reconnection / resume flags for the streaming indicator. */
  isReconnecting?: boolean;
  isResuming?: boolean;
  /** SESSION_BUSY recovery polling flag (shows a waiting spinner). */
  isWaitingForBusy: boolean;
  /** Debounced streaming activity label + elapsed seconds. */
  displayedActivity: StreamingActivity | null;
  elapsedSeconds: number;
  /** Pagination state for the "Load earlier messages" control. */
  hasMoreMessages: boolean;
  isLoadingOlderMessages: boolean;
  /** Scroll container + bottom anchor refs (owned by ChatPage this step). */
  messagesContainerRef: React.RefObject<HTMLDivElement | null>;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  /** Scroll handlers (owned by ChatPage this step). */
  onMessagesScroll: () => void;
  onScrollToBottom: () => void;
  onLoadOlder: () => void;
  /** Stable callbacks threaded into MessageBubble (keep memo intact). */
  onAnswerQuestion: (toolUseId: string, answers: Record<string, string>) => void;
  onPermissionDecision: (requestId: string, decision: 'approve' | 'deny') => void;
  onEscalationSelect: (escalationId: string, optionLabel: string) => void;
  onCancelQueued: (tabId: string) => void;
  onContinue: () => void;
  onFocusClick: (title: string) => void;
  onItemClick: (message: string, context?: string) => void;
  onRetryQueueTimeout: () => void;
}

export function TabView({
  tabId,
  messages: messagesProp,
  sessionId,
  isStreaming,
  pendingQuestion,
  activeTabPendingQuestion,
  pendingPermissionRequestId,
  contextWarning,
  isReconnecting,
  isResuming,
  isWaitingForBusy,
  displayedActivity,
  elapsedSeconds,
  hasMoreMessages,
  isLoadingOlderMessages,
  messagesContainerRef,
  messagesEndRef,
  onMessagesScroll,
  onScrollToBottom,
  onLoadOlder,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  onCancelQueued,
  onContinue,
  onFocusClick,
  onItemClick,
  onRetryQueueTimeout,
}: TabViewProps) {
  const { t } = useTranslation();

  // ── Per-tab store subscription (Migration Step 2) ──────────────────
  // Primary message source is this tab's OWN MessageStore (per-tab isolation +
  // reactivity). `useMessageStore(tabId)` returns null when there is no tabId
  // or the store was destroyed — fall back to the transitional `messagesProp`
  // so no pre-Step-4 writer-gap regresses. When the store has content it wins.
  const sub = useMessageStore(tabId);
  const storeMessages = sub?.messages;
  const messages = (storeMessages && storeMessages.length > 0) ? storeMessages : messagesProp;

  // Last assistant message index — used for Save-to-Memory button placement
  // and to scope streaming indicators to the final assistant bubble.
  const lastAssistantIdx = useMemo(
    () => messages.reduce((lastIdx, m, i) => m.role === 'assistant' ? i : lastIdx, -1),
    [messages],
  );

  // Last resume boundary index — messages before this render dimmed (prior
  // session). Only TRUE resume boundaries (id NOT starting with 'refresh-')
  // trigger dimming; refresh separators are same-session context refreshes.
  const lastResumeBoundaryIdx = useMemo(
    () => messages.reduce((lastIdx, m, i) =>
      m.role === 'system' && !m.id.startsWith('refresh-') ? i : lastIdx, -1),
    [messages],
  );

  return (
    <div
      ref={messagesContainerRef}
      onScroll={onMessagesScroll}
      className={messages.length === 0
        ? 'flex-1 overflow-hidden flex flex-col'
        : 'flex-1 overflow-y-auto pl-2 pr-4 py-3.5 space-y-2.5 min-w-0'
      }
    >
      {isLoadingOlderMessages && (
        <div className="flex justify-center py-2">
          <Spinner size="sm" />
        </div>
      )}
      {hasMoreMessages && !isLoadingOlderMessages && messages.length > 0 && (
        <button
          onClick={onLoadOlder}
          className="w-full py-1.5 text-xs text-gray-400 hover:text-gray-300 transition-colors text-center"
        >
          ↑ Load earlier messages
        </button>
      )}
      {messages.length === 0 ? (
        <WelcomeScreen onFocusClick={onFocusClick} onItemClick={onItemClick} />
      ) : (
        (() => {
        // Root 3 / 3A: derive the answerable question id from React state
        // with a fallback to the ACTIVE tab's per-tab cache. React
        // `pendingQuestion` is null when the question arrived on a
        // background tab or during the mid-stream stale-ref window
        // (setPendingQuestion is gated by isActiveTab). The cache is
        // always populated, so the question stays answerable.
        // Active-tab-only source → PIT71 cross-tab-leak safe (this loop
        // only ever renders the active tab's messages).
        const resolvedPendingToolUseId = resolvePendingToolUseId(pendingQuestion, activeTabPendingQuestion);
        return messages.map((msg, idx) => {
          // Evolution events get their own renderer
          if (msg.evolutionEvent) {
            return (
              <EvolutionMessage
                key={msg.id}
                eventType={msg.evolutionEvent.eventType as EvolutionEventType}
                data={msg.evolutionEvent.data}
              />
            );
          }
          // System messages (resume/refresh boundary) render as divider lines
          if (msg.role === 'system') {
            const isRefresh = msg.id.startsWith('refresh-');
            const label = isRefresh ? 'Context Refreshed' : 'Session Resumed';
            return (
              <div key={msg.id} className="flex items-center gap-3 py-3 px-4 select-none">
                <div className="flex-1 h-px bg-[var(--color-border)]" />
                <span className="text-xs text-[var(--color-text-muted)] whitespace-nowrap flex items-center gap-1.5">
                  {isRefresh && (
                    <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>refresh</span>
                  )}
                  {label}
                </span>
                <div className="flex-1 h-px bg-[var(--color-border)]" />
              </div>
            );
          }
          // Error messages get the structured error renderer
          if (msg.isError) {
            const textBlock = msg.content.find(b => b.type === 'text');
            const errorText = textBlock && 'text' in textBlock ? textBlock.text : 'An error occurred';
            const errorCode = (msg as unknown as Record<string, unknown>).errorCode as string | undefined;
            return (
              <ChatErrorMessage
                key={msg.id}
                error={{
                  code: errorCode,
                  message: errorText,
                  detail: (msg as unknown as Record<string, unknown>).errorDetail as string | undefined,
                  suggestedAction: (msg as unknown as Record<string, unknown>).suggestedAction as string | undefined,
                  retryAfter: (msg as unknown as Record<string, unknown>).retryAfter as number | undefined,
                }}
                onRetry={errorCode === 'QUEUE_TIMEOUT' ? onRetryQueueTimeout : undefined}
              />
            );
          }
          // Only pass isStreaming to the last assistant message.
          // Use lastAssistantIdx (not messages.length-1) so queued
          // user messages appended after the streaming assistant
          // don't strip the streaming indicators.
          const isLastAssistantForStreaming = isStreaming
            && msg.role === 'assistant'
            && idx === lastAssistantIdx;
          // Render streaming status indicators immediately after the
          // streaming assistant message so they stay visually attached
          // to the response — above any queued user messages.
          const streamingIndicator = isLastAssistantForStreaming ? (
            <>
              {isReconnecting && (
                <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                  <Spinner size="sm" />
                  <span className="text-sm">{t('chat.reconnecting', 'Reconnecting...')}</span>
                </div>
              )}
              {isResuming && (
                <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                  <Spinner size="sm" />
                  <span className="text-sm">{t('chat.resuming', 'Resuming session...')}</span>
                </div>
              )}
              {!isResuming && (
                <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                  <Spinner size="sm" />
                  <span className="text-sm">
                    {displayedActivity?.toolName
                      ? (displayedActivity.toolContext
                          ? (elapsedSeconds >= ELAPSED_DISPLAY_THRESHOLD_MS / 1000
                              ? t('chat.runningToolWithContextElapsed', {
                                  tool: displayedActivity.toolName,
                                  context: displayedActivity.toolContext,
                                  count: displayedActivity.toolCount,
                                  elapsed: formatElapsed(elapsedSeconds),
                                })
                              : t('chat.runningToolWithContext', {
                                  tool: displayedActivity.toolName,
                                  context: displayedActivity.toolContext,
                                  count: displayedActivity.toolCount,
                                }))
                          : displayedActivity.toolCount > 1
                            ? (elapsedSeconds >= ELAPSED_DISPLAY_THRESHOLD_MS / 1000
                                ? t('chat.runningToolWithCountElapsed', {
                                    tool: displayedActivity.toolName,
                                    count: displayedActivity.toolCount,
                                    elapsed: formatElapsed(elapsedSeconds),
                                  })
                                : t('chat.runningToolWithCount', {
                                    tool: displayedActivity.toolName,
                                    count: displayedActivity.toolCount,
                                  }))
                            : (elapsedSeconds >= ELAPSED_DISPLAY_THRESHOLD_MS / 1000
                                ? t('chat.runningToolElapsed', {
                                    tool: displayedActivity.toolName,
                                    elapsed: formatElapsed(elapsedSeconds),
                                  })
                                : t('chat.runningTool', { tool: displayedActivity.toolName })))
                      : displayedActivity?.hasContent
                        ? t('chat.processing')
                        : elapsedSeconds >= ELAPSED_DISPLAY_THRESHOLD_MS / 1000
                          ? t('chat.thinkingWithElapsed', { elapsed: formatElapsed(elapsedSeconds) })
                          : t('chat.thinking')}
                  </span>
                </div>
              )}
            </>
          ) : null;
          // Messages before the last resume boundary are from prior
          // session context — dim them to distinguish from current interaction.
          // ONLY dim when there are real (non-system) messages AFTER the
          // boundary. When the boundary is the last message (resume just
          // happened, user hasn't sent anything yet), dimming everything
          // is wrong — it looks like the whole window has a film over it.
          const hasContentAfterBoundary = lastResumeBoundaryIdx >= 0 &&
            lastResumeBoundaryIdx < messages.length - 1 &&
            messages.slice(lastResumeBoundaryIdx + 1).some(m => m.role !== 'system');
          const isPriorSession = hasContentAfterBoundary && idx < lastResumeBoundaryIdx;
          return (
            <React.Fragment key={msg.id}>
              <div className={isPriorSession ? 'opacity-50' : undefined}>
                <MessageBubble
                  message={msg}
                  onAnswerQuestion={onAnswerQuestion}
                  onPermissionDecision={onPermissionDecision}
                  onEscalationSelect={onEscalationSelect}
                  pendingToolUseId={resolvedPendingToolUseId}
                  pendingPermissionRequestId={pendingPermissionRequestId ?? undefined}
                  isStreaming={isLastAssistantForStreaming}
                  sessionId={sessionId}
                  isLastAssistant={idx === lastAssistantIdx}
                  contextWarning={idx === lastAssistantIdx ? contextWarning : null}
                  onCancelQueued={msg.isQueued && tabId ? () => onCancelQueued(tabId) : undefined}
                  onContinue={idx === lastAssistantIdx && !isStreaming ? onContinue : undefined}
                />
              </div>
              {streamingIndicator}
            </React.Fragment>
          );
        });
        })()
      )}
      {/* Fallback streaming indicator — only when isStreaming is true but
          no assistant message exists yet (gap between setIsStreaming(true)
          and assistant placeholder being added to messages). */}
      {isStreaming && lastAssistantIdx < 0 && (
        <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
          <Spinner size="sm" />
          <span className="text-sm">{t('chat.thinking')}</span>
        </div>
      )}
      {/* SESSION_BUSY recovery indicator — polling for backend completion */}
      {isWaitingForBusy && !isStreaming && (
        <div className="flex items-center gap-2 text-[var(--color-text-muted)] py-2">
          <Spinner size="sm" />
          <span className="text-sm">{t('chat.waitingForResponse', 'Waiting for response...')}</span>
        </div>
      )}
      {/* Sticky streaming indicator — always visible when agent is working,
          regardless of scroll position. Uses CSS sticky to float at the
          bottom of the scroll viewport when user scrolls up. Click to
          scroll to the active streaming message. */}
      {isStreaming && lastAssistantIdx >= 0 && (
        <div className="sticky bottom-0 z-10 flex items-center justify-center py-1.5">
          <button
            type="button"
            onClick={onScrollToBottom}
            className="flex items-center gap-2 px-3 py-1.5 rounded-full
                          bg-[var(--color-bg-primary)]/95 backdrop-blur-sm
                          border border-[var(--color-border-subtle)]
                          shadow-sm text-[var(--color-text-muted)]
                          hover:border-[var(--color-border)] hover:text-[var(--color-text-secondary)]
                          transition-colors cursor-pointer">
            <Spinner size="sm" />
            <span className="text-xs font-medium">
              {displayedActivity?.toolName
                ? t('chat.runningTool', { tool: displayedActivity.toolName })
                : t('chat.thinking')}
            </span>
            {elapsedSeconds >= 5 && (
              <span className="text-xs opacity-60">{formatElapsed(elapsedSeconds)}</span>
            )}
          </button>
        </div>
      )}
      <div ref={messagesEndRef} />
    </div>
  );
}
