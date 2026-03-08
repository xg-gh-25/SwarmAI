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
import api from '../services/api';

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

/**
 * Format the save result into a human-readable toast message.
 */
function formatToastMessage(data: SaveSessionResponse): string {
  if (data.status === 'empty') {
    return data.message || 'Nothing new to save';
  }
  if (data.status === 'error') {
    return data.message || 'Failed to save to memory';
  }

  const parts: string[] = [];
  if (data.entries.key_decisions > 0) {
    parts.push(`${data.entries.key_decisions} decision${data.entries.key_decisions > 1 ? 's' : ''}`);
  }
  if (data.entries.lessons_learned > 0) {
    parts.push(`${data.entries.lessons_learned} lesson${data.entries.lessons_learned > 1 ? 's' : ''}`);
  }
  if (data.entries.open_threads > 0) {
    parts.push(`${data.entries.open_threads} thread${data.entries.open_threads > 1 ? 's' : ''}`);
  }
  if (data.entries.recent_context > 0) {
    parts.push(`${data.entries.recent_context} context`);
  }

  if (parts.length === 0) {
    return 'Nothing new to save';
  }

  return `Saved: ${parts.join(', ')}`;
}

/**
 * Module-scoped map preserving incremental save indices across component
 * remounts. Keyed by sessionId so each session tracks its own position.
 */
const nextMessageIdxMap: Record<string, number> = {};

export function useMemorySave(): UseMemorySaveReturn {
  const [statusMap, setStatusMap] = useState<Record<string, MemorySaveStatus>>({});
  const [toastMap, setToastMap] = useState<Record<string, string | null>>({});

  const save = useCallback(async (sessionId: string) => {
    if (!sessionId) return;

    setStatusMap(prev => ({ ...prev, [sessionId]: 'loading' }));
    setToastMap(prev => ({ ...prev, [sessionId]: null }));

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

      const message = formatToastMessage(data);
      setToastMap(prev => ({ ...prev, [sessionId]: message }));

      if (data.status === 'saved') {
        setStatusMap(prev => ({ ...prev, [sessionId]: 'saved' }));
      } else if (data.status === 'empty') {
        setStatusMap(prev => ({ ...prev, [sessionId]: 'empty' }));
      } else {
        setStatusMap(prev => ({ ...prev, [sessionId]: 'error' }));
      }
    } catch {
      setStatusMap(prev => ({ ...prev, [sessionId]: 'error' }));
      setToastMap(prev => ({ ...prev, [sessionId]: 'Failed to save to memory' }));
    }
  }, []);

  const reset = useCallback((sessionId: string) => {
    setStatusMap(prev => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
    setToastMap(prev => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
  }, []);

  return { statusMap, toastMap, save, reset };
}
