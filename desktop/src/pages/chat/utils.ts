import type { ChatSession, Message, ContentBlock } from '../../types';
import { MS_PER_DAY, type TimeGroup } from './constants';
import type { PendingQuestion } from './types';

/**
 * Convert a DB-loaded message row into a display `Message`.
 *
 * Shared by ChatPage (session load / pagination / restore) AND the History
 * overlay's read-only preview — extracted here so both use one mapping.
 *
 * Marks DB-loaded text/thinking blocks as `_confirmed`: they are authoritative
 * history, and without the flag a subsequent streaming assistant event would
 * treat them as provisional and WIPE them (structural reconciliation replaces
 * unconfirmed blocks).
 */
export function toDisplayMessage(
  msg: { id: string; role: string; content: ContentBlock[]; createdAt: string; model?: string },
): Message {
  return {
    id: msg.id,
    role: msg.role as 'user' | 'assistant' | 'system',
    content: (msg.content as ContentBlock[]).map((block) =>
      (block.type === 'text' || block.type === 'thinking')
        ? { ...block, _confirmed: true }
        : block,
    ),
    timestamp: msg.createdAt,
    model: msg.model,
  };
}

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
 * Format a session timestamp for the History list as an absolute LOCAL time
 * `YYYY-MM-DD HH:MM` (24-hour, no timezone conversion, no relative "Xh ago").
 *
 * Rationale (XG, 2026-08-02): History is a scan surface — a stable, sortable,
 * unambiguous absolute stamp beats a drifting relative one. Uses local getters
 * (getFullYear/getMonth/getDate/getHours/getMinutes) so it reflects the user's
 * wall clock, matching every other absolute timestamp shown in the app.
 * Sole caller: HistoryView.
 */
export const formatTimestamp = (timestamp: string | undefined): string => {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';

  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

/**
 * Resolve the toolUseId of the question the active tab should render as
 * answerable (Root 3 / 3A — AskUserQuestion surfacing fix).
 *
 * Background: `ContentBlockRenderer` enables the AskUserQuestion submit only
 * when `pendingToolUseId === block.toolUseId`. That prop was sourced ONLY from
 * React `pendingQuestion` state, which is null on background tabs and during the
 * mid-stream stale-ref window (`setPendingQuestion` is gated by `isActiveTab` in
 * useChatStreamingLifecycle). The per-tab cache (`tabState.pendingQuestion`) is
 * populated regardless, so we fall back to it — the question stays answerable.
 *
 * PIT71/PIT74 cross-tab-leak guard: the caller MUST pass ONLY the ACTIVE tab's
 * cache as `activeTabPending`. ChatPage renders only the active tab's messages,
 * so a question from a different tab is never an input here and can never leak.
 *
 * Pure function. Returns `undefined` (never `''`) when there is no live question.
 */
export function resolvePendingToolUseId(
  reactPending: PendingQuestion | null | undefined,
  activeTabPending: PendingQuestion | null | undefined,
): string | undefined {
  const id = reactPending?.toolUseId || activeTabPending?.toolUseId;
  return id ? id : undefined;
}
