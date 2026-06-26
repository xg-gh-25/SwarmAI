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
 * Migration Step 5.1 (this revision): TabView is now rendered ONE PER OPEN TAB
 * and kept mounted; only the active tab is visible (`display:none` otherwise).
 * It owns its OWN scroll container + bottom anchor refs and its OWN auto-scroll,
 * subscribes to its OWN per-tab `MessageStore` via `useMessageStore(tabId)`, and
 * computes its own streaming activity. Switching tabs is a visibility toggle —
 * zero remount, zero markdown re-parse. The `messages` prop is a transitional
 * per-tab fallback; the store wins when populated. Exported memoized so
 * background views skip re-render on unrelated ChatPage renders.
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
import React, { useMemo, useRef, useEffect, useLayoutEffect, useCallback, memo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Message } from '../../../types';
import { useMessageStore } from '../../../stores/useMessageStore';
import { useStreamingActivity } from '../../../hooks/useStreamingActivity';
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
  /**
   * @deprecated Migration Step "4.1": activity is now computed per-tab inside
   * TabView via `useStreamingActivity(isStreaming, messages)`. These props are
   * accepted (so ChatPage's call site still typechecks) but IGNORED. They are
   * removed when ChatPage moves to the N-TabView render (task 5.1).
   */
  displayedActivity?: StreamingActivity | null;
  elapsedSeconds?: number;
  /** Whether this tab is the active/visible one. Inactive tabs stay mounted
   *  (keep-mounted) but are hidden via display:none — zero remount on switch. */
  isActive: boolean;
  /** Pagination state for the "Load earlier messages" control. */
  hasMoreMessages: boolean;
  isLoadingOlderMessages: boolean;
  /**
   * @deprecated Migration Step 5.1: TabView now owns its OWN scroll container +
   * bottom-anchor refs and its OWN auto-scroll (each keep-mounted view needs its
   * own DOM node). These ref/handler props are accepted for call-site
   * compatibility but IGNORED.
   */
  messagesContainerRef?: React.RefObject<HTMLDivElement | null>;
  messagesEndRef?: React.RefObject<HTMLDivElement | null>;
  onMessagesScroll?: () => void;
  onScrollToBottom?: () => void;
  /** Load older messages for this tab (infinite scroll at top). */
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

function TabViewImpl({
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
  isActive,
  hasMoreMessages,
  isLoadingOlderMessages,
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

  // ── Per-tab store subscription — SINGLE RENDER SOURCE ──────────────
  // The rendered message list comes from EXACTLY ONE source: this tab's own
  // MessageStore subscription (`useMessageStore(tabId)`). Every keep-mounted
  // TabView (active AND background) has its own live subscription, and every
  // session-load path seeds the store (loadSessionMessages, handleSelectTab,
  // initTabState, reconcile/mergeTabFromDb), so the store is the consistent
  // superset authority. `messagesProp` (a tabMapRef snapshot) is NO LONGER a
  // render source — the former `(store.length>0) ? store : messagesProp`
  // dual-source selector was the reconcile-gap split-brain: a stale prop
  // snapshot rendered a truncated reply whenever the store was momentarily
  // empty. Single source = divergence is structurally impossible.
  // (run_9db9f987 — Knowledge/Designs/2026-06-25-reconcile-gap-render-source-design.md)
  //
  // isActive gates the React re-render: a background (display:none) keep-mounted
  // tab must NOT re-render its full non-virtualized list on every store rAF
  // notification — N background streaming tabs doing so saturates the main
  // thread and freezes the ACTIVE tab (run_5e248977). The store still accumulates
  // (transport unaffected); this view re-syncs to the latest snapshot before
  // paint on activation. The active tab always auto-refreshes in real-time.
  const sub = useMessageStore(tabId, undefined, isActive);
  const storeMessages = sub?.messages;

  // Last assistant message's total renderable text length (-1 if none).
  const lastAsstChars = (arr: Message[] | undefined): number => {
    if (!arr || arr.length === 0) return -1;
    const last = [...arr].reverse().find((m) => m.role === 'assistant');
    if (!last) return -1;
    return last.content.reduce(
      (n, b) => n + (b && typeof b === 'object' && 'text' in b ? (b as { text: string }).text.length : 0),
      0,
    );
  };

  // ── Render source: MORE-COMPLETE WINS, but the prop is NEVER used while
  //    streaming. ───────────────────────────────────────────────────────────
  // The store is the authoritative live-turn source. DURING streaming we render
  // store-only: the prop (a tabMapRef snapshot) lags the store mid-stream, and a
  // longer PREVIOUS answer sitting in the prop must never overwrite the
  // in-progress reply. WHILE IDLE we render whichever source has the
  // more-complete LAST ASSISTANT message. This rescues the cold-start / restore
  // gap that store-only rendering left blank: on launch the store lazy-loads
  // from the backend (momentarily empty, or a shorter/incompletely-persisted
  // row) while the restored prop already holds the full last answer — store-only
  // showed a blank/truncated bubble for the whole load window (frontend.log:
  // storeChars 0→155 vs propChars 1860, rendered 0/155). Preferring the
  // more-complete source is symmetric with the store-vs-DB merge guard
  // (MessageStore._mergePreservingInteractive). Safe across turns: while idle the
  // prop tracks the store (both converge on the same last message), so a longer
  // prop only wins when it is genuinely the more-complete copy, never a stale
  // older turn.
  const storeChars = lastAsstChars(storeMessages);
  const propChars = lastAsstChars(messagesProp);
  const preferProp = !isStreaming && propChars > storeChars;
  const messages = preferProp ? messagesProp : (storeMessages ?? []);

  // RECONCILE-GAP PROBE: verify the chosen source actually rendered the
  // more-complete content. Retained through rollout, then removed.
  if (isActive && storeChars !== propChars && storeChars >= 0 && propChars >= 0) {
    console.warn('[reconcile-gap] RENDER-DIVERGE', {
      tabId,
      isActive,
      isStreaming,
      storeChars,
      propChars,
      chosen: preferProp ? 'prop' : 'store',
      renderedChars: preferProp ? propChars : storeChars,
    });
  }

  // ── Per-tab scroll (Migration Step 5.1) ────────────────────────────
  // Each keep-mounted TabView owns its OWN scroll container + bottom anchor and
  // its OWN auto-scroll, so background views keep their scroll position and the
  // active view scrolls independently. DOM is never destroyed on switch, so the
  // browser preserves scrollTop natively (no save/restore needed).
  const containerRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);

  // ── Mount-on-first-activation (Step 5.2 / F2) ──────────────────────
  // A tab that has never been visible renders a lightweight placeholder — no
  // message list, no markdown parse — so opening the app with several restored
  // tabs does NOT parse every tab's history up front. Once activated, content
  // mounts and stays mounted (keep-mounted), so later switches are zero-parse.
  const everActiveRef = useRef(false);
  if (isActive) everActiveRef.current = true;

  const scrollToBottom = useCallback(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const threshold = 100;
    userScrolledUpRef.current =
      !(el.scrollTop + el.clientHeight >= el.scrollHeight - threshold);
    if (el.scrollTop === 0) onLoadOlder();
  }, [onLoadOlder]);

  // Auto-scroll to bottom on new content — only for the active (visible) view
  // and only when the user has not scrolled up. Scrolling a hidden view is
  // pointless (and scrollIntoView no-ops on display:none).
  useEffect(() => {
    if (!isActive) return;
    if (!userScrolledUpRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'auto' });
    }
  }, [messages, isActive]);

  // Preserve scroll position when OLDER messages are prepended (load-earlier),
  // so the viewport doesn't jump. Runs before paint; compares scrollHeight
  // across the prepend and offsets scrollTop by the added height. Only fires on
  // a genuine top-prepend (first id changed AND list grew) — not on appends.
  const prevFirstIdRef = useRef<string | null>(null);
  const prevLenRef = useRef(0);
  const prevScrollHeightRef = useRef(0);
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const firstId = messages[0]?.id ?? null;
    const prepended =
      prevFirstIdRef.current !== null &&
      firstId !== prevFirstIdRef.current &&
      messages.length > prevLenRef.current;
    if (prepended && prevScrollHeightRef.current) {
      el.scrollTop += el.scrollHeight - prevScrollHeightRef.current;
    }
    prevFirstIdRef.current = firstId;
    prevLenRef.current = messages.length;
    prevScrollHeightRef.current = el.scrollHeight;
  }, [messages]);

  // ── Per-tab streaming activity (Migration Step 4.1) ────────────────
  // Activity label + elapsed timer derived from THIS tab's own state, gated to
  // streaming (no timers for idle/background tabs). Replaces the former
  // displayedActivity/elapsedSeconds props (now ignored).
  const { displayedActivity, elapsedSeconds } = useStreamingActivity(isStreaming, messages);

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
      ref={containerRef}
      onScroll={handleScroll}
      aria-hidden={!isActive}
      style={isActive ? undefined : { display: 'none' }}
      className={messages.length === 0
        ? 'flex-1 overflow-hidden flex flex-col'
        : 'flex-1 overflow-y-auto pl-2 pr-4 py-3.5 space-y-2.5 min-w-0'
      }
    >
      {everActiveRef.current ? (
      <>
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
            onClick={scrollToBottom}
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
      <div ref={endRef} />
      </>
      ) : null}
    </div>
  );
}

/**
 * Memoized export — background TabViews skip re-render when ChatPage re-renders
 * with unchanged props. Each TabView still re-renders on its OWN store change
 * via its internal `useMessageStore(tabId)` subscription. Effective because all
 * props passed by ChatPage are referentially stable for non-changing tabs
 * (stable callbacks + tabMapRef-derived values).
 */
export const TabView = memo(TabViewImpl);
