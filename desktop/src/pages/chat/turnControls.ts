/**
 * turnControls — the send / stop / continue controllers of a chat tab, out of
 * ChatPage so they can be driven by tests (docs/design/2026-10-24-chat-stream-structure.md A1).
 *
 * Every dependency is injected (``TurnControlDeps``): the tab map, the stores, the
 * chat service, the hook's stream handlers and setters, the composer. ChatPage builds
 * the deps each render and calls these functions; behaviour is the same as the
 * handlers they replace.
 *
 * Key exports:
 * - ``sendMessage``     — the composer's Send: busy -> ``sendWhileBusy`` (the server queues
 *   it); otherwise the tab's own POST with stream handlers and a reconnect resend.
 * - ``sendWhileBusy``   — POST a message while a turn runs; the bubble is "Queued" until
 *   the server decides, "Not sent" when it never accepted it.
 * - ``answerQuestion``  — continue a turn waiting on an AskUserQuestion answer.
 * - ``decidePermission``— continue a turn waiting on a command-permission decision.
 * - ``stopTurn``        — the Stop button: one Stop at a time per tab.
 * - ``stopBackground``  — stop a tab's background agents while no turn runs.
 */
import type { ChatRequest, ContentBlock, Message, UnifiedAttachment, StreamEvent } from '../../types';
import type { UnifiedTab, TabStatus } from '../../hooks/useUnifiedTabState';
import type { UiContextSnapshot } from '../../utils/uiContext';
import type { chatService, StreamLivenessOpts } from '../../services/chat';
import { canContinueTurn, shouldQueueSend } from '../../hooks/streaming-guards';
import { terminalContextOf } from '../../utils/terminalAttachment';
import { refreshCanvasChanges } from '../../services/canvasSession';

/** The part of a MessageStore the controllers use. */
export interface TurnStore {
  readonly messages: readonly Message[];
  append(m: Message): void;
  appendMany(ms: Message[]): void;
  updateById(id: string, fn: (m: Message) => Message): void;
  updateLast(fn: (m: Message) => Message, pred: (m: Message) => boolean): void;
  endStreaming(): void;
}

type Chat = Pick<typeof chatService, 'streamChat' | 'streamAnswerQuestion' | 'streamCmdPermissionContinue' | 'stopSession'>;
type Abort = () => void;

export interface TurnControlDeps {
  tabs(): Map<string, UnifiedTab>;
  activeTabId(): string | null | undefined;
  agentId(): string | null | undefined;
  options(): { enableSkills: boolean; enableMCP: boolean };
  composer: {
    text(): string;
    attachments(): UnifiedAttachment[];
    /** empty the ACTIVE tab's composer (text and attachments) */
    clear(): void;
    /** empty the ACTIVE tab's composer text only */
    clearText(): void;
    /** give a draft back to the tab it was taken from (draftToRestore rules) */
    restore(tabId: string, text: string, attachments: UnifiedAttachment[]): void;
  };
  buildContent(text: string, attachments: UnifiedAttachment[]): Promise<ContentBlock[]>;
  stores: {
    get(tabId: string): TurnStore | undefined | null;
    getOrCreate(tabId: string, init?: { sessionId?: string }): TurnStore;
  };
  chat: Chat;
  stream: {
    handler(assistantId: string, tabId?: string): (e: StreamEvent) => void;
    error(assistantId: string, tabId?: string): (e: Error) => void;
    complete(tabId?: string): () => void;
    disconnect(tabId?: string): () => void;
    liveness(tabId: string | null | undefined): StreamLivenessOpts;
  };
  initTab(tabId: string): void;
  setLocalSending(on: boolean, tabId?: string): void;
  incrementStreamGen(): void;
  setTabStatus(tabId: string, status: TabStatus): void;
  /** the first message of a new tab names it */
  titleFromFirstMessage(tabId: string, text: string): void;
  updateTab(tabId: string, patch: Partial<Omit<UnifiedTab, 'id'>>): void;
  noteSend(tabId: string, request: ChatRequest): ChatRequest;
  editorContext(tabId: string | null): UiContextSnapshot;
  pluginCommand(text: string): Promise<unknown>;
  rebindQueue(tabId: string): void;
  syncQueuedBadges(tabId: string): void;
  /** React message state of the ACTIVE tab (fallback writes, Stopped marker) */
  setMessages(fn: (prev: Message[]) => Message[]): void;
  clearPendingQuestion(): void;
  clearPendingPermission(): void;
  dismissNotice(tabId: string, id: string): void;
  /** tabs whose permission decision POST is open (double-click guard) */
  permissionLoading: Set<string>;
  newClientId(): string;
}

const asAbortController = (abort: Abort) =>
  ({ abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController);

/** The assistant bubble a continuation renders into: the one holding its
 *  question / prompt, else the tab's last assistant message. */
function continuationHostId(d: TurnControlDeps, tabId: string | undefined, holds: (m: Message) => boolean): string {
  const msgs = (tabId ? d.stores.get(tabId)?.messages : undefined) ?? [];
  const host = [...msgs].reverse().find((m) => m.role === 'assistant' && holds(m))
    ?? [...msgs].reverse().find((m) => m.role === 'assistant');
  return host?.id ?? `local-sub-continuation-${Date.now()}`;
}

/**
 * Send while a turn of the session is in progress: POST the message on its own
 * (no stream handlers of this tab, no stream generation): the backend queues it
 * (`accepted` with ``queued``, then SESSION_BUSY) or, if the session freed in
 * between, runs it — that turn then reaches the tab on the subscription like any
 * drained turn. The bubble shows "Queued" until the server's queue decides; an
 * unaccepted send is "Not sent · Retry" (docs/design/chat-turn-liveness.md S3, P2b).
 */
export async function sendWhileBusy(d: TurnControlDeps, tabId: string, text: string, attachments: UnifiedAttachment[]): Promise<void> {
  const tab = d.tabs().get(tabId);
  const sid = tab?.sessionId;
  const agentId = d.agentId();
  if (!tab || !sid || !agentId) return;
  const usable = (attachments || []).filter((a) => !a.error && !a.isLoading);
  const hasAtt = usable.length > 0;
  // The draft leaves the composer now (this tab is the active one); if the send
  // does not happen it goes back to this tab (restore), never elsewhere.
  d.composer.clear();
  const prep = Date.now() + Math.random();
  tab._preparingSend = prep;
  let content: ContentBlock[] = [];
  let failed = false;
  try {
    content = await d.buildContent(text, usable);   // the files as they are now
  } catch (err) {
    console.error('[ChatPage] busy send: reading attachments failed', err);
    failed = true;
  }
  // Stop or a reset took the preparation away: not sent
  const stillMine = tab._preparingSend === prep;
  if (stillMine) tab._preparingSend = undefined;
  // failed, stopped, closed, reset or rebound to another session meanwhile: not sent
  if (failed || !stillMine || d.tabs().get(tabId) !== tab || tab.sessionId !== sid || content.length === 0) {
    d.composer.restore(tabId, text, attachments);
    return;
  }
  d.rebindQueue(tabId);
  const clientId = d.newClientId();
  const trimmed = text.trim();
  const display: ContentBlock[] = [{ type: 'text', text: trimmed || '[Attachments]' }];
  if (hasAtt && trimmed) display.push({ type: 'text', text: `📎 ${usable.map((a) => a.name).join(', ')}` });
  const store = d.stores.getOrCreate(tabId, { sessionId: sid });
  store.append({ id: clientId, role: 'user', content: display, timestamp: new Date().toISOString(),
    metadata: { client_id: clientId }, isQueued: true });
  if (!tab._queuedLocal) tab._queuedLocal = new Map();
  tab._queuedLocal.set(clientId, undefined);
  const terminal = terminalContextOf(usable);
  const { enableSkills, enableMCP } = d.options();
  const request: ChatRequest = {
    agentId,
    ...(hasAtt ? { content } : { message: text }),
    sessionId: sid,
    enableSkills,
    enableMCP,
    editorContext: d.editorContext(tabId),
    ...(terminal && { terminalContext: terminal }),
    clientId,
  };
  if (!tab._unsentRequests) tab._unsentRequests = new Map();
  tab._unsentRequests.set(clientId, request);
  let acceptedHere = false;
  const settle = () => {
    const t = d.tabs().get(tabId);
    if (!t) return;
    if (!acceptedHere) {
      // never accepted: "Not sent · Retry" (the request stays for Retry)
      t._queuedLocal?.delete(clientId);
      const st = d.stores.get(tabId);
      if (st?.messages.some((m) => m.id === clientId)) {
        st.updateById(clientId, (m) => ({ ...m, sendState: 'failed', isQueued: undefined }));
      }
    }
    d.syncQueuedBadges(tabId);
  };
  d.chat.streamChat(
    request,
    (ev) => {
      if (ev.type !== 'accepted') return;   // a turn it started renders from the subscription
      acceptedHere = true;
      const t = d.tabs().get(tabId);
      if (!t) return;
      t._unsentRequests?.delete(clientId);
      if ((ev as { queued?: boolean }).queued) t._queuedLocal?.set(clientId, Date.now());
      else t._queuedLocal?.delete(clientId);   // it runs now: not queued
      d.syncQueuedBadges(tabId);
    },
    () => settle(),
    () => settle(),
    () => settle(),
    d.stream.liveness(tabId),
  );
}

/** The composer's Send. */
export async function sendMessage(d: TurnControlDeps): Promise<void> {
  const messageText = d.composer.text();
  const currentAttachments = d.composer.attachments();
  const hasText = messageText.trim().length > 0;
  const hasAttachments = currentAttachments.some((a) => !a.error && !a.isLoading);
  const agentId = d.agentId();
  if ((!hasText && !hasAttachments) || !agentId) return;

  const tabs = d.tabs();
  const guardTab = tabs.get(d.activeTabId() ?? '');
  // Session creation in flight: the tab's first POST is open and it has no
  // session yet; once it has one, the queue path below takes the send.
  if (!guardTab?.sessionId && guardTab?.localSending) return;
  // A send of this tab is still reading its attachments (its draft is still in
  // the composer): a second Send now would send the same text twice.
  if (guardTab?._preparingSend) return;

  // Busy: the message is the server's to queue (chat-turn-liveness S3). It never
  // takes over this tab's stream handlers; the queued turn arrives as its own
  // (drained) turn on the session subscription.
  if (guardTab?.sessionId && shouldQueueSend(guardTab)) {
    void sendWhileBusy(d, guardTab.id, messageText, currentAttachments);
    return;
  }

  // The send belongs to the tab it started in, whatever is active after the await.
  const sendTabId = d.activeTabId() ?? undefined;
  if (sendTabId && !tabs.has(sendTabId)) d.initTab(sendTabId);
  // The request is open now (synchronously): a second rapid Send is caught by
  // the queue guard above (shouldQueueSend reads localSending).
  d.setLocalSending(true, sendTabId);
  // This send's token: Stop (or a newer send) replaces it, and the send stops
  // after its await without writing anything (chat-turn-liveness P2a).
  const sendToken = Date.now() + Math.random();
  const sendTab = sendTabId ? d.tabs().get(sendTabId) : undefined;
  if (sendTab) sendTab._sendToken = sendToken;
  const sendSid = sendTab?.sessionId;
  const stillThisSend = () => {
    const t0 = sendTabId ? d.tabs().get(sendTabId) : undefined;
    return !!t0 && t0._sendToken === sendToken && t0.sessionId === sendSid;
  };

  // A fresh send: flags of a previous (stopped) stream must not suppress errors
  // or break indicators of this one.
  if (guardTab) {
    guardTab.userStopped = false;
    guardTab.hasReceivedData = false;
    guardTab.isReconnecting = false;
    guardTab.reconnectionAttempt = 0;
    guardTab.isResuming = false;
    guardTab.turnPhase = undefined;
    guardTab._postDisconnectUncertain = false;
  }

  if (messageText.trim().startsWith('/plugin')) {
    d.setLocalSending(false, sendTabId);
    d.composer.clearText();
    await d.pluginCommand(messageText.trim());
    return;
  }

  // The draft leaves the composer now (this tab is the active one); a send that
  // does not happen gives it back to this tab, never elsewhere.
  d.composer.clear();
  let content: ContentBlock[] = [];
  let failed = false;
  const prep = sendToken;
  if (sendTab) sendTab._preparingSend = prep;
  try {
    content = await d.buildContent(messageText, currentAttachments);
  } catch (err) {
    console.error('[ChatPage] send: reading attachments failed', err);
    failed = true;
  } finally {
    if (sendTab && sendTab._preparingSend === prep) sendTab._preparingSend = undefined;
  }
  // Failed, stopped, closed or superseded while the attachments were read:
  // nothing of this send is written and no POST opens (Stop already cleared
  // localSending); the draft goes back to its tab.
  if (failed || !stillThisSend() || content.length === 0) {
    if (stillThisSend()) d.setLocalSending(false, sendTabId);
    if (sendTabId) d.composer.restore(sendTabId, messageText, currentAttachments);
    return;
  }
  // Terminal chips of THIS message: the same value rides the send and any
  // reconnect resend of it (it is part of the message, not one-shot state).
  const sendTerminal = terminalContextOf(currentAttachments);

  const displayText = hasText ? messageText : '[Attachments]';
  const userMessageContent: ContentBlock[] = [{ type: 'text', text: displayText }];
  if (hasAttachments && hasText) {
    userMessageContent.push({ type: 'text', text: `📎 ${currentAttachments.map((a) => a.name).join(', ')}` });
  }
  // The message's own client id: sent to the backend, echoed back, it correlates
  // the optimistic rows with the persisted ones.
  const clientId = d.newClientId();
  const userMessage: Message = { id: clientId, role: 'user', content: userMessageContent, timestamp: new Date().toISOString() };
  d.incrementStreamGen();
  if (sendTabId) {
    d.setTabStatus(sendTabId, 'streaming');
    if (messageText.trim()) d.titleFromFirstMessage(sendTabId, messageText);
  }

  // The placeholder shares the turn's clientId ("-asst"): MessageStore correlates
  // it with the persisted assistant row (metadata.client_id `{clientId}-asst`).
  const assistantMessageId = `${clientId}-asst`;
  const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
  if (sendTabId && !d.tabs().has(sendTabId)) d.initTab(sendTabId);
  // SINGLE-WRITER: the optimistic rows go through the tab's store.
  if (sendTabId) d.stores.getOrCreate(sendTabId).appendMany([userMessage, assistantPlaceholder]);
  else d.setMessages((prev) => [...prev, userMessage, assistantPlaceholder]);

  // The session STRICTLY from this tab's own state, never the active tab's: a tab
  // with no session yet sends undefined and the backend creates one for it.
  const resolvedSessionId = sendTabId ? d.tabs().get(sendTabId)?.sessionId : undefined;
  const editorContext = d.editorContext(sendTabId ?? null);   // the live Canvas state, read now
  const { enableSkills, enableMCP } = d.options();
  const sendRequest: ChatRequest = {
    agentId,
    ...(hasAttachments ? { content } : { message: messageText }),
    sessionId: resolvedSessionId,
    enableSkills,
    enableMCP,
    editorContext,
    ...(sendTerminal && { terminalContext: sendTerminal }),
    clientId,
  };
  if (sendTabId) d.noteSend(sendTabId, sendRequest);
  const abort = d.chat.streamChat(
    sendRequest,
    d.stream.handler(assistantMessageId, sendTabId),
    d.stream.error(assistantMessageId, sendTabId),
    d.stream.complete(sendTabId),
    d.stream.disconnect(sendTabId),
    d.stream.liveness(sendTabId),
  );
  if (sendTab) sendTab._sendToken = undefined;   // the POST is open: Stop aborts it now
  if (!sendTabId) return;

  // A reconnect re-sends THIS message: the same request (the same clientId, so a
  // re-persisted assistant row stays keyed) with this tab's own fresh handlers.
  const streamRequest: ChatRequest = { ...sendRequest };
  const retryTab = sendTabId;
  const retryStreamFn = () => {
    d.setLocalSending(true, retryTab);
    d.noteSend(retryTab, streamRequest);
    return d.chat.streamChat(
      { ...streamRequest, sessionId: d.tabs().get(retryTab)?.sessionId ?? streamRequest.sessionId },
      d.stream.handler(assistantMessageId, retryTab),
      d.stream.error(assistantMessageId, retryTab),
      d.stream.complete(retryTab),
      d.stream.disconnect(retryTab),
      d.stream.liveness(retryTab),
    );
  };
  d.updateTab(sendTabId, {
    abortController: asAbortController(abort),
    hasReceivedData: false,
    isReconnecting: false,
    reconnectionAttempt: 0,
    retryStreamFn,
    // A manual send supersedes any armed auto-resend for this tab.
    _pendingResendOnRecovery: false,
    _pendingResendAssistantId: undefined,
    _pendingResendAttempts: 0,
  });
}

/** Continue a turn that waits for an AskUserQuestion answer (the active tab's). */
export function answerQuestion(d: TurnControlDeps, toolUseId: string, answers: Record<string, string>): void {
  const tabId = d.activeTabId() ?? undefined;
  const tabState = tabId ? d.tabs().get(tabId) : undefined;
  const tabSessionId = tabState?.sessionId;
  const agentId = d.agentId();
  if (!agentId || !tabSessionId) return;
  // The session waits for this answer (activity waiting_input, the bee stays on);
  // only another request of this tab, or a turn that is not waiting, blocks it.
  if (tabState && !canContinueTurn(tabState)) return;
  if (tabId) d.dismissNotice(tabId, `ask-uq-${toolUseId}`);
  // Answer in flight: the reconcile loop must not re-surface this question while
  // the backend still reports waiting_input on it; the ref (not only React state)
  // is what the loop reads.
  if (tabState) {
    tabState._answeredToolUseId = toolUseId;
    tabState.pendingQuestion = null;
  }
  // The answers go ONTO the question block, so it renders "Answered: …" (and
  // survives a tab switch); the store is the render source, tabState.messages the
  // cold-restore seed.
  if (tabId) {
    const isIt = (b: ContentBlock) => (b as { type?: string; toolUseId?: string }).type === 'ask_user_question'
      && (b as { toolUseId?: string }).toolUseId === toolUseId;
    const writeAnswers = (msg: Message): Message => {
      if (!msg.content.some(isIt)) return msg;
      return { ...msg, content: msg.content.map((b) => (isIt(b) ? ({ ...b, answers } as typeof b) : b)) };
    };
    const hasBlock = (msg: Message) => msg.content.some(isIt);
    const store = d.stores.get(tabId);
    if (store) {
      if (!store.messages.some(hasBlock) && import.meta.env.DEV) {
        console.warn('[ChatPage] answer submitted but no ask_user_question block matched toolUseId — summary will not render',
          { toolUseId, tabId });
      }
      store.updateLast(writeAnswers, hasBlock);
      if (tabState) tabState.messages = store.messages as Message[];
    } else if (tabState) {
      tabState.messages = tabState.messages.map(writeAnswers);
    }
  }
  d.clearPendingQuestion();
  d.incrementStreamGen();
  d.setLocalSending(true, tabId);
  if (tabId) d.setTabStatus(tabId, 'streaming');
  // The continuation renders into the bubble that holds the question.
  const assistantMessageId = continuationHostId(d, tabId, (m) => m.content.some(
    (b) => (b as { type?: string; toolUseId?: string }).type === 'ask_user_question'
      && (b as { toolUseId?: string }).toolUseId === toolUseId));
  const { enableSkills, enableMCP } = d.options();
  const abort = d.chat.streamAnswerQuestion(
    { agentId, sessionId: tabSessionId, toolUseId, answers, enableSkills, enableMCP },
    d.stream.handler(assistantMessageId, tabId),
    d.stream.error(assistantMessageId, tabId),
    d.stream.complete(tabId),
    d.stream.disconnect(tabId),
    d.stream.liveness(tabId),
  );
  if (!tabId) return;
  const retryTab = tabId;
  const retryStreamFn = () => d.chat.streamAnswerQuestion(
    { agentId, sessionId: d.tabs().get(retryTab)?.sessionId ?? tabSessionId, toolUseId, answers, enableSkills, enableMCP },
    d.stream.handler(assistantMessageId, retryTab),
    d.stream.error(assistantMessageId, retryTab),
    d.stream.complete(retryTab),
    d.stream.disconnect(retryTab),
    d.stream.liveness(retryTab),
  );
  d.updateTab(tabId, {
    abortController: asAbortController(abort),
    hasReceivedData: false,
    isReconnecting: false,
    reconnectionAttempt: 0,
    retryStreamFn,
  });
}

/**
 * Continue a turn that waits for a command-permission decision (the active tab's).
 * Approve and deny share one streaming path (/cmd-permission-continue): on deny the
 * model gets the refusal as the tool result and continues. A decision is not
 * idempotent, so a reconnect never re-sends it, and the previous message's resend
 * must not be re-sent in its place: ``retryStreamFn`` is cleared.
 */
export function decidePermission(d: TurnControlDeps, requestId: string, decision: 'approve' | 'deny'): void {
  const tabId = d.activeTabId() ?? undefined;
  const tabState = tabId ? d.tabs().get(tabId) : undefined;
  const tabSessionId = tabState?.sessionId;
  if (!tabSessionId || !d.agentId()) return;
  if (tabId && d.permissionLoading.has(tabId)) return;   // per-tab double-click guard
  if (tabState && !canContinueTurn(tabState)) return;
  if (tabId) d.permissionLoading.add(tabId);
  // Decision in flight: the mirror must not re-surface this prompt while the
  // backend still reports waiting_input on it (core/permission_prompt).
  if (tabState) tabState._decidedPermissionId = requestId;
  d.clearPendingPermission();
  const decide = (msg: Message): Message => ({
    ...msg,
    content: msg.content.map((block) => (block.type === 'cmd_permission_request' && block.requestId === requestId
      ? { ...block, decision } : block)),
  });
  const holds = (m: Message) => m.content.some((b) => b.type === 'cmd_permission_request' && b.requestId === requestId);
  d.setMessages((prev) => prev.map(decide));
  if (tabId) {
    if (tabState) {
      tabState.messages = tabState.messages.map(decide);
      tabState.pendingPermissionRequestId = null;
    }
    const store = d.stores.get(tabId);
    const target = store?.messages.find(holds);
    if (store && target) store.updateById(target.id, decide);
  }
  d.incrementStreamGen();
  d.setLocalSending(true, tabId);
  if (tabId) d.setTabStatus(tabId, 'streaming');
  // The continuation renders into the bubble that holds the prompt.
  const assistantMessageId = continuationHostId(d, tabId, holds);
  const { enableSkills, enableMCP } = d.options();
  // error / complete handlers are made when the POST settles (as before A1):
  // they read the stream generation of that moment
  const abort = d.chat.streamCmdPermissionContinue(
    { sessionId: tabSessionId, requestId, decision, enableSkills, enableMCP },
    d.stream.handler(assistantMessageId, tabId),
    (error) => { d.stream.error(assistantMessageId, tabId)(error); if (tabId) d.permissionLoading.delete(tabId); },
    () => { d.stream.complete(tabId)(); if (tabId) d.permissionLoading.delete(tabId); },
    d.stream.disconnect(tabId),
    d.stream.liveness(tabId),
  );
  if (tabId) {
    d.updateTab(tabId, {
      abortController: asAbortController(abort),
      hasReceivedData: false,
      isReconnecting: false,
      reconnectionAttempt: 0,
      retryStreamFn: undefined,
    });
  }
}

/** Stop a tab's background agents while no turn runs: the same POST /chat/stop.
 *  One at a time per tab (design 2026-10-24-bg-agent-stop-livereload D3). */
export function stopBackground(d: TurnControlDeps, tabId: string): void {
  const tab = d.tabs().get(tabId);
  const sid = tab?.sessionId;
  if (!tab || !sid || tab._stopInFlight) return;
  tab._stopInFlight = true;
  d.chat.stopSession(sid).catch((err) => {
    console.warn('[stopBackground] Backend stop failed (best effort):', err);
  }).finally(() => { tab._stopInFlight = false; });
}

const stopped = (): ContentBlock => ({ type: 'text', text: '\n\n---\n*Stopped*' });

/**
 * The Stop button (the active tab). UI feedback is synchronous; the backend stop is
 * fire-and-forget, and its receipt reaches every tab of the session on the
 * transcript feed. One Stop at a time per tab (D3): a second click while the first
 * is in flight aborts nothing again, appends no second marker, sends no second
 * request; a synchronous throw before the request goes out clears the flag.
 */
export function stopTurn(d: TurnControlDeps): void {
  const tabId = d.activeTabId() ?? undefined;
  const tab = tabId ? d.tabs().get(tabId) : undefined;
  const sid = tab?.sessionId;
  if (!sid) {
    // the first send is not accepted yet (no session): cancel this tab's request
    if (tab) {
      tab._sendToken = undefined;   // a send still reading its attachments stops
      tab._preparingSend = undefined;
      tab.userStopped = true;
      try { tab.abortController?.abort(); } catch { /* already aborted */ }
      tab.abortController = null;
    }
    if (tabId) d.setLocalSending(false, tabId);
    return;
  }
  if (!tab || !tabId || tab._stopInFlight) return;
  tab._stopInFlight = true;
  let requested = false;
  try {
    // 1. stopped + abort the stream FIRST, before anything async
    tab._sendToken = undefined;   // a send still reading its attachments stops
    tab._preparingSend = undefined;   // and a busy send still preparing
    tab.userStopped = true;
    if (tab.abortController) {
      try { tab.abortController.abort(); } catch { /* already aborted */ }
      tab.abortController = null;
    }
    // 2. end the store's streaming phase (unblocks replace / reconcile)
    d.stores.get(tabId)?.endStreaming();
    // 3. idle now, without waiting for the backend
    d.setLocalSending(false, tabId);
    d.incrementStreamGen();
    d.setTabStatus(tabId, 'idle');
    // 4. the "Stopped" marker on the last assistant message: React state, the
    // tab's message cache (direct write, no rAF) and the tab's store
    d.setMessages((prev) => {
      const last = prev.reduce((acc, m, i) => (m.role === 'assistant' ? i : acc), -1);
      if (last < 0) {
        return [...prev, { id: Date.now().toString(), role: 'assistant', content: [stopped()], timestamp: new Date().toISOString() }];
      }
      const updated = [...prev];
      updated[last] = { ...updated[last], content: [...updated[last].content, stopped()] };
      return updated;
    });
    if (tab.messages && tab.messages.length > 0) {
      const last = tab.messages.reduce((acc: number, m: Message, i: number) => (m.role === 'assistant' ? i : acc), -1);
      if (last >= 0) {
        const updated = [...tab.messages];
        updated[last] = { ...updated[last], content: [...updated[last].content, stopped()] };
        tab.messages = updated;
      }
    }
    const store = d.stores.get(tabId);
    if (store && store.messages.some((m) => m.role === 'assistant')) {
      store.updateLast((m) => ({ ...m, content: [...m.content, stopped()] }), (m) => m.role === 'assistant');
    }
    // 5. the backend stop (best effort, not awaited). Its response comes after the
    // stopped turn's products (its "files changed" card) are persisted, so the
    // Canvas Changes refresh goes here: the idle edge above fires before them.
    d.chat.stopSession(sid).then(() => { void refreshCanvasChanges(sid); }).catch((err) => {
      console.warn('[handleStop] Backend stop failed (best effort):', err);
    }).finally(() => { tab._stopInFlight = false; });
    requested = true;
  } finally {
    if (!requested) tab._stopInFlight = false;
  }
}
