/**
 * Stores — centralized state management modules.
 *
 * MessageStore is the single source of truth for per-tab chat messages.
 * All message mutations go through the store; React components subscribe
 * via useMessageStore hook for reactive updates.
 */

export { MessageStore, messageStoreRegistry } from './MessageStore';
export type { StorePhase, MessageStoreOptions } from './MessageStore';
export { useMessageStore } from './useMessageStore';
export type { UseMessageStoreResult } from './useMessageStore';
