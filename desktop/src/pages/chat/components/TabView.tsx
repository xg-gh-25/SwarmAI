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
  /** True when the backend health check reports disconnected. Used to make the
   *  waiting indicator honest ("backend offline") instead of a bare spinner. */
  isBackendOffline?: boolean;
  /** Manually clear this tab's SESSION_BUSY recovery wait ("stop waiting").
   *  Safe local clear — the 15s reconcile tick still surfaces the result. */
  onCancelBusyWait?: (tabId: string) => void;
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
  isBackendOffline,
  onCancelBusyWait,
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

  // ── Per-tab store subscription — primary render source ─────────────
  // The rendered list is driven by this tab's own MessageStore subscription
  // (`useMessageStore(tabId)`). Every keep-mounted TabView (active AND
  // background) has its own live subscription, and every session-load path seeds
  // the store (loadSessionMessages, handleSelectTab, initTabState, reconcile/
  // mergeTabFromDb), so the store is the consistent superset authority.
  // `messagesProp` (= tabState.messages, a store MIRROR) is consulted ONLY by the
  // two narrow, streaming-gated rescues defined below (startup-gap + same-message
  // truncation) — it is never a free-floating second source. The former
  // `(store.length>0) ? store : messagesProp` selector was the reconcile-gap
  // split-brain (a stale prop truncated the reply); this revision keeps the store
  // authoritative and only lets a prop that is provably MORE complete for the
  // SAME (or absent) last message win, so divergence can't truncate.
  //
  // isActive gates the React re-render: a background (display:none) keep-mounted
  // tab must NOT re-render its full non-virtualized list on every store rAF
  // notification — N background streaming tabs doing so saturates the main
  // thread and freezes the ACTIVE tab (run_5e248977). The store still accumulates
  // (transport unaffected); this view re-syncs to the latest snapshot before
  // paint on activation. The active tab always auto-refreshes in real-time.
  const sub = useMessageStore(tabId, undefined, isActive);
  const storeMessages = sub?.messages;

  // Last assistant message (and its total renderable text length, -1 if none).
  const lastAsst = (arr: Message[] | undefined): Message | undefined =>
    arr && arr.length > 0 ? [...arr].reverse().find((m) => m.role === 'assistant') : undefined;
  const asstChars = (m: Message | undefined): number =>
    m ? m.content.reduce(
      (n, b) => n + (b && typeof b === 'object' && 'text' in b ? (b as { text: string }).text.length : 0),
      0,
    ) : -1;
  // A "plain" answer is text/thinking only — branch (b)'s content-swap is
  // restricted to these so it can NEVER drop a tool_use / tool_result /
  // interactive (ask_user_question / cmd_permission_request / escalation) block
  // the store carries (asstChars counts text only, and the prop mirror isn't
  // guaranteed to include those blocks). A truncated answer to rescue is text,
  // so this loses no real coverage.
  const isPlainAnswer = (m: Message | undefined): boolean =>
    !!m && Array.isArray(m.content) && m.content.every((b) => b.type === 'text' || b.type === 'thinking');

  // ── Mirror-desync guard: prefer the LIVE store snapshot over the React mirror ──
  // `sub.messages` is React state synced FROM the store by the subscription
  // callback — but that callback only runs while the tab is active AND while the
  // subscription keeps firing. If the SSE is torn down mid-stream (backend
  // EVICTION: the session vanishes from the states map → no more notify), the
  // mirror is left STALE-EMPTY while the store's own `_messages` never lost a
  // thing (no store mutation ran → no STORE-CLOBBER). The render then read the
  // empty mirror and, because `isStreaming` forces store-only, painted a blank
  // bubble that spun until the 90s watchdog — the "卡18分钟" freeze (frontend.log:
  // 18308cab storeChars 9434→0 in 77ms, propChars 11246, isStreaming true).
  // The store is authoritative: read its live snapshot and use whichever of
  // (mirror, live snapshot) carries the more-complete last assistant. getSnapshot()
  // returns a ref-stable cached array (rebuilt only when `_messages` changes), so
  // in steady state mirror===live and this is a no-op that preserves the
  // keep-mounted "same array ref → no re-render" property.
  const mirrorMsgs = storeMessages ?? [];
  const liveMsgs = sub?.store?.getSnapshot() ?? mirrorMsgs;
  const storeMsgs = asstChars(lastAsst(liveMsgs)) > asstChars(lastAsst(mirrorMsgs))
    ? liveMsgs
    : mirrorMsgs;

  // ── Render source: MORE-COMPLETE WINS — symmetric across mirror/store/prop. ──
  // The store (`storeMsgs`, now desync-guarded above) is the authoritative
  // live-turn source. The prop (`messagesProp` = tabState.messages) is a second
  // MIRROR; in steady state it only LAGS the store, leading only at cold start
  // (store lazy-loads empty/short while the restored prop holds the full last
  // answer — storeChars 0→155 vs propChars 1860). Three narrow, guarded rescues:
  //   (s) STREAMING SAFETY NET: store has NO assistant message AT ALL (`!sa`) but
  //       the prop does → the store mirror AND its live snapshot are both empty
  //       (the still-open eviction/recovery hop where the store object itself was
  //       re-created empty). A real in-flight turn ALWAYS carries an assistant
  //       placeholder in the store, so `!sa` here is NEVER a live turn — there is
  //       nothing to clobber. Render the prop instead of a blank, spinning bubble.
  //   (a) STARTUP GAP (idle): store has no assistant yet but the prop does.
  //   (b) SAME-MESSAGE truncation (idle): store's last assistant and the prop's
  //       are the SAME id but the store loaded a shorter copy with NO interactive
  //       block → swap in the prop's fuller content for that one message only.
  // The same-id / interactive guards keep a longer OLDER/different prop turn from
  // clobbering a newer store turn and never drop a live question/permission/
  // escalation. Symmetric with MessageStore._mergePreservingInteractive.
  const sa = lastAsst(storeMsgs);
  const pa = lastAsst(messagesProp);
  const storeChars = asstChars(sa);
  const propChars = asstChars(pa);

  let messages: Message[];
  if (isStreaming) {
    // (s) streaming safety net — only when the store carries no assistant at all.
    messages = (!sa && pa) ? messagesProp : storeMsgs;
  } else if (!sa && pa) {
    // (a) startup gap — store has no assistant yet, render the restored prop.
    messages = messagesProp;
  } else if (sa && pa && sa.id === pa.id && propChars > storeChars && isPlainAnswer(sa)) {
    // (b) same-message truncation — swap in the prop's fuller content only.
    messages = storeMsgs.map((m) => (m.id === sa.id ? { ...m, content: pa.content } : m));
  } else {
    messages = storeMsgs;
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

  const body = (
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
      {/* SESSION_BUSY recovery indicator — polling for backend completion.
          When the backend is offline the poll cannot self-resolve, so we say so
          and offer a manual exit (clears locally; the 15s reconcile still
          surfaces the answer when the backend returns). */}
      {isWaitingForBusy && !isStreaming && (
        <div className="flex items-center gap-2 text-[var(--color-text-muted)] py-2">
          <Spinner size="sm" />
          <span className="text-sm">
            {isBackendOffline
              ? t('chat.waitingBackendOffline', 'Backend offline — waiting to reconnect...')
              : t('chat.waitingForResponse', 'Waiting for response...')}
          </span>
          {onCancelBusyWait && tabId && (
            <button
              type="button"
              onClick={() => onCancelBusyWait(tabId)}
              className="ml-1 text-xs px-2 py-0.5 rounded border border-[var(--color-border)] hover:bg-[var(--color-bg-hover)] transition-colors"
              title={t('chat.stopWaitingTitle', 'Stop waiting and return to idle')}
            >
              {t('chat.stopWaiting', 'Stop waiting')}
            </button>
          )}
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

  return body;
}

/**
 * Memoized export — background TabViews skip re-render when ChatPage re-renders
 * with unchanged props. Each TabView still re-renders on its OWN store change
 * via its internal `useMessageStore(tabId)` subscription. Effective because all
 * props passed by ChatPage are referentially stable for non-changing tabs
 * (stable callbacks + tabMapRef-derived values).
 */
export const TabView = memo(TabViewImpl);
