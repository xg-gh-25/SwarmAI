import type { ChatSession, Message, ContentBlock } from '../../types';
import { MS_PER_DAY, type TimeGroup } from './constants';

/**
 * Concatenate a page of older messages in front of the current messages,
 * merging the page boundary when both sides are assistant messages.
 *
 * The backend persists each agentic turn as a separate DB row and merges
 * consecutive assistant rows per-fetch (see `_merge_consecutive_assistant_messages`).
 * But a single agent response whose rows straddle a pagination boundary is
 * split across two fetches — the backend cannot merge across fetches. Without
 * this seam merge, "load older" would render the response as two bubbles.
 *
 * Rule: if the LAST message of `older` and the FIRST message of `current` are
 * both assistant, their content blocks are concatenated into one message that
 * keeps the older message's id/timestamp (stable anchor for the cursor) and
 * the newer message's model (most recent attribution).
 *
 * Pure function — does not mutate either input array or their messages.
 */
export function mergeOlderMessages(
  older: Message[],
  current: Message[],
): Message[] {
  if (older.length === 0) return current;
  if (current.length === 0) return older;

  const lastOlder = older[older.length - 1];
  const firstCurrent = current[0];

  if (lastOlder.role === 'assistant' && firstCurrent.role === 'assistant') {
    const mergedBoundary: Message = {
      ...lastOlder,
      content: [
        ...(lastOlder.content as ContentBlock[]),
        ...(firstCurrent.content as ContentBlock[]),
      ],
      // Newer message's model wins (most recent attribution).
      model: firstCurrent.model ?? lastOlder.model,
    };
    return [
      ...older.slice(0, -1),
      mergedBoundary,
      ...current.slice(1),
    ];
  }

  return [...older, ...current];
}

export interface GroupedSessions {
  group: TimeGroup;
  sessions: ChatSession[];
}

/**
 * Groups chat sessions by time periods (today, yesterday, this week, this month, older)
 */
export const groupSessionsByTime = (sessions: ChatSession[]): GroupedSessions[] => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - MS_PER_DAY);

  // Week starts on Monday
  const dayOfWeek = now.getDay();
  const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const weekStart = new Date(today.getTime() - mondayOffset * MS_PER_DAY);

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);

  const groups: Record<TimeGroup, ChatSession[]> = {
    today: [],
    yesterday: [],
    thisWeek: [],
    thisMonth: [],
    older: [],
  };

  for (const session of sessions) {
    const date = new Date(session.lastAccessedAt);
    const sessionDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    if (sessionDay.getTime() === today.getTime()) {
      groups.today.push(session);
    } else if (sessionDay.getTime() === yesterday.getTime()) {
      groups.yesterday.push(session);
    } else if (sessionDay >= weekStart) {
      groups.thisWeek.push(session);
    } else if (sessionDay >= monthStart) {
      groups.thisMonth.push(session);
    } else {
      groups.older.push(session);
    }
  }

  // Return only non-empty groups in order
  const order: TimeGroup[] = ['today', 'yesterday', 'thisWeek', 'thisMonth', 'older'];
  return order
    .filter((group) => groups[group].length > 0)
    .map((group) => ({ group, sessions: groups[group] }));
};

/**
 * Format timestamp for display in chat history
 */
export const formatTimestamp = (timestamp: string | undefined): string => {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / MS_PER_DAY);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
};
