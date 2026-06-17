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
}

// ---------------------------------------------------------------------------
// Store Implementation
// ---------------------------------------------------------------------------

/** Default watchdog timeout — matches SSE STALL_TIMEOUT_MS */
const DEFAULT_WATCHDOG_MS = 45_000;

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
  private _watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  private _watchdogTimeoutMs: number;
  private _pendingReconcileThunk: (() => void) | null = null;
  private _reconcileGen = 0;
  private _destroyed = false;

  // ─── Injected dependencies ───
  private _fetchMessages: ((sessionId: string) => Promise<ChatMessage[]>) | undefined;
  private _toDisplayMessage: ((msg: ChatMessage) => Message) | undefined;

  constructor(options?: MessageStoreOptions) {
    this._sessionId = options?.sessionId;
    this._watchdogTimeoutMs = options?.watchdogTimeoutMs ?? DEFAULT_WATCHDOG_MS;
    this._fetchMessages = options?.fetchMessages;
    this._toDisplayMessage = options?.toDisplayMessage;
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
    this._notify();
  }

  /**
   * Append multiple messages. Always succeeds.
   */
  appendMany(msgs: Message[]): void {
    if (this._destroyed || msgs.length === 0) return;
    this._messages = [...this._messages, ...msgs];
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

    // Mutate internally (zero-copy hot path) — rAF notify produces snapshot
    this._messages[idx] = updater(this._messages[idx]);
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
    this._messages[idx] = updater(this._messages[idx]);
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

    if (this._phase === 'streaming') {
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

    this._messages = messages;
    this._initialLoadComplete = true;
    this._notify();
  }

  /**
   * Remove messages matching a predicate. Always succeeds.
   * Used for: cancel queued, remove orphans, cleanup.
   */
  remove(predicate: (msg: Message) => boolean): void {
    if (this._destroyed) return;
    this._messages = this._messages.filter(m => !predicate(m));
    this._notify();
  }

  // ─── Phase Transitions ───

  /**
   * Enter streaming phase. Resets watchdog. Tracks streaming message ID.
   * Re-entrant: safe to call multiple times (updates target message).
   */
  startStreaming(messageId: string): void {
    if (this._destroyed) return;
    this._streamingMessageId = messageId;
    this._phase = 'streaming';
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

    try {
      const dbMessages = await this._fetchMessages(sessionId);
      // Guard: if streaming started during fetch, don't replace
      if (this._phase === 'streaming') {
        this._pendingReconcileThunk = () => this._fetchAndReconcile();
        return;
      }
      const convert = this._toDisplayMessage || this._defaultToDisplay;
      this._messages = dbMessages.map(convert);
      this._initialLoadComplete = true;
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
    this._clearWatchdog();
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._listeners.clear();
    this._messages = [];
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
   * Get a snapshot of messages (new array reference for React).
   * Use in subscription callbacks to trigger React re-render.
   */
  getSnapshot(): Message[] {
    return [...this._messages];
  }

  // ─── Private Methods ───

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
        this._flush();
      });
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
   * Watchdog — forces endStreaming() if no updateLast() for the configured timeout.
   * Prevents stuck streaming phase when SSE disconnects without close event (R3).
   */
  private _resetWatchdog(): void {
    this._clearWatchdog();
    if (this._phase === 'streaming') {
      this._watchdogTimer = setTimeout(() => {
        if (this._phase === 'streaming' && !this._destroyed) {
          console.warn('[MessageStore] Watchdog: no update for %dms, forcing endStreaming', this._watchdogTimeoutMs);
          this.endStreaming();
        }
      }, this._watchdogTimeoutMs);
    }
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
  private _applyMerge(dbMessages: ChatMessage[]): void {
    const convert = this._toDisplayMessage || this._defaultToDisplay;
    const dbConverted = dbMessages.map(convert);

    const localById = new Map(this._messages.map(m => [m.id, m]));
    const dbIds = new Set(dbConverted.map(m => m.id));

    const merged: Message[] = [];

    // Pass 1: Walk DB messages in order
    for (const dbMsg of dbConverted) {
      const localMatch = localById.get(dbMsg.id);
      if (localMatch) {
        // Streaming message always wins
        if (localMatch.id === this._streamingMessageId) {
          merged.push(localMatch);
        } else {
          // Keep local version (it may have confirmed blocks, streaming state, etc.)
          merged.push(localMatch);
        }
      } else {
        // New from DB — add it
        merged.push(dbMsg);
      }
    }

    // Pass 2: Preserve local-only messages not in DB
    // (queued messages, synthetic boundaries, resume markers)
    for (const local of this._messages) {
      if (!dbIds.has(local.id)) {
        // Local-only: insert at chronological position in merged
        const insertIdx = this._findChronologicalPosition(merged, local.timestamp);
        merged.splice(insertIdx, 0, local);
      }
    }

    this._messages = merged;
    this._initialLoadComplete = true;
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
    };
  }

  /** findLastIndex polyfill for older targets. */
  private _findLastIndex(predicate: (msg: Message) => boolean): number {
    for (let i = this._messages.length - 1; i >= 0; i--) {
      if (predicate(this._messages[i])) return i;
    }
    return -1;
  }
}

// ---------------------------------------------------------------------------
// Registry — module-level, keyed by tabId. Survives React strict mode.
// ---------------------------------------------------------------------------

const _registry = new Map<string, MessageStore>();

export const messageStoreRegistry = {
  /** Get or create a MessageStore for a tab. */
  getOrCreate(tabId: string, options?: MessageStoreOptions): MessageStore {
    let store = _registry.get(tabId);
    if (!store) {
      store = new MessageStore(options);
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
};
