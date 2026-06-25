/**
 * useMessageStore — React hook that subscribes to a MessageStore instance.
 *
 * Provides the bridge between the module-level MessageStore (source of truth)
 * and React's rendering lifecycle. Components subscribe via this hook and
 * receive reactive updates when the store's messages change.
 *
 * Usage:
 *   const { messages, store } = useMessageStore(tabId);
 *   // messages = reactive (triggers re-render on change)
 *   // store = stable reference for imperative operations
 *
 * The store is created/retrieved from the module-level registry, ensuring
 * it survives React strict mode double-mounts and tab switches.
 *
 * @exports useMessageStore
 */

import { useState, useEffect, useLayoutEffect, useRef, useMemo } from 'react';
import { messageStoreRegistry, type MessageStoreOptions, type MessageStore } from './MessageStore';
import type { Message } from '../types';

export interface UseMessageStoreResult {
  /** Reactive messages array — triggers re-render on change */
  messages: Message[];
  /** Stable store reference for imperative operations */
  store: MessageStore;
}

/**
 * Subscribe to a per-tab MessageStore.
 *
 * @param tabId - The tab identifier (used as registry key)
 * @param options - Optional store creation options (only used on first creation)
 * @param isActive - Whether this tab is the visible/active one (default true).
 *   When false, store mutations do NOT trigger a React re-render (the tab is
 *   `display:none` and rendering it is pure waste). The store STILL receives and
 *   accumulates updates (transport unaffected); only the React commit is gated.
 *   On reactivation (false→true) the hook re-syncs to the latest snapshot BEFORE
 *   paint via useLayoutEffect, so no content is missed and there is no stale
 *   frame. This eliminates the cross-tab render saturation where N background
 *   streaming tabs each re-render their full non-virtualized list every rAF,
 *   freezing the active tab (run_5e248977). The ACTIVE tab always auto-refreshes
 *   in real-time — no tab-switch required.
 */
export function useMessageStore(
  tabId: string | null | undefined,
  options?: MessageStoreOptions,
  isActive: boolean = true,
): UseMessageStoreResult | null {
  // Get or create store from registry (stable across renders)
  const store = useMemo(() => {
    if (!tabId) return null;
    return messageStoreRegistry.getOrCreate(tabId, options);
  }, [tabId]); // eslint-disable-line react-hooks/exhaustive-deps -- options intentionally excluded (only used on creation)

  // Reactive messages state — synced from store via subscription
  const [messages, setMessages] = useState<Message[]>(() => store?.messages ?? []);

  // Track latest store to detect tabId changes
  const storeRef = useRef(store);
  storeRef.current = store;

  // Track isActive in a ref so the subscribe callback reads the LIVE value, not
  // a stale render closure (Gate-1 finding: the subscribe effect is keyed on
  // [store] only — a closure-captured isActive would keep gating on the value
  // from the last (re)subscribe, mirroring the bug avoided at
  // useChatStreamingLifecycle.ts:1093-1097). Reading from the ref also avoids
  // re-subscribe churn on every activation toggle.
  const isActiveRef = useRef(isActive);
  isActiveRef.current = isActive;

  useEffect(() => {
    if (!store) return;

    // Sync immediately in case store was populated before subscription
    // (race between store creation and useEffect — see R9 in risk analysis)
    setMessages(store.getSnapshot());

    const unsub = store.subscribe(() => {
      // Only update if this is still the current store (tab may have changed)
      // AND this tab is active. A hidden (display:none) background tab skips the
      // React commit entirely — the store keeps accumulating, the activation
      // layout-effect below re-syncs when it becomes visible.
      if (storeRef.current === store && isActiveRef.current) {
        setMessages(store.getSnapshot());
      }
    });

    return unsub;
  }, [store]);

  // On activation (isActive false→true, or store identity change while active),
  // re-sync to the latest snapshot BEFORE paint so content that arrived while
  // hidden appears immediately with no stale frame and no missed update.
  // useLayoutEffect (not useEffect) guarantees the commit happens pre-paint.
  useLayoutEffect(() => {
    if (store && isActive) {
      setMessages(store.getSnapshot());
    }
  }, [isActive, store]);

  if (!store) return null;

  return { messages, store };
}
