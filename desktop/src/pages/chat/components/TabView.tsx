/**
 * TabView — per-tab chat message-list view.
 *
 * Renders a single tab's scrollable message list: the "Load earlier messages"
 * control, the `WelcomeScreen` empty-state, the per-message bubble map
 * (MessageBubble / system divider / CompactNotice / ChatErrorMessage), the connection-state
 * indicators (reconnecting / resuming, the fallback spinner, the SESSION_BUSY
 * recovery spinner), the "back to latest" pill (only while streaming AND the
 * user has scrolled away). Where the viewport sits — following the bottom, or
 * holding what the user is reading — is decided by pages/chat/scrollFocus.ts. What the
 * agent is DOING is not rendered here — it lives in each message's WorkGroup.
 *
 * Migration Step 5.1 (this revision): TabView is now rendered ONE PER OPEN TAB
 * and kept mounted; only the active tab is visible (`display:none` otherwise).
 * It owns its OWN scroll container + bottom anchor refs and its OWN auto-scroll,
 * subscribes to its OWN per-tab `MessageStore` via `useMessageStore(tabId)`, and
 * computes its own streaming activity. Switching tabs is a visibility toggle —
 * zero remount, zero markdown re-parse. The render source is the store's LIVE
 * snapshot (`store.getSnapshot()`), read at render time — the single authority.
 * The `messages` prop (= tabState.messages) is a SECOND, persisted container
 * retained ONLY for the three narrow store-empty/store-short rescues in the
 * render-source memo below (streaming eviction / cold-start gap / same-message
 * truncation), never a free-floating fallback. Exported memoized so background
 * views skip re-render on unrelated ChatPage renders.
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
import React, { useMemo, useRef, useEffect, useLayoutEffect, useCallback, useState, memo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Message } from '../../../types';
import { useMessageStore } from '../../../stores/useMessageStore';
import { deriveTurnView, toolRow } from '../turnView';
import { pathTail } from './WorkGroup';
import {
  ANCHOR_PROPS, activationLanding, anchorChainFor, anchorDelta, isToggleClick, pickAnchor,
  REPLY_TOP_MARGIN, sentMessageId, tailErrorId,
} from '../scrollFocus';
import type { ScrollAnchor, SeenState } from '../scrollFocus';
import type { StreamingActivity, ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { Spinner } from '../../../components/common';
import { ChatErrorMessage } from '../../../components/chat';
import type { PendingQuestion } from '../types';
import { resolvePendingToolUseId } from '../utils';
import { MessageBubble } from './MessageBubble';
import { WelcomeScreen } from './WelcomeScreen';
import { CompactNotice } from './CompactNotice';
import { tf } from '../tf';
import { useStreamingRest } from '../../../hooks/useStreamingRest';

/** Bottom-proximity threshold (px): within this of the bottom counts as "at
 *  bottom" for both auto-scroll and the resize re-pin. */
export const BOTTOM_THRESHOLD = 100;

/** Stable empty-array identity for the "no store yet" render source — a shared
 *  const (never a fresh `[]` per render) so the render-source useMemo dep stays
 *  ref-stable when the store is genuinely empty. */
const EMPTY_MESSAGES: Message[] = [];

/** Last assistant message in an array (undefined if none). Module-level (not a
 *  per-render closure) — pure, no captured state, stable across renders.
 *  No-alloc tail scan (break on first assistant from the end) — NOT
 *  `[...arr].reverse().find()`: the render-source useMemo re-runs on every
 *  streaming frame (each token bumps the store version → getSnapshot() returns a
 *  new array identity → the [liveMsgs] dep changes), so a full-array copy+reverse
 *  here was paid twice per frame during streaming. The assistant is almost always
 *  the last element, so this is typically O(1). Mirrors MessageStore._lastAsstChars. */
function lastAsst(arr: Message[] | undefined): Message | undefined {
  if (!arr) return undefined;
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i].role === 'assistant') return arr[i];
  }
  return undefined;
}

/** Total renderable text length of a message's content blocks (-1 if none). Used
 *  by the same-message-truncation rescue to compare prop-vs-store completeness. */
function asstChars(m: Message | undefined): number {
  return m
    ? m.content.reduce(
        (n, b) => n + (b && typeof b === 'object' && 'text' in b ? (b as { text: string }).text.length : 0),
        0,
      )
    : -1;
}

/** A "plain" answer is text/thinking only — branch (b)'s content-swap is
 *  restricted to these so it can NEVER drop a tool_use / tool_result / interactive
 *  (ask_user_question / cmd_permission_request / escalation) block the store
 *  carries (asstChars counts text only, and the prop mirror isn't guaranteed to
 *  include those blocks). A truncated answer to rescue is text, so this loses no
 *  real coverage. */
function isPlainAnswer(m: Message | undefined): boolean {
  return !!m && Array.isArray(m.content) && m.content.every((b) => b.type === 'text' || b.type === 'thinking');
}

/** Pure: is the scroll container within BOTTOM_THRESHOLD of its bottom?
 *  Exported so the scroll-intent decision is unit-testable without a live DOM
 *  (jsdom has no layout). */
export function isAtBottom(scrollTop: number, clientHeight: number, scrollHeight: number): boolean {
  return scrollTop + clientHeight >= scrollHeight - BOTTOM_THRESHOLD;
}

/** How long after a wheel / touch / scroll-key event a scroll still counts as the
 *  user's. Trackpad momentum keeps sending wheel events, so this only has to
 *  cover the gap between the input event and the scroll it causes. */
export const USER_SCROLL_GRACE_MS = 300;

/** Keys that scroll the transcript (with focus in it or on <body>), plus Tab —
 *  focusing an off-screen element scrolls it into view. Keys typed into a field
 *  never count (see the document keydown listener). */
export const SCROLL_KEYS: ReadonlySet<string> = new Set([
  'ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ', 'Tab',
]);

/** Pure: is a scroll event happening now the user's own? True while a pointer is
 *  held on the scroller (scrollbar drag, drag-select) or within the grace window
 *  of a wheel / touch / scroll key. Only the user's scroll may leave FOLLOW. */
export function userScrollingNow(pointerHeld: boolean, intentUntil: number, now: number): boolean {
  return pointerHeld || now < intentUntil;
}

/** Pure: given the prior FOLLOW state and a scroll event's metrics, compute the
 *  next follow state. `follow` is the SINGLE authoritative "stick to bottom"
 *  signal that replaces the old wasAtBottomRef + userScrolledUpRef pair and the
 *  shouldPinOnActivation gate — the root fix for the tab-switch re-pin bug that
 *  recurred 4× (run_a319239e/75691aa8/24f98f06 …). The old design inferred
 *  "should I be at the bottom?" from scroll events via THREE scattered gates; a
 *  background STREAMING tab (display:none + isActive-gated no-render) emits NO
 *  scroll events, so those gates froze/desynced and the view stopped following
 *  the live stream on return (symptom C). Instead: DEFAULT is follow=true (pinned
 *  to bottom for BOTH streaming and idle tabs); it flips false ONLY on a GENUINE
 *  user scroll-up and flips back true when the user returns to the bottom.
 *
 *  The reflow-safety rule is preserved from the old nextScrollIntent (Gate-2 HIGH):
 *  follow changes ONLY when scrollTop ACTUALLY MOVED — a reflow that grows
 *  scrollHeight with an unchanged scrollTop (e.g. Canvas opening shrinks width →
 *  content reflows taller) must NOT be misread as a user scroll-up, or it would
 *  self-suppress the very follow it should keep. Prod's handleScroll delegates
 *  here so the tested rule IS the shipped rule (no re-derivation / test-theater).
 *  While following, handleScroll only gets here for a scroll the USER made
 *  (userScrollingNow); a scroll the engine made re-pins instead. */
export function nextFollowState(
  prev: { follow: boolean; lastScrollTop: number },
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
): { follow: boolean; lastScrollTop: number } {
  const atBottom = isAtBottom(scrollTop, clientHeight, scrollHeight);
  const moved = scrollTop !== prev.lastScrollTop;
  return {
    // Only a genuine scroll (scrollTop moved) can change follow; a reflow-grow
    // keeps the prior state. On a real move, follow === "am I at the bottom now".
    follow: moved ? atBottom : prev.follow,
    lastScrollTop: moved ? scrollTop : prev.lastScrollTop,
  };
}

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
  /** This tab's history is being fetched: an empty tab shows a spinner, not the
   *  welcome screen (the list stays mounted; ChatPage no longer swaps it out). */
  isLoadingHistory?: boolean;
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

/** Each bubble's React key (and its view-memory scope): the client id when the row
 *  carries one, so the stream-end reconcile — which swaps the live bubble's client id
 *  for the DB row's id and keeps the client id in `metadata.client_id` — does not
 *  remount the turn (its 2s fold lingers and open choices live in the mounted view).
 *  Role-prefixed. When several rows share a client id (a continued or cut turn), the
 *  LAST one — the live tail, the one that is ending — keeps the client-id key; the
 *  earlier ones are keyed by their row id, so keys stay unique. */
export function bubbleKeys(messages: Array<{ id: string; role: string; metadata?: { client_id?: string } }>): Map<string, string> {
  const keys = new Map<string, string>();
  const used = new Set<string>();
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    // The live bubble's id IS its client id; the DB row carries it in metadata.
    const preferred = `${msg.role}:${msg.metadata?.client_id ?? msg.id}`;
    const key = used.has(preferred) ? `id:${msg.id}` : preferred;
    used.add(key);
    keys.set(msg.id, key);
  }
  return keys;
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
  isLoadingHistory = false,
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

  // ── Per-tab store subscription — SINGLE render source (the live snapshot) ──
  // The rendered list is driven by this tab's own MessageStore, read at render
  // time via `sub.store.getSnapshot()` — the store's LIVE, ref-stable snapshot.
  // Every keep-mounted TabView (active AND background) has its own subscription,
  // and every session-load path seeds the store, so the store is the consistent
  // superset authority.
  //
  // Why getSnapshot() and NOT `sub.messages`: `sub.messages` is a React-state
  // MIRROR synced FROM the store by the subscription callback (useMessageStore),
  // but that callback only fires while the tab is active AND the subscription
  // keeps notifying. If the SSE is torn down mid-stream (backend EVICTION: the
  // session vanishes from the states map → no more notify), the mirror is left
  // STALE-EMPTY while the store's own `_messages` is intact — the render then
  // read the empty mirror and painted a blank spinning bubble until the 90s
  // watchdog (the "卡18分钟" freeze). Reading the live snapshot directly makes
  // the mirror-vs-live divergence structurally impossible: there is ONE store
  // source, not two views of it. `sub.messages` still exists in useMessageStore
  // as the isActive-gated RE-RENDER TRIGGER (its ONLY remaining job) — the
  // background (display:none) tab must NOT re-render its non-virtualized list on
  // every store rAF notify (run_5e248977), and on activation useLayoutEffect
  // re-syncs the state → re-render → this render reads the fresh snapshot.
  // getSnapshot() is memoized on the store's version (rebuilt only when
  // `_messages` changes), so it is ref-stable across renders → the useMemo below
  // holds, and the keep-mounted "same array ref → no re-render" property is kept.
  const sub = useMessageStore(tabId, undefined, isActive);
  const liveMsgs = sub?.store?.getSnapshot() ?? EMPTY_MESSAGES;

  // ── Render source: LIVE STORE, with a narrow PROP RESCUE (dual-container). ──
  // The live store snapshot (`liveMsgs`) is the authority. The prop
  // (`messagesProp` = tabState.messages) is a SECOND container (persisted in the
  // tab map, independent of the module-level store) — retained NOT as a general
  // fallback but for the three cases where the store legitimately does not yet
  // hold the content the prop does. It only LAGS the store in steady state;
  // it LEADS only in these guarded rescues:
  //   (s) STREAMING SAFETY NET: the store has NO assistant message at all (`!sa`)
  //       but the prop does → eviction/recovery hop where the store is empty. A
  //       real in-flight turn ALWAYS carries an assistant placeholder, so `!sa`
  //       while streaming is never a live turn — render the prop, not a blank
  //       spinning bubble ("卡18分钟" fix).
  //   (a) STARTUP GAP (idle): cold start — the module-level store lazy-loads
  //       empty/short while the restored (persisted) prop already holds the full
  //       last answer, before loadSessionMessages seeds the store.
  //   (b) SAME-MESSAGE truncation (idle): store's last assistant and the prop's
  //       are the SAME id but the store loaded a shorter copy with NO interactive
  //       block → swap in the prop's fuller content for that one message only.
  // The same-id / isPlainAnswer guards keep a longer OLDER/different prop turn
  // from clobbering a newer store turn and never drop a live question/permission/
  // escalation block. Symmetric with MessageStore._mergePreservingInteractive.
  //
  // Memoized on [liveMsgs, messagesProp, isStreaming]: getSnapshot() returns a
  // new array identity ONLY on a store content change, so this whole derivation
  // (formerly recomputed every render — 4× `[...arr].reverse()`) now recomputes
  // only when a source actually changes, not on every streaming rAF frame.
  const messages = useMemo<Message[]>(() => {
    const sa = lastAsst(liveMsgs);
    const pa = lastAsst(messagesProp);
    if (isStreaming) {
      // (s) streaming safety net — only when the store carries no assistant at all.
      return (!sa && pa) ? messagesProp : liveMsgs;
    }
    if (!sa && pa) {
      // (a) startup gap — store has no assistant yet, render the restored prop.
      return messagesProp;
    }
    if (sa && pa && sa.id === pa.id && asstChars(pa) > asstChars(sa) && isPlainAnswer(sa)) {
      // (b) same-message truncation — swap in the prop's fuller content only.
      return liveMsgs.map((m) => (m.id === sa.id ? { ...m, content: pa.content } : m));
    }
    return liveMsgs;
  }, [liveMsgs, messagesProp, isStreaming]);

  // ── Per-tab scroll (Migration Step 5.1) ────────────────────────────
  // Each keep-mounted TabView owns its OWN scroll container + bottom anchor and
  // its OWN auto-scroll, so background views keep their scroll position and the
  // active view scrolls independently. DOM is never destroyed on switch, so the
  // browser preserves scrollTop natively (no save/restore needed).
  const containerRef = useRef<HTMLDivElement>(null);
  // Inner wrapper around the message list. The ResizeObserver observes THIS (not
  // just the fixed-height scroll viewport) so content-height growth during
  // streaming — whose markdown render is 200ms-throttled (ContentBlockRenderer) —
  // triggers the re-pin (root cause B, run_24f98f06). Without it, streaming grew
  // scrollHeight with no observer fire and stranded the view above the newest msg.
  const contentRef = useRef<HTMLDivElement>(null);
  // ── Where the viewport is: FOLLOW or READING (pages/chat/scrollFocus.ts) ──
  // FOLLOW (followRef=true, the default) pins the bottom: the live work while
  // streaming, the last message when idle. READING holds `anchorRef` — the element
  // at the viewport top, or the toggle just clicked — in place while things change
  // size around it (WebKit has no CSS scroll anchoring).
  // Into FOLLOW: the tab becomes visible (switch, close, restart; XG 2026-08-03
  // choice A, kept 2026-10-18), the user sends in this tab, scrolls back to the
  // bottom, or clicks "back to latest".
  // Into READING: a scroll up the USER made (wheel / trackpad, a held pointer on
  // the scroller or its bar, a scroll key — see userScrollingNow), or a click that
  // opens or closes something outside the live card. A reply that finished while
  // the tab was hidden lands on its start when it is taller than the viewport.
  // A scroll the user did not make never leaves FOLLOW: the engine moves scrollTop
  // on its own (clamping after content shrinks, re-laying out a turn), and reading
  // such a move as "scrolled up" stranded the view above the stream after every
  // send (XG 2026-10-19). Programmatic moves also record lastScrollTopRef.
  const followRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const anchorRef = useRef<ScrollAnchor[]>([]);
  // User scroll intent: a pointer held on the scroller, or a wheel / touch / scroll
  // key within USER_SCROLL_GRACE_MS (trackpad momentum keeps sending wheel events).
  const pointerHeldRef = useRef(false);
  const userIntentUntilRef = useRef(0);
  // Direction of the last wheel: >0 down, <0 up, 0 not a wheel (key, touch,
  // pointer). Momentum after a flick DOWN must not license an upward engine move.
  const wheelDirRef = useRef(0);
  // Render mirror of "not following", for the "back to latest" pill only — the
  // pin path reads followRef.
  const [awayFromBottom, setAwayFromBottom] = useState(false);

  // ── Mount-on-first-activation (Step 5.2 / F2) ──────────────────────
  // A tab that has never been visible renders a lightweight placeholder — no
  // message list, no markdown parse — so opening the app with several restored
  // tabs does NOT parse every tab's history up front. Once activated, content
  // mounts and stays mounted (keep-mounted), so later switches are zero-parse.
  const everActiveRef = useRef(false);
  if (isActive) everActiveRef.current = true;

  const setScrollTop = useCallback((el: HTMLElement, top: number) => {
    el.scrollTop = top;
    lastScrollTopRef.current = el.scrollTop;
  }, []);

  const follow = useCallback(() => {
    followRef.current = true;
    anchorRef.current = [];
    setAwayFromBottom(false);
    const el = containerRef.current;
    if (el) setScrollTop(el, el.scrollHeight);
  }, [setScrollTop]);

  const read = useCallback((chain: ScrollAnchor[]) => {
    followRef.current = false;
    anchorRef.current = chain;
    setAwayFromBottom(true);
  }, []);

  // Keep the viewport where its mode says, after any content or size change.
  const settle = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (followRef.current) {
      setScrollTop(el, el.scrollHeight);
      return;
    }
    const delta = anchorDelta(el, anchorRef.current);
    if (delta) setScrollTop(el, el.scrollTop + delta);
  }, [setScrollTop]);

  // The first frames after activation: scroll events there are the engine
  // re-laying out a display:none container, not the user.
  const activatingRef = useRef(false);

  const markUserIntent = useCallback(() => {
    userIntentUntilRef.current = performance.now() + USER_SCROLL_GRACE_MS;
    wheelDirRef.current = 0;
  }, []);
  const handleWheel = useCallback((e: React.WheelEvent) => {
    markUserIntent();
    wheelDirRef.current = Math.sign(e.deltaY);
  }, [markUserIntent]);
  const handlePointerDown = useCallback(() => {
    pointerHeldRef.current = true;
    wheelDirRef.current = 0; // a drag can go either way
  }, []);
  useEffect(() => {
    // Released anywhere (a scrollbar drag often ends outside the scroller). A
    // context menu, a native drag or the window losing focus may swallow the
    // pointerup; clear on those too, or a stuck "held" would read every engine
    // scroll as the user's again.
    const release = () => {
      if (!pointerHeldRef.current) return;
      pointerHeldRef.current = false;
      markUserIntent();
    };
    const drop = () => { pointerHeldRef.current = false; };
    // Capture phase: a child stopping propagation must not leave it held.
    window.addEventListener('pointerup', release, true);
    window.addEventListener('pointercancel', release, true);
    window.addEventListener('contextmenu', drop);
    window.addEventListener('dragstart', drop);
    window.addEventListener('blur', drop);
    return () => {
      window.removeEventListener('pointerup', release, true);
      window.removeEventListener('pointercancel', release, true);
      window.removeEventListener('contextmenu', drop);
      window.removeEventListener('dragstart', drop);
      window.removeEventListener('blur', drop);
    };
  }, [markUserIntent]);
  useEffect(() => {
    // Scroll keys: WebKit does not focus what a click lands on, so after clicking
    // the transcript the keydown targets <body> and never passes through the
    // scroller — listen on the document. Typing in a field is not scrolling.
    if (!isActive) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (!SCROLL_KEYS.has(e.key)) return;
      const t = e.target;
      if (t instanceof HTMLElement && (t.isContentEditable || t.closest('input, textarea, select'))) return;
      markUserIntent();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [isActive, markUserIntent]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (activatingRef.current) {
      lastScrollTopRef.current = el.scrollTop;
      return;
    }
    if (followRef.current) {
      const byUser = userScrollingNow(pointerHeldRef.current, userIntentUntilRef.current, performance.now())
        // a wheel going down cannot be what moved the view up
        && !(wheelDirRef.current > 0 && el.scrollTop < lastScrollTopRef.current);
      if (!byUser) {
        // Following, and the user did not scroll: the engine moved it. Pin again.
        settle();
        return;
      }
    }
    const moved = el.scrollTop !== lastScrollTopRef.current;
    const next = nextFollowState(
      { follow: followRef.current, lastScrollTop: lastScrollTopRef.current },
      el.scrollTop, el.clientHeight, el.scrollHeight,
    );
    followRef.current = next.follow;
    lastScrollTopRef.current = next.lastScrollTop;
    if (moved) anchorRef.current = next.follow ? [] : pickAnchor(el);
    setAwayFromBottom(!next.follow);
    if (el.scrollTop === 0) onLoadOlder();
  }, [onLoadOlder, settle]);

  // A click that opens or closes something: hold what was clicked in place.
  const handleClickCapture = useCallback((e: React.MouseEvent) => {
    const el = containerRef.current;
    const btn = isToggleClick(e.target);
    if (el && btn) read(anchorChainFor(btn, el));
  }, [read]);

  // What the user last saw here, recorded when the tab is hidden.
  const seenRef = useRef<SeenState | null>(null);
  const latestRef = useRef({ messages, isStreaming });
  latestRef.current = { messages, isStreaming };
  const prevActiveRef = useRef(false);

  // Activation landing, before paint.
  useLayoutEffect(() => {
    const was = prevActiveRef.current;
    prevActiveRef.current = isActive;
    const { messages: msgs, isStreaming: streaming } = latestRef.current;
    if (!isActive) {
      if (was) seenRef.current = { replyId: lastAsst(msgs)?.id ?? null, streaming };
      return undefined;
    }
    if (was) return undefined;
    activatingRef.current = true;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => { activatingRef.current = false; });
    });
    const el = containerRef.current;
    const landing = activationLanding(seenRef.current, lastAsst(msgs)?.id ?? null, streaming);
    const row = landing.kind === 'reply' && el
      ? [...el.querySelectorAll('[data-msg-id]')].find((r) => r.getAttribute('data-msg-id') === landing.replyId)
      : undefined;
    if (el && row && row.getBoundingClientRect().height > el.clientHeight) {
      const top = row.getBoundingClientRect().top - el.getBoundingClientRect().top;
      setScrollTop(el, el.scrollTop + top - REPLY_TOP_MARGIN);
      read(anchorChainFor(row, el));
    } else {
      follow();
    }
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
      activatingRef.current = false;
    };
  }, [isActive, follow, read, setScrollTop]);

  // A message the user just sent in this tab, or an error that just ended its
  // turn, brings the view to the bottom.
  // Keyed by id AND timestamp: a second send while busy merges into the queued
  // bubble (same id, fresh timestamp) — it is still a send.
  const sentKey = (msgs: Message[]) => {
    const sid = sentMessageId(msgs);
    return sid ? `${sid}@${msgs.find((m) => m.id === sid)?.timestamp ?? ''}` : null;
  };
  const sentIdRef = useRef<string | null>(sentKey(messages));
  const errorIdRef = useRef<string | null>(tailErrorId(messages));
  useLayoutEffect(() => {
    const id = sentKey(messages);
    const err = tailErrorId(messages);
    if (isActive && ((id && id !== sentIdRef.current) || (err && err !== errorIdRef.current))) follow();
    sentIdRef.current = id;
    errorIdRef.current = err;
  }, [messages, isActive, follow]);

  useEffect(() => {
    if (!isActive) return;
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => settle());
    ro.observe(el);
    if (contentRef.current) ro.observe(contentRef.current);
    return () => ro.disconnect();
  }, [isActive, settle]);

  // Preserve scroll position when OLDER messages are prepended (load-earlier),
  // so the viewport doesn't jump. Runs before paint; compares scrollHeight
  // across the prepend and offsets scrollTop by the added height. Only fires on
  // a genuine top-prepend (first id changed AND list grew) — not on appends.
  // Declared before the settle effect, so a held anchor then measures no move.
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
      setScrollTop(el, el.scrollTop + el.scrollHeight - prevScrollHeightRef.current);
    }
    prevFirstIdRef.current = firstId;
    prevLenRef.current = messages.length;
    prevScrollHeightRef.current = el.scrollHeight;
  }, [messages, setScrollTop]);

  // New content (streaming tokens, a finished turn condensing): settle before paint.
  useLayoutEffect(() => {
    if (isActive) settle();
  }, [messages, isStreaming, isActive, settle]);

  // ── Per-tab resting cue for the streaming bee ──────────────────────
  // Keyed on the render-source `messages` identity, which changes only when
  // this tab's store CONTENT changes (heartbeat touch() never does), so the bee
  // rests after 30s of no new content while the turn is streaming. Gated on
  // isActive: a hidden tab skips the store→React commit (its `messages` freezes),
  // so an armed timer there would only force a hidden re-render every 30s. A
  // hidden tab holds no timer and starts awake when it is shown again.
  const isResting = useStreamingRest(isStreaming && isActive, messages);

  // ── Per-tab live action (Migration Step 4.1; v7.1) ─────────────────
  // Derived from THIS tab's own messages, only while streaming. Replaces the
  // former displayedActivity/elapsedSeconds props (now ignored).
  // What runs now, from the same projection the live card uses — the pill repeats
  // the card's title (one fact, once), never a step count or a second timer.
  const liveAction = useMemo(() => {
    if (!isStreaming) return null;
    const last = [...messages].reverse().find((m) => m.role === 'assistant');
    if (!last) return null;
    const units = deriveTurnView(last.content, true).units;
    const live = units[units.length - 1];
    const step = live?.kind === 'process' ? live.live?.step : undefined;
    if (!step) return null;
    // The running tool row's own words (Codex-style verb + object, never translated);
    // the tooltip says the whole command.
    const row = toolRow(step.block);
    const object = row.path ? pathTail(row.path) : row.object;
    return { text: `${row.verb} ${object}`.trim(), title: `${row.verb} ${row.command || object}`.trim() };
  }, [isStreaming, messages]);

  // Last assistant message index — used for Save-to-Memory button placement
  // and to scope streaming indicators to the final assistant bubble.
  const lastAssistantIdx = useMemo(
    () => messages.reduce((lastIdx, m, i) => m.role === 'assistant' ? i : lastIdx, -1),
    [messages],
  );

  // Last resume boundary index — messages before this render dimmed (prior
  // session). Only TRUE resume boundaries trigger dimming; same-session
  // separators do NOT dim prior context. Excluded prefixes: 'refresh-'
  // (context refresh) and 'compact-' (autocompact notice — a current-session
  // event, not a session boundary, so prior messages stay un-dimmed).
  // Start of the current turn: a "compacting" divider is live only inside it
  // (one left by an interrupted earlier turn must not claim to be running).
  const lastUserIdx = useMemo(
    () => messages.reduce((last, m, i) => (m.role === 'user' ? i : last), -1),
    [messages],
  );

  const lastResumeBoundaryIdx = useMemo(
    () => messages.reduce((lastIdx, m, i) =>
      m.role === 'system' && !m.id.startsWith('refresh-') && !m.id.startsWith('compact-')
        ? i : lastIdx, -1),
    [messages],
  );

  const body = (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      onWheel={handleWheel}
      onTouchMove={markUserIntent}
      onPointerDown={handlePointerDown}
      aria-hidden={!isActive}
      style={isActive ? undefined : { display: 'none' }}
      // Non-empty: `chat-scroll-gutter` (index.css) owns the overflow and the side
      // gutters, so the transcript lines up with the composer. Empty: the
      // WelcomeScreen is its own scroll container with the same gutter.
      className={messages.length === 0
        ? 'flex-1 overflow-hidden flex flex-col'
        : 'flex-1 chat-scroll-gutter py-3.5 min-w-0'
      }
    >
      {everActiveRef.current ? (
      // Inner content wrapper — the ResizeObserver observes THIS element so
      // content-height growth during streaming triggers the re-pin (root cause B).
      // `space-y-2.5` moved here from the scroll container: with the list wrapped
      // in one div, container-level space-y no longer reaches the message rows, so
      // the inter-message spacing lives on the wrapper (non-empty). Its width is the
      // shared `chat-column`. Empty state gets flex-col/flex-1 so WelcomeScreen's
      // h-full still fills the viewport.
      <div
        ref={contentRef}
        onClickCapture={handleClickCapture}
        className={messages.length === 0 ? 'flex-1 flex flex-col min-h-0' : 'space-y-2.5 chat-column'}
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
          {tf(t, 'chat.list.loadEarlier', '↑ Load earlier messages')}
        </button>
      )}
      {messages.length === 0 && isLoadingHistory ? (
        <div data-testid="tab-loading" className="flex-1 flex items-center justify-center">
          <Spinner size="lg" />
        </div>
      ) : messages.length === 0 ? (
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
        const keys = bubbleKeys(messages);
        return messages.map((msg, idx) => {
          // System messages render as divider lines. Three variants by id prefix:
          //   'compact-' → compaction divider + collapsible summary card
          //                (CompactNotice; live and history share the id).
          //   'refresh-' → 'Context Refreshed' (same-session context refresh)
          //   else       → 'Session Resumed' (true resume boundary)
          if (msg.role === 'system') {
            if (msg.id.startsWith('compact-')) {
              return <CompactNotice key={msg.id} message={msg} isStreaming={isStreaming && idx > lastUserIdx} />;
            }
            const isRefresh = msg.id.startsWith('refresh-');
            const label = isRefresh
              ? tf(t, 'chat.list.contextRefreshed', 'Context Refreshed')
              : tf(t, 'chat.list.sessionResumed', 'Session Resumed');
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
            const errorText = textBlock && 'text' in textBlock ? textBlock.text : tf(t, 'chat.list.errorFallback', 'An error occurred');
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
          // Connection-state indicators (reconnecting / resuming) render right
          // after the streaming assistant message, above any queued user
          // messages. What the agent is DOING lives in that message's own
          // WorkGroup (pages/chat/turnView.ts) — the one activity surface —
          // so there is no separate activity line here any more.
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
            <React.Fragment key={keys.get(msg.id)}>
              <div className={isPriorSession ? 'opacity-50' : undefined} data-msg-id={msg.id} {...ANCHOR_PROPS}>
                <MessageBubble
                  message={msg}
                  viewKey={keys.get(msg.id)}
                  onAnswerQuestion={onAnswerQuestion}
                  onPermissionDecision={onPermissionDecision}
                  onEscalationSelect={onEscalationSelect}
                  pendingToolUseId={resolvedPendingToolUseId}
                  pendingPermissionRequestId={pendingPermissionRequestId ?? undefined}
                  isStreaming={isLastAssistantForStreaming}
                  isResting={isLastAssistantForStreaming && isResting}
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
      {/* "Back to latest" pill — shown ONLY while streaming AND the user has
          scrolled away from the bottom (awayFromBottom mirrors followRef). At
          the bottom the live step is already visible in the message's
          WorkGroup, so a pill there would repeat it. CSS sticky floats it at
          the bottom of the viewport; click scrolls back to the live message. */}
      {isStreaming && lastAssistantIdx >= 0 && awayFromBottom && (
        <div data-testid="streaming-pill" className="sticky bottom-0 z-10 flex items-center justify-center py-1.5">
          <button
            type="button"
            onClick={follow}
            className="flex items-center gap-2 px-3 py-1.5 rounded-full max-w-[min(90%,640px)] min-w-0
                          bg-[var(--color-bg-primary)]/95 backdrop-blur-sm
                          border border-[var(--color-border-subtle)]
                          shadow-sm text-[var(--color-text-muted)]
                          hover:border-[var(--color-border)] hover:text-[var(--color-text-secondary)]
                          transition-colors cursor-pointer">
            <Spinner size="sm" />
            <span data-testid="streaming-pill-text" className="text-xs font-medium tabular-nums truncate min-w-0" title={liveAction?.title}>
              {liveAction
                ? tf(t, 'chat.turn.backToLatestAction', '{{action}} · back to latest', { action: liveAction.text })
                : tf(t, 'chat.turn.backToLatestThinking', 'Thinking · back to latest')}
            </span>
          </button>
        </div>
      )}
      </div>
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
