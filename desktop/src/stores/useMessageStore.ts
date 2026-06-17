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

import { useState, useEffect, useRef, useMemo } from 'react';
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
 */
export function useMessageStore(
  tabId: string | null | undefined,
  options?: MessageStoreOptions,
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

  useEffect(() => {
    if (!store) return;

    // Sync immediately in case store was populated before subscription
    // (race between store creation and useEffect — see R9 in risk analysis)
    setMessages(store.getSnapshot());

    const unsub = store.subscribe(() => {
      // Only update if this is still the current store (tab may have changed)
      if (storeRef.current === store) {
        setMessages(store.getSnapshot());
      }
    });

    return unsub;
  }, [store]);

  if (!store) return null;

  return { messages, store };
}
