/**
 * MessageStore — per-tab single source of truth for chat messages.
 *
 * Replaces the fragmented message state management (45+ setMessages callers,
 * 3 storage layers, zero coordination) with a centralized store that enforces
 * phase-gated operations.
 *
 * Key invariants:
 * - reconcile() and replace() are NO-OP during streaming (queued for later)
 * - append() and updateLast() always succeed (hot path, no gate)
 * - Notifications are rAF-gated (max 1 React render per frame)
 * - Watchdog timer forces endStreaming() after 45s of no updates
 *
 * Design reference: Knowledge/Designs/2026-06-17-message-store-refactor-design.md
 *
 * @exports MessageStore — Per-tab store class
 * @exports messageStoreRegistry — Module-level registry (survives React strict mode)
 * @exports useMessageStore — React hook for subscribing to a store
 */

import type { Message, ContentBlock, ChatMessage } from '../types';
import { decideStallAction, type StallLiveness } from '../services/stallPolicy';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StorePhase = 'idle' | 'streaming';

export interface MessageStoreOptions {
  /** Session ID for DB fetches during reconcile */
  sessionId?: string;
  /** Watchdog timeout in ms (default: 45000) */
  watchdogTimeoutMs?: number;
  /** Fetch function for reconcile thunk (injected for testability) */
  fetchMessages?: (sessionId: string) => Promise<ChatMessage[]>;
  /** Convert backend ChatMessage to frontend Message (injected for testability) */
  toDisplayMessage?: (msg: ChatMessage) => Message;
  /** Loop-independent backend-liveness verdict (A2, run_d2f25153). When provided,
   *  the watchdog becomes a TRIGGER that never force-ends a live stream — it
   *  consults this via the shared {@link decideStallAction} policy, exactly like
   *  the transport stall timer (single authority, Gate-1 Finding 1/AC4). Omitted
   *  → defaults to bounded 'unknown' (a longer-but-finite version of the legacy
   *  single 90s force-end; never worse). */
  isBackendLive?: () => StallLiveness;
  /** Fired once when an alive stream is silent past the turn-liveness cap
   *  (surface a 'still working — Stop?' hint) — mirror of chat.ts onAffordance. */
  onAffordance?: () => void;
}

// ---------------------------------------------------------------------------
// Store Implementation
// ---------------------------------------------------------------------------

/** Default watchdog timeout — matches SSE STALL_TIMEOUT_MS.
 *  90s (not 45s): a cold tab injects ~47K tokens of context, and Bedrock
 *  cache-creation of that prompt makes first-token latency ~38-45s. A 45s
 *  watchdog raced that latency and force-ended streaming before the first
 *  token arrived, desyncing the lifecycle (stuck "thinking", phantom resume).
 *  Non-content SSE events (session_start, tool_use) do NOT reset this
 *  watchdog, so the full cold-start gap must fit inside the timeout. */
const DEFAULT_WATCHDOG_MS = 90_000;

export class MessageStore {
  // ─── State ───
  private _messages: Message[] = [];
  private _phase: StorePhase = 'idle';
  private _initialLoadComplete = false;
  private _streamingMessageId: string | null = null;
  private _sessionId: string | undefined;

  // ─── Internal bookkeeping ───
  private _listeners: Set<() => void> = new Set();
  private _notifying = false;
  private _dirty = false;
  private _rafId: number | null = null;
  private _fallbackTimerId: ReturnType<typeof setTimeout> | null = null;
  private _watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  private _watchdogTimeoutMs: number;
  // Diagnostic: liveness touches received during the CURRENT streaming session
  // (reset in startStreaming, ++ in touch). Logged on a watchdog fire so a fire
  // is conclusive: touches>0 = events were arriving then stopped (real silence);
  // touches=0 = NO liveness event ever reached this store while streaming (e.g.
  // heartbeats not delivered during a cold --resume → the fix is inert, look at
  // the backend/transport, not the watchdog).
  private _touchCount = 0;
  private _pendingReconcileThunk: (() => void) | null = null;
  private _reconcileGen = 0;
  private _initializeGen = 0;
  private _reconcileInFlight = 0;
  private _destroyed = false;
  private _snapshot: Message[] | null = null;
  private _snapshotSource: Message[] | null = null;
  // Monotonic content-version. Bumped by _touchVersion() on EVERY content change.
  // getSnapshot() memoizes on this (in addition to array identity) so the HOT-PATH
  // writers (updateLast/updateById) can mutate _messages IN PLACE — O(1) instead of
  // an O(n) full-array spread per streaming token (was O(n²) per message) — while
  // still invalidating React's snapshot every token. LOAD-BEARING: an in-place
  // mutation that does NOT bump _version leaves getSnapshot serving a stale array
  // (silent render loss, OT01). That is why the two are coupled in one statement.
  private _version = 0;
  private _snapshotVersion = -1;

  // ─── Resume boundary tracking ───
  // Index of the last resume_boundary system message in _messages.
  // Messages at indices <= this are from a prior session and should NOT be
  // treated as "new" content during reconcile. Prevents the resume message
  // leakage bug where old messages appear as fresh after cold resume.
  private _resumeBoundaryIdx = -1;

  // ─── Injected dependencies ───
  private _fetchMessages: ((sessionId: string) => Promise<ChatMessage[]>) | undefined;
  private _toDisplayMessage: ((msg: ChatMessage) => Message) | undefined;
  // A2 (run_d2f25153): loop-independent backend-liveness verdict + affordance cb.
  // Nullable + late-settable (setLiveness) because the verdict source is a live
  // React ref that outlives the store's construction.
  private _isBackendLive: (() => StallLiveness) | undefined;
  private _onAffordance: (() => void) | undefined;
  // Total silence (ms) accumulated across consecutive watchdog re-arms with no
  // liveness touch. Reset to 0 by touch()/startStreaming; grows by the timeout
  // per silent re-arm — the elapsed input to decideStallAction (AC7/AC8 bounds).
  private _watchdogSilentMs = 0;
  private _affordanceShown = false;

  constructor(options?: MessageStoreOptions) {
    this._sessionId = options?.sessionId;
    this._watchdogTimeoutMs = options?.watchdogTimeoutMs ?? DEFAULT_WATCHDOG_MS;
    this._fetchMessages = options?.fetchMessages;
    this._toDisplayMessage = options?.toDisplayMessage;
    this._isBackendLive = options?.isBackendLive;
    this._onAffordance = options?.onAffordance;
  }

  /** Late-inject the loop-independent liveness verdict + affordance callback
   *  (A2). The verdict comes from a live React health ref that isn't available
   *  when the per-tab store is constructed, so the lifecycle hook sets it once
   *  wired. Idempotent; safe to call with fresh closures on re-render. */
  setLiveness(isBackendLive: () => StallLiveness, onAffordance?: () => void): void {
    this._isBackendLive = isBackendLive;
    this._onAffordance = onAffordance;
  }

  // ─── Public Accessors ───

  get messages(): Message[] {
    return this._messages;
  }

  get phase(): StorePhase {
    return this._phase;
  }

  get initialLoadComplete(): boolean {
    return this._initialLoadComplete;
  }

  get streamingMessageId(): string | null {
    return this._streamingMessageId;
  }

  get sessionId(): string | undefined {
    return this._sessionId;
  }

  set sessionId(id: string | undefined) {
    this._sessionId = id;
  }

  // ─── Write Operations ───

  /**
   * Append a message to the end. Always succeeds regardless of phase.
   * Used for: user messages, assistant placeholders, synthetic boundaries.
   */
  append(msg: Message): void {
    if (this._destroyed) return;
    this._messages = [...this._messages, msg];
    this._touchVersion();
    // Track resume boundary position for filtering
    if (msg.role === 'system' && msg.id?.startsWith('resume-boundary')) {
      this._resumeBoundaryIdx = this._messages.length - 1;
    }
    // Reset watchdog on append during streaming — prevents premature
    // endStreaming() when session_cleared/boundary appends arrive during
    // long tool execution gaps (adversarial finding #7).
    if (this._phase === 'streaming') {
      this._resetWatchdog();
    }
    this._notify();
  }

  /**
   * Index of the last resume boundary marker (-1 if none).
   * Messages at or before this index are from a prior session.
   */
  get resumeBoundaryIdx(): number {
    return this._resumeBoundaryIdx;
  }

  /**
   * Append multiple messages. Always succeeds.
   */
  appendMany(msgs: Message[]): void {
    if (this._destroyed || msgs.length === 0) return;
    this._messages = [...this._messages, ...msgs];
    this._touchVersion();
    this._notify();
  }

  /**
   * Update the last message matching a predicate using an updater function.
   * HOT PATH — called per token during streaming. Uses internal mutation
   * for performance, then notifies (rAF-gated).
   */
  updateLast(updater: (msg: Message) => Message, predicate?: (msg: Message) => boolean): void {
    if (this._destroyed) return;
    const idx = predicate
      ? this._findLastIndex(predicate)
      : this._messages.length - 1;

    if (idx < 0) return;

    // HOT PATH (per token): mutate the element IN PLACE + bump the version in the
    // SAME step. No full-array spread — that was O(n) per token → O(n²) per
    // message. The updater returns a NEW message object at idx (so a snapshot
    // taken before this call keeps the old object ref — MessageBubble memo), and
    // _touchVersion() drives getSnapshot() invalidation since the array identity
    // is deliberately unchanged. These two lines are coupled: an in-place write
    // without the version bump = stale getSnapshot = silent render loss (OT01).
    this._messages[idx] = updater(this._messages[idx]);
    this._touchVersion();
    this._resetWatchdog();
    this._notify();
  }

  /**
   * Update a specific message by ID.
   */
  updateById(id: string, updater: (msg: Message) => Message): void {
    if (this._destroyed) return;
    const idx = this._messages.findIndex(m => m.id === id);
    if (idx < 0) return;
    // In-place mutate + version bump (same hot-path contract as updateLast) —
    // no full-array spread. _touchVersion() is what invalidates getSnapshot here
    // (array identity is intentionally unchanged); the two are coupled.
    this._messages[idx] = updater(this._messages[idx]);
    this._touchVersion();
    this._notify();
  }

  /**
   * Reconcile with DB messages — merge by ID, preserving local-only messages.
   * PHASE-GATED: if streaming, queues a thunk for execution on endStreaming().
   *
   * Merge strategy:
   * - Messages matched by ID: keep local version (streaming msg always wins)
   * - New from DB: append at chronological position
   * - Local-only (queued, synthetic): always preserved
   */
  reconcile(dbMessages: ChatMessage[]): void {
    if (this._destroyed) return;

    if (this._phase === 'streaming' || this._reconcileInFlight > 0) {
      // Queue a thunk that re-fetches fresh data on drain (not stale data)
      this._pendingReconcileThunk = () => this._fetchAndReconcile();
      return;
    }

    this._applyMerge(dbMessages);
  }

  /**
   * Replace all messages. Used for: initial load, tab switch restore.
   * PHASE-GATED: NO-OP during streaming (logs warning).
   */
  replace(messages: Message[]): void {
    if (this._destroyed) return;

    if (this._phase === 'streaming') {
      console.warn('[MessageStore] replace() called during streaming — ignored');
      return;
    }

    // Invalidate any in-flight reconcile fetch — prevents stale DB data
    // from overwriting these messages when the fetch resolves.
    // Critical for error handlers: endStreaming() flushes reconcile thunk,
    // then replace() sets error content. Without this, the flushed thunk's
    // async result could overwrite the error content (adversarial finding).
    this._reconcileGen++;
    const _prevClobber = this._lastAsstChars(this._messages);
    this._messages = messages;
    this._touchVersion();
    this._initialLoadComplete = true;
    this._probeClobber(_prevClobber, 'replace');
    this._notify();
  }

  /**
   * Guarded INITIAL-LOAD seed from a paginated DB fetch (OT01 rescue b, Scope B,
   * run_2aea0237). Like replace() — a full Message[] initial load, phase-gated,
   * invalidates in-flight reconciles — EXCEPT it applies the SAME persist-lag
   * "more-complete-wins" rule the reconcile path uses: if the store already holds
   * a SAME-ID last-assistant that is FULLER than the loaded one (a just-streamed
   * answer the backend hasn't finished persisting → a shorter paginated row), the
   * store's fuller content is preserved for that one message. This closes the
   * source of TabView rescue (b) (TabView.tsx:301-303): `loadSessionMessages`
   * (ChatPage.tsx) previously called `replace(formatted)` UNCONDITIONALLY, and
   * several of its 6 call sites are NOT gated on an empty store (session-open,
   * task-drag, active-restore) — so a persist-lagged shorter row could clobber a
   * fuller store. Falls through to a plain replace when the store is empty or the
   * load is not-shorter (the common case) — so it is a safe drop-in for replace()
   * at the initial-load seam. NOT for the recovery path (reconcileTabFromDb keeps
   * its unconditional replace by design — Gate-1: merging there would let a frozen
   * partial survive).
   */
  seedFromLoad(messages: Message[]): void {
    if (this._destroyed) return;
    if (this._phase === 'streaming') {
      console.warn('[MessageStore] seedFromLoad() called during streaming — ignored');
      return;
    }
    // Guard the last-assistant against a persist-lagged shorter row.
    const loaded = messages;
    const prevLastAsst = [...this._messages].reverse().find((m) => m.role === 'assistant');
    if (prevLastAsst) {
      const loadedIdx = loaded.map((m, i) => (m.role === 'assistant' ? i : -1)).filter((i) => i >= 0).pop();
      if (loadedIdx !== undefined) {
        const loadedLastAsst = loaded[loadedIdx];
        // Correlate store↔load last-assistant by exact id OR client_id — MIRRORS the
        // reconcile path (_applyMerge:846-895). The just-streamed store bubble carries
        // a client-side id `${clientId}-asst` while the persist-lagged DB row arrives
        // under its real UUID with metadata.client_id set. Exact-id-only matching
        // (Gate-2 catch, run_2aea0237) silently misses the PRE-rename race — the common
        // one — and clobbers the fuller store content. Match if: same id, OR the load's
        // client_id points at the store bubble's id, OR both carry the same client_id.
        const loadedCid = loadedLastAsst.metadata?.client_id;
        const prevCid = prevLastAsst.metadata?.client_id;
        const sameMsg =
          loadedLastAsst.id === prevLastAsst.id ||
          (!!loadedCid && (loadedCid === prevLastAsst.id || loadedCid === prevCid));
        // store fuller → keep the store's complete content for that one message
        if (sameMsg && MessageStore._textLen(prevLastAsst) > MessageStore._textLen(loadedLastAsst)) {
          const merged = loaded.slice();
          merged[loadedIdx] = MessageStore._mergePreservingInteractive(loadedLastAsst, prevLastAsst);
          this.replace(merged);
          return;
        }
      }
    }
    this.replace(loaded);
  }

  /**
   * Remove messages matching a predicate. Always succeeds.
   * Used for: cancel queued, remove orphans, cleanup.
   */
  remove(predicate: (msg: Message) => boolean): void {
    if (this._destroyed) return;
    const _prevClobber = this._lastAsstChars(this._messages);
    this._messages = this._messages.filter(m => !predicate(m));
    this._touchVersion();
    this._probeClobber(_prevClobber, 'remove');
    this._notify();
  }

  // ─── Phase Transitions ───

  /**
   * Enter streaming phase. Resets watchdog. Tracks streaming message ID.
   * Re-entrant: safe to call multiple times (updates target message).
   */
  startStreaming(messageId: string): void {
    if (this._destroyed) return;
    // Re-entrant call (reconnection retry) — old stream's queued reconcile is stale
    if (this._phase === 'streaming') {
      this._pendingReconcileThunk = null;
    }
    this._streamingMessageId = messageId;
    this._phase = 'streaming';
    this._touchCount = 0;  // diagnostic: count liveness touches this session
    this._watchdogSilentMs = 0;  // fresh turn → reset the A2 silence accumulator
    this._affordanceShown = false;
    this._resetWatchdog();
  }

  /**
   * Exit streaming phase. Flushes pending reconcile thunk.
   * Clears watchdog timer.
   */
  endStreaming(): void {
    if (this._destroyed) return;
    this._phase = 'idle';
    this._streamingMessageId = null;
    this._clearWatchdog();

    // Flush pending reconcile (as thunk — re-fetches fresh)
    if (this._pendingReconcileThunk) {
      const thunk = this._pendingReconcileThunk;
      this._pendingReconcileThunk = null;
      thunk();
    }

    // run_1a264fd1: notify subscribers of the streaming→idle transition. Without
    // this, a watchdog-fire (or any silent endStreaming) flipped _phase to idle
    // but no listener fired, so the hook's subscription never observed it and
    // tabState.isStreaming stayed true → frozen tab with no indicator. _notify
    // is rAF-gated + _destroyed-guarded, so this is safe even mid-teardown.
    this._notify();
  }

  /**
   * Liveness ping — reset the streaming watchdog WITHOUT mutating content.
   *
   * The watchdog force-ends streaming after _watchdogTimeoutMs (90s) of no
   * updateLast()/append(). A long SILENT step — a multi-minute tool, slow Opus
   * thinking — emits heartbeat (every 15s) and still_working (every 60s) SSE
   * events but NO content, so without this the watchdog fired mid-turn → the UI
   * read "done" while the backend was still working (frontend.log: repeated
   * "Watchdog: no update for 90000ms, forcing endStreaming" inside a live turn).
   * touch() lets ANY liveness signal keep the watchdog armed, so it now only
   * fires on a genuinely dead stream (no content AND no heartbeat for 90s).
   * Pure timer reset: no content change, no _notify(), and a hard NO-OP unless
   * streaming — a dropped connection stops BOTH content and heartbeats, so
   * real-disconnect detection (R3) is fully preserved.
   */
  touch(): void {
    if (this._destroyed || this._phase !== 'streaming') return;
    this._touchCount++;
    this._resetWatchdog();
  }

  /** Last assistant message's total text length. Used by the clobber probe. */
  private _lastAsstChars(msgs: Message[]): number {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.role === 'assistant') {
        return Array.isArray(m.content)
          ? m.content.reduce((n, b) => n + ('text' in b ? (b as { text: string }).text.length : 0), 0)
          : 0;
      }
    }
    return 0;
  }

  /**
   * [clobber probe] Log when a mutation sharply shrinks a COMPLETED assistant
   * answer (>=200 chars → <=20). This is the "background-completed answer
   * vanished from the store" bug (frontend.log: store had 1453 chars at turn
   * end, then 0 chars ~14s later, with no logged cause). Logging-only, zero
   * behavior change — the stack pinpoints which caller clobbered it so the fix
   * can be surgical instead of a symptom patch. Remove with the root-cause fix.
   */
  private _probeClobber(prevChars: number, reason: string): void {
    const nowChars = this._lastAsstChars(this._messages);
    if (prevChars >= 200 && nowChars <= 20) {
      console.warn('[reconcile-gap] STORE-CLOBBER', {
        sessionId: this._sessionId,
        reason,
        phase: this._phase,
        prevChars,
        nowChars,
        msgCount: this._messages.length,
        stack: new Error().stack?.split('\n').slice(2, 7).join(' | '),
      });
    }
  }

  // ─── Lifecycle ───

  /**
   * Initial DB load for a session. Sets initialLoadComplete on success.
   */
  async initialize(sessionId: string): Promise<void> {
    if (this._destroyed) return;
    this._sessionId = sessionId;

    if (!this._fetchMessages) {
      console.warn('[MessageStore] No fetchMessages function — cannot initialize');
      return;
    }

    const gen = ++this._initializeGen;
    try {
      const dbMessages = await this._fetchMessages(sessionId);
      // Guard: discard if another initialize was called, or streaming started during fetch
      if (gen !== this._initializeGen || this._destroyed) return;
      if (this._phase === 'streaming') {
        this._pendingReconcileThunk = () => this._fetchAndReconcile();
        return;
      }
      const convert = this._toDisplayMessage || this._defaultToDisplay;
      const _prevInitClobber = this._lastAsstChars(this._messages);
      this._messages = dbMessages.map(convert);
      this._touchVersion();
      this._initialLoadComplete = true;
      this._probeClobber(_prevInitClobber, 'initialize');
      this._notify();
    } catch (err) {
      console.error('[MessageStore] initialize failed:', err);
    }
  }

  /**
   * Destroy the store. Clears timers, listeners, and prevents further mutations.
   */
  destroy(): void {
    this._destroyed = true;
    // [clobber probe] A populated store being destroyed (then recreated empty
    // by the registry on next get) is a prime "answer vanished" suspect.
    const _prevDestroyChars = this._lastAsstChars(this._messages);
    if (_prevDestroyChars >= 200) {
      console.warn('[reconcile-gap] STORE-CLOBBER', {
        sessionId: this._sessionId, reason: 'destroy', phase: this._phase,
        prevChars: _prevDestroyChars, nowChars: 0, msgCount: this._messages.length,
        stack: new Error().stack?.split('\n').slice(2, 7).join(' | '),
      });
    }
    this._clearWatchdog();
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._fallbackTimerId !== null) {
      clearTimeout(this._fallbackTimerId);
      this._fallbackTimerId = null;
    }
    this._listeners.clear();
    this._messages = [];
    // Reset the snapshot memo + bump version so a post-destroy getSnapshot() can
    // never serve the stale populated snapshot (Gate-1 fix, run_ebbb7ccb): the
    // version-memo would otherwise match (_snapshotVersion===_version) and return
    // the pre-destroy array. Belt: identity also changed (new []). Suspenders:
    this._snapshot = null;
    this._snapshotSource = null;
    this._touchVersion();
  }

  // ─── Subscriptions ───

  /**
   * Subscribe to message changes. Returns unsubscribe function.
   * Notifications are rAF-gated (max 1 per animation frame).
   */
  subscribe(listener: () => void): () => void {
    this._listeners.add(listener);
    return () => { this._listeners.delete(listener); };
  }

  /**
   * Get a memoized snapshot of messages for React.
   * Returns the SAME array reference if no mutation occurred since last call.
   * This avoids 60 allocations/second during streaming (rAF-gated subscription
   * calls this every frame). React can bail out when reference is unchanged.
   */
  getSnapshot(): Message[] {
    // Invalidate on EITHER a new array identity (append/replace/… — the belt) OR
    // a content-version bump (updateLast/updateById mutate in place, so identity
    // is unchanged — the suspenders). The shallow [...] copy still happens here,
    // ONCE per change (typically once per rAF flush), freezing element refs at
    // snapshot time so React's per-message memo sees a stable ref for unchanged
    // messages and a new object only for the one that changed.
    if (this._snapshotSource !== this._messages || this._snapshotVersion !== this._version) {
      this._snapshot = [...this._messages];
      this._snapshotSource = this._messages;
      this._snapshotVersion = this._version;
    }
    return this._snapshot!;
  }

  // ─── Private Methods ───

  /**
   * Bump the content version. MUST be called by EVERY writer that changes
   * message content — it is the single source of truth for getSnapshot()
   * invalidation. New-array writers (append/replace/…) also flip _messages
   * identity (a belt), but the in-place hot-path writers (updateLast/updateById)
   * rely SOLELY on this bump (suspenders) — so routing all writes through it
   * keeps one uniform invalidation rule and stops a future writer from silently
   * going stale. Never called by touch()/watchdog (no content change).
   */
  private _touchVersion(): void {
    this._version++;
  }

  /**
   * Force-drain any pending rAF/timeout-gated notification synchronously.
   *
   * macOS App Nap throttles BOTH requestAnimationFrame AND setTimeout on a
   * backgrounded Tauri WebView, so the rAF-gated `_notify` (and even its 100ms
   * setTimeout fallback) can stall indefinitely while a tab/window is in the
   * background — the React mirror then lags the store's actual content. Call
   * this on foreground (visibilitychange → visible / window focus) to guarantee
   * listeners observe the latest snapshot immediately. No-op if nothing pending.
   */
  flush(): void {
    if (this._destroyed) return;
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._fallbackTimerId !== null) {
      clearTimeout(this._fallbackTimerId);
      this._fallbackTimerId = null;
    }
    this._flush();
  }

  /**
   * rAF-gated notification — coalesces rapid updates into one callback per frame.
   * Re-entrancy guard prevents infinite loops during migration (R8).
   */
  private _notify(): void {
    if (this._notifying || this._destroyed) return;
    this._dirty = true;

    if (this._rafId === null) {
      // In test environment (no requestAnimationFrame), fire synchronously
      if (typeof requestAnimationFrame === 'undefined') {
        this._flush();
        return;
      }
      this._rafId = requestAnimationFrame(() => {
        this._rafId = null;
        if (this._fallbackTimerId !== null) {
          clearTimeout(this._fallbackTimerId);
          this._fallbackTimerId = null;
        }
        this._flush();
      });
      // Fallback: if rAF is throttled (Tauri WebView background/AppNap),
      // force flush after 100ms. Without this, UI freezes indefinitely
      // when macOS throttles animation frames on background tabs.
      // Evidence: 2026-06-18 session response intermittent freeze.
      this._fallbackTimerId = setTimeout(() => {
        this._fallbackTimerId = null;
        if (this._rafId !== null) {
          cancelAnimationFrame(this._rafId);
          this._rafId = null;
          this._flush();
        }
      }, 100);
    }
  }

  /** Flush pending notifications to listeners. */
  private _flush(): void {
    if (!this._dirty || this._destroyed) return;
    this._dirty = false;
    this._notifying = true;
    this._listeners.forEach(fn => {
      try { fn(); } catch (e) { console.error('[MessageStore] listener error:', e); }
    });
    this._notifying = false;
  }

  /**
   * Watchdog RESET — called on real liveness (content write / touch / stream
   * start). Resets the A2 silence accumulator (a real signal arrived → the
   * stream is alive right now) and re-arms the timer from zero.
   *
   * Prevents a stuck streaming phase when SSE disconnects without a close event
   * (R3), WITHOUT blind-force-ending a live-but-silent stream (A2, Gate-1
   * Finding 1): on fire it consults the SAME loop-independent liveness verdict
   * as the transport stall timer (shared decideStallAction) — it force-ends ONLY
   * when the backend is proven dead, or 'unknown' past its bounded budget.
   */
  private _resetWatchdog(): void {
    this._watchdogSilentMs = 0;
    this._affordanceShown = false;
    this._armWatchdog();
  }

  /** Arm (or re-arm) the watchdog timer WITHOUT resetting the silence
   *  accumulator — used both by _resetWatchdog (after a real-liveness reset to 0)
   *  and by the fire path's re-arm (accumulated silence preserved). */
  private _armWatchdog(): void {
    this._clearWatchdog();
    if (this._phase !== 'streaming') return;
    this._watchdogTimer = setTimeout(() => {
      if (this._phase !== 'streaming' || this._destroyed) return;
      this._watchdogSilentMs += this._watchdogTimeoutMs;
      // A2: gate the force-end on the loop-independent liveness verdict. No
      // verdict wired (older callers / tests) → 'unknown' → bounded behaviour.
      const liveness: StallLiveness = this._isBackendLive ? this._isBackendLive() : 'unknown';
      const action = decideStallAction(liveness, this._watchdogSilentMs);
      if (action === 'rearm') {
        // Backend alive (or unknown-under-budget): keep the streaming phase, keep
        // waiting. NEVER force-end a live-but-silent stream — this is the fix.
        this._armWatchdog();
        return;
      }
      if (action === 'affordance') {
        // Alive but silent past the turn-liveness cap: surface a Stop hint once,
        // keep streaming (user decides — no auto force-end of a live backend).
        if (!this._affordanceShown) {
          this._affordanceShown = true;
          console.warn(
            `[MessageStore] Long silent turn: backend alive `
            + `${Math.round(this._watchdogSilentMs / 1000)}s with no content — offering Stop `
            + `(touches this session=${this._touchCount})`,
          );
          this._onAffordance?.();
        }
        this._armWatchdog();
        return;
      }
      // action === 'cancel' — proven dead, or unknown past budget → force-end.
      console.warn(
        `[MessageStore] Watchdog: backend not alive after `
        + `${Math.round(this._watchdogSilentMs / 1000)}s of silence, forcing endStreaming `
        + `(touches this session=${this._touchCount})`,
      );
      this.endStreaming();
    }, this._watchdogTimeoutMs);
  }

  private _clearWatchdog(): void {
    if (this._watchdogTimer) {
      clearTimeout(this._watchdogTimer);
      this._watchdogTimer = null;
    }
  }

  /**
   * Fetch fresh messages from DB and apply merge.
   * Uses generation ticket (R1) to detect phase changes during async fetch.
   */
  private async _fetchAndReconcile(): Promise<void> {
    if (!this._fetchMessages || !this._sessionId) return;

    this._reconcileInFlight++;
    const gen = ++this._reconcileGen;
    try {
      const dbMessages = await this._fetchMessages(this._sessionId);
      // Generation ticket: discard if phase changed during fetch
      if (gen !== this._reconcileGen || this._phase === 'streaming' || this._destroyed) {
        // Re-queue if we're streaming again
        if (this._phase === 'streaming') {
          this._pendingReconcileThunk = () => this._fetchAndReconcile();
        }
        return;
      }
      this._applyMerge(dbMessages);
    } catch (err) {
      console.error('[MessageStore] fetchAndReconcile failed:', err);
    } finally {
      this._reconcileInFlight--;
      // Drain a reconcile that was re-queued because it raced THIS in-flight
      // fetch. Without this, a turn-end reconcile (scheduleTurnEndReconcile) that
      // arrived with fresh full DB data while inFlight>0 would NO-OP + re-queue
      // (reconcile():209), and the thunk would never run again — no further
      // endStreaming() flushes it on a finished turn — leaving the truncated
      // streamed buffer permanently. Only drain at idle (a re-queue during
      // streaming is owned by the next endStreaming flush) and when no other
      // fetch is in flight, so the drained thunk's own inFlight++/-- can't
      // recurse here. The drained thunk re-runs _fetchAndReconcile, which at
      // idle applies the merge and does not re-queue (it only re-queues when
      // phase==='streaming', :448) → terminates.
      if (
        this._reconcileInFlight === 0 &&
        this._phase === 'idle' &&
        this._pendingReconcileThunk &&
        !this._destroyed
      ) {
        const thunk = this._pendingReconcileThunk;
        this._pendingReconcileThunk = null;
        thunk();
      }
    }
  }

  /**
   * Core merge algorithm — merge DB messages with local state by ID.
   *
   * Strategy:
   * - Walk DB messages in order (canonical chronological)
   * - For each: if local match exists by ID, keep local (unless server is newer)
   * - Streaming message ALWAYS wins (never overwritten)
   * - Local-only messages (queued, synthetic) preserved and re-inserted chronologically
   * - Messages in local but not in DB and not local-only: kept (DB may be paginated)
   */
  /**
   * Interactive content block types that the FRONTEND synthesizes from their
   * own SSE event types (ask_user_question / cmd_permission_request / escalation).
   * They are NEVER inside an `assistant` event, so the backend never persists
   * them. A client_id-matched DB row therefore lacks them — and a blind replace
   * would erase the live question/permission/escalation form. _applyMerge carries
   * these forward when the DB version of a matched assistant message omits them.
   *
   * Note on `escalation`: unlike question/permission, an escalation does NOT have
   * its own turn-end schedule call — it is raised via the compaction_guard flow,
   * which triggers a backend interrupt() and thus a SUBSEQUENT `result` event.
   * So escalation blocks are protected here against the RESULT-path reconcile
   * (the result handler schedules a reconcile that would otherwise drop the
   * synthesized escalation block). It is load-bearing, not dead.
   */
  private static readonly _INTERACTIVE_BLOCK_TYPES = new Set([
    'ask_user_question',
    'cmd_permission_request',
    'escalation',
  ]);

  /**
   * Stable identity for an interactive block (used to avoid double-adding a
   * block that the DB row DID happen to contain). Falls back to type when no
   * id field is present.
   */
  private static _interactiveBlockKey(block: ContentBlock): string {
    const b = block as unknown as Record<string, unknown>;
    const id = b.toolUseId ?? b.requestId ?? b.id;
    return `${block.type}:${id ?? ''}`;
  }

  /** Total renderable text length of a message — basis for "more-complete
   *  content wins" in the persist-lag guard below. */
  private static _textLen(m: Message): number {
    return Array.isArray(m.content)
      ? m.content.reduce((n, b) => n + ('text' in b ? (b as { text: string }).text.length : 0), 0)
      : 0;
  }

  /**
   * Merge a canonical DB assistant message with the local one it replaces,
   * carrying forward any local-only interactive blocks the DB version lacks.
   * The DB message wins for all persisted content (text/thinking/tool); only
   * the unpersisted interactive blocks are appended (preserving their order
   * relative to each other, at the end — they are always the trailing
   * turn-terminal block in practice).
   */
  private static _mergePreservingInteractive(dbMsg: Message, localMsg: Message): Message {
    // ── Persist-lag guard (THE reconcile race / #1 recurring "答到一半/白屏") ──
    // The 200ms turn-end reconcile can fetch the DB BEFORE the backend finishes
    // persisting this assistant message → a shorter/empty DB row. Plain DB-wins
    // would then OVERWRITE the complete streamed answer already in the store
    // with that stale row, truncating/blanking the reply. Rule: MORE-COMPLETE
    // CONTENT WINS. If the local message has more text than the DB row, the DB
    // is stale (persist hasn't caught up) — keep the DB's canonical id/metadata
    // but the local (complete) content. A legitimate server-side edit on a
    // completed turn only adds/keeps content (>= chars → DB still wins); a
    // content-SHORTENING edit is vanishingly rare and far less harmful than the
    // constant truncation race this kills at the source.
    if (MessageStore._textLen(localMsg) > MessageStore._textLen(dbMsg)) {
      // local already carries its own interactive blocks, so no further merge.
      return { ...dbMsg, content: localMsg.content };
    }

    const localInteractive = localMsg.content.filter((b) =>
      MessageStore._INTERACTIVE_BLOCK_TYPES.has(b.type),
    );
    if (localInteractive.length === 0) return dbMsg;

    // Index local interactive blocks by key so we can both (a) carry forward the
    // ones the DB lacks, and (b) merge local-only fields (e.g. ask_user_question
    // `answers`) onto a DB block that shares the key. Backend does NOT persist
    // ask_user_question blocks today, so in practice they're carried forward —
    // but this guards against silent answer-loss if persistence ever changes
    // (Gate-1 latent-risk hardening, run_b549e8ca).
    const localByKey = new Map(
      localInteractive.map((b) => [MessageStore._interactiveBlockKey(b), b] as const),
    );
    const dbKeys = new Set(
      dbMsg.content
        .filter((b) => MessageStore._INTERACTIVE_BLOCK_TYPES.has(b.type))
        .map((b) => MessageStore._interactiveBlockKey(b)),
    );

    // (a) Merge local-only `answers` onto matching DB interactive blocks.
    let mergedContent = dbMsg.content;
    let didMerge = false;
    mergedContent = mergedContent.map((b) => {
      if (!MessageStore._INTERACTIVE_BLOCK_TYPES.has(b.type)) return b;
      const local = localByKey.get(MessageStore._interactiveBlockKey(b));
      const localAnswers = (local as { answers?: Record<string, string> } | undefined)?.answers;
      const dbAnswers = (b as { answers?: Record<string, string> }).answers;
      if (localAnswers && !dbAnswers) {
        didMerge = true;
        return { ...b, answers: localAnswers } as typeof b;
      }
      return b;
    });

    // (b) Carry forward local interactive blocks the DB version lacks entirely.
    const carryForward = localInteractive.filter(
      (b) => !dbKeys.has(MessageStore._interactiveBlockKey(b)),
    );

    if (carryForward.length === 0 && !didMerge) return dbMsg;

    return { ...dbMsg, content: [...mergedContent, ...carryForward] };
  }

  private _applyMerge(dbMessages: ChatMessage[]): void {
    const _prevMergeClobber = this._lastAsstChars(this._messages);
    const convert = this._toDisplayMessage || this._defaultToDisplay;
    const dbConverted = dbMessages.map(convert);

    // Note: clientId matching uses dbMessages[dbIdx] directly in the loop
    // (indices are 1:1 with dbConverted since map() preserves order).

    // Boundary-aware filtering: if we have a resume boundary, identify
    // which local messages are "before boundary" (prior session content).
    // DB messages matching those IDs should not appear as "new" — they're
    // old content that the backend persisted but the user shouldn't re-see.
    const preBoundaryIds = new Set<string>();
    if (this._resumeBoundaryIdx >= 0) {
      for (let i = 0; i < this._resumeBoundaryIdx; i++) {
        if (this._messages[i]?.id) {
          preBoundaryIds.add(this._messages[i].id);
        }
      }
    }

    const localById = new Map(this._messages.map(m => [m.id, m]));
    const dbIds = new Set(dbConverted.map(m => m.id));

    // clientId correlation map: for DB messages that have metadata.client_id,
    // build a reverse lookup so we can match optimistic messages against their DB
    // counterparts. This eliminates R2/R4 duplication where optimistic ID !== DB
    // UUID. Two key sources, BOTH required (run_f62f4b80):
    //   (1) the optimistic id itself (`local-{ts}-{rand}` / `local-{cid}-asst`) —
    //       the pre-reconcile placeholder whose id IS the clientId.
    //   (2) a message's CARRIED `metadata.client_id` — the post-reconcile case: a
    //       bubble already RENAMED to a DB id on a prior reconcile no longer has a
    //       `local-*` id, but it retains its client_id here so a later mid-turn-cut
    //       reconcile (whose merged DB row id differs, e.g. A1→A3) still correlates.
    //       Without (2) the client_id fallback is a one-shot: consumed by the first
    //       rename, then a second cut duplicates the bubble.
    const clientIdToLocalIdx = new Map<string, number>();
    for (let i = 0; i < this._messages.length; i++) {
      const m = this._messages[i];
      if (m.id.startsWith('local-')) {
        clientIdToLocalIdx.set(m.id, i);            // (1) optimistic id
      }
      const carried = m.metadata?.client_id;
      // (2) carried key — do NOT clobber an existing (1) mapping for the same key.
      if (carried && !clientIdToLocalIdx.has(carried)) {
        clientIdToLocalIdx.set(carried, i);
      }
    }
    // Track which local messages were matched by clientId (to exclude from Pass 2).
    // Keyed by the local message's ARRAY INDEX (stable across id-rename), not its
    // id — a renamed bubble matched via its carried client_id must still be
    // excluded from Pass-2 re-insertion even though its id is no longer local-*.
    const matchedByClientId = new Set<string>();
    const matchedLocalIdx = new Set<number>();

    const merged: Message[] = [];

    // Pass 1: Walk DB messages in order
    // Note: _applyMerge only runs when phase=idle (streamingMessageId=null),
    // so the streaming guard below is for future safety only.
    for (let dbIdx = 0; dbIdx < dbConverted.length; dbIdx++) {
      const dbMsg = dbConverted[dbIdx];
      // The raw DB row's client_id — carried onto the merged message as a stable
      // correlation key so a FUTURE mid-turn-cut reconcile (different merged id)
      // still matches this bubble (run_f62f4b80). Applies to BOTH match branches:
      // the id-match branch too — else a no-cut reconcile (A1==A1) re-drops the
      // key via {...dbMsg}, re-arming the duplicate for the next cut.
      const dbClientId = dbMessages[dbIdx]?.metadata?.client_id;
      const carryCid = (m: Message): Message =>
        dbClientId ? { ...m, metadata: { ...(m.metadata ?? {}), client_id: dbClientId } } : m;
      const localMatch = localById.get(dbMsg.id);
      if (localMatch) {
        // Streaming message always wins (defensive — normally null here)
        if (this._streamingMessageId && localMatch.id === this._streamingMessageId) {
          merged.push(localMatch);
        } else {
          // DB is source of truth for completed messages (server-side edits propagate),
          // but carry forward local-only interactive blocks the DB never persisted
          // (ask_user_question / cmd_permission_request / escalation).
          merged.push(carryCid(MessageStore._mergePreservingInteractive(dbMsg, localMatch)));
        }
      } else {
        // No direct ID match — try clientId correlation (AC4). The DB row's
        // client_id matches either a local optimistic id (`local-*`) OR a prior
        // bubble's CARRIED client_id (already renamed to a DB id) — both indexed.
        if (dbClientId && clientIdToLocalIdx.has(dbClientId)) {
          // Match found — DB message replaces the optimistic/renamed local message.
          // DB wins (has real ID, persisted content), but carry forward any
          // local-only interactive blocks the DB never persisted + the client_id.
          matchedByClientId.add(dbClientId);
          const localIdx = clientIdToLocalIdx.get(dbClientId)!;
          matchedLocalIdx.add(localIdx);
          merged.push(carryCid(MessageStore._mergePreservingInteractive(dbMsg, this._messages[localIdx])));
        } else {
          // New from DB — but skip if it belongs to pre-boundary content
          // (prevents old messages from "leaking" into current view after resume)
          if (preBoundaryIds.has(dbMsg.id)) {
            continue; // Skip — this is prior session content
          }
          merged.push(dbMsg);
        }
      }
    }

    // H2 backstop: does the merged (DB-derived) set already contain a real,
    // non-empty assistant message? If so, any leftover EMPTY assistant
    // placeholder is stale — its content either arrived via DB or never will.
    // Dropping it prevents the ghost-empty-bubble that appears beside the real
    // reply on paths where the placeholder couldn't be correlated (continuation
    // turns pass no client_id, keep numeric placeholder ids). We require a real
    // assistant row to exist FIRST so a slow assistant-persist race never blanks
    // an in-flight turn — the placeholder survives until real content exists.
    const hasRealAssistant = merged.some(
      (m) => m.role === 'assistant' && m.content.length > 0,
    );

    // Pass 2: Preserve local-only messages not in DB
    // (queued messages, synthetic boundaries, resume markers)
    // Skip messages already matched in Pass 1: by clientId-as-id (matchedByClientId)
    // OR by carried client_id (matchedLocalIdx — a bubble already renamed to a DB
    // id whose `local.id` is no longer in matchedByClientId; without this index
    // check the renamed bubble is re-inserted → duplicate, run_f62f4b80).
    for (let localArrIdx = 0; localArrIdx < this._messages.length; localArrIdx++) {
      const local = this._messages[localArrIdx];
      if (
        !dbIds.has(local.id) &&
        !matchedByClientId.has(local.id) &&
        !matchedLocalIdx.has(localArrIdx)
      ) {
        // H2: drop a STALE assistant placeholder once a real DB assistant row
        // exists, so the turn-end reconcile finalizes to a single bubble.
        // Two cases — both gated on hasRealAssistant (never blank an in-flight
        // turn) and never the streaming message (defensive — reconcile is
        // idle-gated):
        //   (a) EMPTY placeholder — the original "Thinking forever" hang.
        //   (b) NON-EMPTY placeholder with a NUMERIC id — the continuation/drain
        //       paths (answer-question, queue-drain, permission, retry-timeout)
        //       keep numeric ids and stream content in, so at turn-end they are
        //       non-empty AND uncorrelated; without this they duplicate the DB
        //       row (adversarial HIGH, run_af36e709). A numeric-id assistant
        //       message is provably always an optimistic placeholder — every
        //       numeric-id site in ChatPage is an assistant placeholder, and no
        //       real DB id (UUID) or local id (local-*) is purely numeric — so
        //       it is safe to drop in favor of the canonical DB row.
        const isStalePlaceholder =
          hasRealAssistant &&
          local.role === 'assistant' &&
          local.id !== this._streamingMessageId &&
          (local.content.length === 0 || /^\d+$/.test(local.id));
        if (isStalePlaceholder) {
          // Before dropping, rescue any local-only interactive block this
          // placeholder carries. On answer-question / permission CONTINUATION
          // turns the placeholder has a NUMERIC id + no client_id (uncorrelated
          // in Pass 1), and the synthesized ask_user_question / cmd_permission_request
          // block lives ONLY here — never in the DB. A blind drop would erase the
          // live question/permission form (adversarial HIGH, run_59f8f5ad). Carry
          // the interactive blocks onto the LAST real DB assistant message so the
          // form survives in the canonical bubble (text content stays the DB's).
          const interactiveBlocks = local.content.filter((b) =>
            MessageStore._INTERACTIVE_BLOCK_TYPES.has(b.type),
          );
          if (interactiveBlocks.length > 0) {
            for (let i = merged.length - 1; i >= 0; i--) {
              if (merged[i].role === 'assistant' && merged[i].content.length > 0) {
                const existingKeys = new Set(
                  merged[i].content
                    .filter((b) => MessageStore._INTERACTIVE_BLOCK_TYPES.has(b.type))
                    .map((b) => MessageStore._interactiveBlockKey(b)),
                );
                const toAdd = interactiveBlocks.filter(
                  (b) => !existingKeys.has(MessageStore._interactiveBlockKey(b)),
                );
                if (toAdd.length > 0) {
                  merged[i] = { ...merged[i], content: [...merged[i].content, ...toAdd] };
                }
                break;
              }
            }
          }
          continue;
        }
        // Local-only: insert at chronological position in merged
        const insertIdx = this._findChronologicalPosition(merged, local.timestamp);
        merged.splice(insertIdx, 0, local);
      }
    }

    this._messages = merged;
    this._touchVersion();
    this._initialLoadComplete = true;
    this._probeClobber(_prevMergeClobber, 'reconcile/_applyMerge');
    this._notify();
  }

  /** Find insertion point for a message by timestamp (binary-search friendly). */
  private _findChronologicalPosition(messages: Message[], timestamp: string): number {
    // Simple linear scan — fine for <1000 messages
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].timestamp <= timestamp) {
        return i + 1;
      }
    }
    return 0;
  }

  /** Default conversion for ChatMessage → Message (minimal, for testing). */
  private _defaultToDisplay(msg: ChatMessage): Message {
    return {
      id: msg.id,
      role: msg.role as 'user' | 'assistant' | 'system',
      content: msg.content as ContentBlock[],
      timestamp: msg.createdAt,
      model: msg.model,
      // Preserve the client_id correlation key — mirrors toDisplayMessage (the
      // production converter). Harmless today (prod never injects this fallback,
      // and reconcile reads client_id from the RAW dbMessages), but keeping the
      // two converters in lockstep removes a latent trap: if this fallback is
      // ever wired as the local-bubble converter, a stripped key would silently
      // re-open the mid-turn-cut duplicate (run_03d6ee38).
      ...(msg.metadata ? { metadata: msg.metadata } : {}),
    };
  }

  /** findLastIndex polyfill for older targets. */
  private _findLastIndex(predicate: (msg: Message) => boolean): number {
    for (let i = this._messages.length - 1; i >= 0; i--) {
      if (predicate(this._messages[i])) return i;
    }
    return -1;
  }

  // ─── Persistence (crash recovery) ───

  /** Storage key prefix for crash recovery state. */
  private static readonly _PERSIST_PREFIX = 'swarm_store_';
  /** Schema version for persisted data. Bump on breaking changes. */
  private static readonly _PERSIST_VERSION = 1;
  /** Max tool_use blocks before truncating tool_result content. */
  private static readonly _LARGE_SESSION_TOOL_THRESHOLD = 80;
  /** Max chars for truncated tool_result content blocks. */
  private static readonly _TRUNCATED_CONTENT_LENGTH = 200;

  /**
   * Persist current messages to sessionStorage for crash recovery.
   * Gracefully degrades on quota exceeded. Truncates large tool_result
   * content blocks for sessions with 80+ tool calls.
   */
  persist(sessionId: string): void {
    if (this._destroyed || this._messages.length === 0) return;
    if (typeof window === 'undefined' || typeof window.sessionStorage === 'undefined') return;

    const key = `${MessageStore._PERSIST_PREFIX}${sessionId}`;
    const msgs = MessageStore._prepareForStorage(this._messages);
    const payload = {
      version: MessageStore._PERSIST_VERSION,
      messages: msgs,
      phase: this._phase,
      resumeBoundaryIdx: this._resumeBoundaryIdx,
      persistedAt: Date.now(),
    };

    try {
      window.sessionStorage.setItem(key, JSON.stringify(payload));
    } catch {
      // Quota exceeded — graceful degradation
      console.warn('[MessageStore] persist failed (quota exceeded)');
    }
  }

  /**
   * Restore messages from sessionStorage. Returns null if no valid entry.
   * Does NOT modify this store — caller decides what to do with the data.
   */
  static restore(sessionId: string): { messages: Message[]; resumeBoundaryIdx: number } | null {
    if (typeof window === 'undefined' || typeof window.sessionStorage === 'undefined') return null;

    const key = `${MessageStore._PERSIST_PREFIX}${sessionId}`;
    try {
      const raw = window.sessionStorage.getItem(key);
      if (!raw) return null;

      const parsed = JSON.parse(raw);
      if (
        !parsed ||
        parsed.version !== MessageStore._PERSIST_VERSION ||
        !Array.isArray(parsed.messages)
      ) {
        window.sessionStorage.removeItem(key);
        return null;
      }

      return {
        messages: parsed.messages,
        resumeBoundaryIdx: parsed.resumeBoundaryIdx ?? -1,
      };
    } catch {
      // Corrupted — discard
      try { window.sessionStorage.removeItem(key); } catch { /* ignore */ }
      return null;
    }
  }

  /**
   * Remove persisted state for a session (called on normal result).
   */
  static removePersisted(sessionId: string): void {
    if (typeof window === 'undefined' || typeof window.sessionStorage === 'undefined') return;
    try {
      window.sessionStorage.removeItem(`${MessageStore._PERSIST_PREFIX}${sessionId}`);
    } catch { /* ignore */ }
  }

  /**
   * Clean up stale persisted entries older than maxAgeMs.
   * Called on app startup to prevent sessionStorage bloat.
   */
  static cleanup(maxAgeMs: number = 24 * 60 * 60 * 1000): void {
    if (typeof window === 'undefined' || typeof window.sessionStorage === 'undefined') return;

    const now = Date.now();
    const keysToRemove: string[] = [];

    try {
      for (let i = 0; i < window.sessionStorage.length; i++) {
        const key = window.sessionStorage.key(i);
        if (!key?.startsWith(MessageStore._PERSIST_PREFIX)) continue;

        try {
          const raw = window.sessionStorage.getItem(key);
          if (!raw) continue;
          const parsed = JSON.parse(raw);
          if (parsed.persistedAt && (now - parsed.persistedAt) > maxAgeMs) {
            keysToRemove.push(key);
          }
        } catch {
          keysToRemove.push(key); // Corrupted — remove
        }
      }

      for (const key of keysToRemove) {
        window.sessionStorage.removeItem(key);
      }
    } catch { /* iteration failed — skip cleanup */ }
  }

  /** Truncate tool_result content for large sessions. */
  private static _prepareForStorage(messages: Message[]): Message[] {
    let toolUseCount = 0;
    for (const msg of messages) {
      for (const block of msg.content) {
        if (block.type === 'tool_use') toolUseCount++;
      }
    }

    if (toolUseCount < MessageStore._LARGE_SESSION_TOOL_THRESHOLD) return messages;

    return messages.map(msg => ({
      ...msg,
      content: msg.content.map(block => {
        if (block.type !== 'tool_result') return block;
        const raw = block as unknown as Record<string, unknown>;
        if (typeof raw.content === 'string' && raw.content.length > MessageStore._TRUNCATED_CONTENT_LENGTH) {
          return { ...block, content: (raw.content as string).slice(0, MessageStore._TRUNCATED_CONTENT_LENGTH) + '…' } as typeof block;
        }
        return block;
      }),
    }));
  }
}

// ---------------------------------------------------------------------------
// Registry — module-level, keyed by tabId. Survives React strict mode.
// ---------------------------------------------------------------------------

const _registry = new Map<string, MessageStore>();

// A2 (run_d2f25153): a registry-level default liveness verdict, applied to EVERY
// store (existing + future) via setDefaultLiveness. This is the single wiring
// point so the ~8 getOrCreate callsites don't each need to pass liveness — the
// per-tab watchdog gate (Gate-1 Finding 1) is fed from ONE authority (AC4).
let _defaultIsBackendLive: (() => StallLiveness) | undefined;
let _defaultOnAffordance: (() => void) | undefined;

export const messageStoreRegistry = {
  /** Wire the loop-independent liveness verdict once (from ChatPage's health
   *  ref). Applies to all currently-registered stores AND is inherited by any
   *  store created afterwards. Idempotent — safe to call on every render with a
   *  fresh stable closure. */
  setDefaultLiveness(isBackendLive: () => StallLiveness, onAffordance?: () => void): void {
    _defaultIsBackendLive = isBackendLive;
    _defaultOnAffordance = onAffordance;
    for (const store of _registry.values()) {
      store.setLiveness(isBackendLive, onAffordance);
    }
  },

  /** Get or create a MessageStore for a tab. */
  getOrCreate(tabId: string, options?: MessageStoreOptions): MessageStore {
    let store = _registry.get(tabId);
    if (!store) {
      store = new MessageStore(options);
      // Inherit the registry default liveness unless the caller supplied its own.
      if (_defaultIsBackendLive && !options?.isBackendLive) {
        store.setLiveness(_defaultIsBackendLive, _defaultOnAffordance);
      }
      _registry.set(tabId, store);
    }
    return store;
  },

  /** Get an existing store (returns undefined if not registered). */
  get(tabId: string): MessageStore | undefined {
    return _registry.get(tabId);
  },

  /** Destroy and remove a store (tab close). */
  destroy(tabId: string): void {
    const store = _registry.get(tabId);
    if (store) {
      store.destroy();
      _registry.delete(tabId);
    }
  },

  /** Clear all stores (app shutdown). */
  clear(): void {
    for (const store of _registry.values()) {
      store.destroy();
    }
    _registry.clear();
  },

  /** Number of active stores (diagnostic). */
  get size(): number {
    return _registry.size;
  },

  /**
   * Force-flush every registered store (foreground resume). Drains any
   * App-Nap-throttled rAF/timeout notifications so the React mirror catches up
   * to store content the moment the window returns to the foreground.
   */
  flushAll(): void {
    for (const store of _registry.values()) {
      store.flush();
    }
  },
};
