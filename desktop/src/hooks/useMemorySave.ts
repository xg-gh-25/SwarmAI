/**
 * Hook for the one-click "Save to Memory" feature.
 *
 * Manages the API call to POST /api/memory/save-session, tracks loading/saved
 * state per session via `statusMap` and `toastMap`, and supports incremental
 * saves (second click only processes new messages).
 *
 * Key exports:
 * - ``useMemorySave``       — React hook returning per-session status maps and actions
 * - ``MemorySaveStatus``    — Union type for save status values
 * - ``nextMessageIdxMap``   — Module-scoped map preserving incremental save indices
 *                             across component remounts
 */
import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../services/api';
import { tf } from '../pages/chat/tf';

export type MemorySaveStatus = 'idle' | 'loading' | 'saved' | 'empty' | 'error';

interface SaveSessionResponse {
  status: 'saved' | 'empty' | 'error';
  entries: {
    key_decisions: number;
    lessons_learned: number;
    open_threads: number;
    recent_context: number;
  };
  total_saved: number;
  next_message_idx: number;
  message?: string;
}

interface UseMemorySaveReturn {
  /** Per-session status map — look up via `statusMap[sessionId] || 'idle'` */
  statusMap: Record<string, MemorySaveStatus>;
  /** Per-session toast message map — look up via `toastMap[sessionId] || null` */
  toastMap: Record<string, string | null>;
  /** Trigger a save for the given session */
  save: (sessionId: string) => Promise<void>;
  /** Reset status for a specific session back to idle */
  reset: (sessionId: string) => void;
}

type Translate = Parameters<typeof tf>[0];

/**
 * Format the save result into a human-readable toast message (localized; a
 * message the backend wrote is shown as-is).
 */
function formatToastMessage(data: SaveSessionResponse, t: Translate): string {
  const nothingNew = tf(t, 'chat.memory.nothingNew', 'Nothing new to save');
  if (data.status === 'empty') {
    return data.message || nothingNew;
  }
  if (data.status === 'error') {
    return data.message || tf(t, 'chat.memory.failed', 'Failed to save to memory');
  }
  const counts: Array<[number, string, string, string]> = [
    [data.entries.key_decisions, 'chat.memory.decisions', '{{count}} decision', '{{count}} decisions'],
    [data.entries.lessons_learned, 'chat.memory.lessons', '{{count}} lesson', '{{count}} lessons'],
    [data.entries.open_threads, 'chat.memory.threads', '{{count}} thread', '{{count}} threads'],
    [data.entries.recent_context, 'chat.memory.context', '{{count}} context', '{{count}} context'],
  ];
  const parts = counts
    .filter(([n]) => n > 0)
    .map(([count, key, one, other]) => tf(t, key, count === 1 ? one : other, { count }));

  if (parts.length === 0) {
    return nothingNew;
  }

  return tf(t, 'chat.memory.saved', 'Saved: {{parts}}', {
    parts: parts.join(tf(t, 'chat.memory.separator', ', ')),
  });
}

/**
 * Module-scoped map preserving incremental save indices across component
 * remounts. Keyed by sessionId so each session tracks its own position.
 */
const nextMessageIdxMap: Record<string, number> = Object.create(null) as Record<string, number>;

/** Create a null-prototype object — immune to prototype key collisions (e.g. "toString"). */
function nullMap<T>(): Record<string, T> {
  return Object.create(null) as Record<string, T>;
}

/** Spread into a new null-prototype object — prevents prototype pollution on updates. */
function withEntry<T>(prev: Record<string, T>, key: string, value: T): Record<string, T> {
  return Object.assign(nullMap<T>(), prev, { [key]: value });
}

export function useMemorySave(): UseMemorySaveReturn {
  const { t } = useTranslation();
  const [statusMap, setStatusMap] = useState<Record<string, MemorySaveStatus>>(nullMap);
  const [toastMap, setToastMap] = useState<Record<string, string | null>>(nullMap);

  const save = useCallback(async (sessionId: string) => {
    if (!sessionId) return;

    setStatusMap(prev => withEntry(prev, sessionId, 'loading'));
    setToastMap(prev => withEntry(prev, sessionId, null));

    try {
      const sinceIdx = nextMessageIdxMap[sessionId] || 0;

      const response = await api.post<SaveSessionResponse>('/memory/save-session', {
        session_id: sessionId,
        since_message_idx: sinceIdx,
      });

      const data = response.data;

      // Update the next message index for incremental saves
      if (data.next_message_idx > 0) {
        nextMessageIdxMap[sessionId] = data.next_message_idx;
      }

      const message = formatToastMessage(data, t);
      setToastMap(prev => withEntry(prev, sessionId, message));

      if (data.status === 'saved') {
        setStatusMap(prev => withEntry(prev, sessionId, 'saved'));
      } else if (data.status === 'empty') {
        setStatusMap(prev => withEntry(prev, sessionId, 'empty'));
      } else {
        setStatusMap(prev => withEntry(prev, sessionId, 'error'));
      }
    } catch {
      setStatusMap(prev => withEntry(prev, sessionId, 'error'));
      setToastMap(prev => withEntry(prev, sessionId, tf(t, 'chat.memory.failed', 'Failed to save to memory')));
    }
  }, [t]);

  const reset = useCallback((sessionId: string) => {
    setStatusMap(prev => {
      const next = Object.assign(nullMap<MemorySaveStatus>(), prev);
      delete next[sessionId];
      return next;
    });
    setToastMap(prev => {
      const next = Object.assign(nullMap<string | null>(), prev);
      delete next[sessionId];
      return next;
    });
  }, []);

  return { statusMap, toastMap, save, reset };
}
