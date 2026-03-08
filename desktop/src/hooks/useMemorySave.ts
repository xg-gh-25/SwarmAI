/**
 * Hook for the one-click "Save to Memory" feature.
 *
 * Manages the API call to POST /api/memory/save-session, tracks loading/saved
 * state, and supports incremental saves (second click only processes new messages).
 */
import { useState, useCallback, useRef } from 'react';
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
  /** Current status of the save operation */
  status: MemorySaveStatus;
  /** Human-readable message for toast display */
  toastMessage: string | null;
  /** Trigger a save for the given session */
  save: (sessionId: string) => Promise<void>;
  /** Reset status back to idle (e.g., after toast dismisses) */
  reset: () => void;
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

export function useMemorySave(): UseMemorySaveReturn {
  const [status, setStatus] = useState<MemorySaveStatus>('idle');
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Track the next message index for incremental saves per session
  const nextMessageIdxRef = useRef<Record<string, number>>({});

  const save = useCallback(async (sessionId: string) => {
    if (!sessionId) return;

    setStatus('loading');
    setToastMessage(null);

    try {
      const sinceIdx = nextMessageIdxRef.current[sessionId] || 0;

      const response = await api.post<SaveSessionResponse>('/memory/save-session', {
        session_id: sessionId,
        since_message_idx: sinceIdx,
      });

      const data = response.data;

      // Update the next message index for incremental saves
      if (data.next_message_idx > 0) {
        nextMessageIdxRef.current[sessionId] = data.next_message_idx;
      }

      const message = formatToastMessage(data);
      setToastMessage(message);

      if (data.status === 'saved') {
        setStatus('saved');
      } else if (data.status === 'empty') {
        setStatus('empty');
      } else {
        setStatus('error');
      }
    } catch (err) {
      setStatus('error');
      setToastMessage('Failed to save to memory');
    }
  }, []);

  const reset = useCallback(() => {
    setStatus('idle');
    setToastMessage(null);
  }, []);

  return { status, toastMessage, save, reset };
}
