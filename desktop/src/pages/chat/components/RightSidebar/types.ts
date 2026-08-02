/**
 * Shared types for the surviving RightSidebar bits after SwarmRadar was removed
 * (2026-08-02). The Radar shell + its sections are gone; these types now back the
 * ChatHeader AlertsPill (attention queue) and the History overlay.
 *
 * Key exports:
 * - ``DropPayload``       — Union type for drag-to-chat data transfer payloads
 * - ``RadarArtifact``     — Git-derived recently modified file entry
 * - ``AttentionItem`` / ``ItemClickHandler`` — the 🔔 attention queue (AlertsPill)
 * - ``RunningPipeline``   — in-flight pipeline entry (attention queue)
 * - ``HistoryViewProps``  — Props for the History mode session browser
 */

import type { Agent, ChatSession } from '../../../../types';
import type { GroupedSessions } from '../../utils';

// ---------------------------------------------------------------------------
// Drag-to-Chat payload
// ---------------------------------------------------------------------------

/**
 * Typed union describing data transferred when a Radar item is dragged onto
 * ChatInput.  Discriminated on the ``type`` field.
 */
export type DropPayload =
  | { type: 'file'; path: string; name: string }
  | { type: 'radar-todo'; id: string; title: string; context?: string }
  | { type: 'radar-artifact'; path: string; title: string };

// ---------------------------------------------------------------------------
// Artifact model (frontend representation of git-derived file entry)
// ---------------------------------------------------------------------------

/** A recently modified file in the workspace git tree. */
export interface RadarArtifact {
  path: string;
  title: string;
  type: 'code' | 'document' | 'config' | 'image' | 'other';
  modifiedAt: string;
}

// ---------------------------------------------------------------------------
// Attention queue (🔔 需要你) — Run 1 redesign
// ---------------------------------------------------------------------------

/**
 * One item in the 🔔 "需要你" attention queue. Discriminated on ``kind``.
 * The queue aggregates the three signals that genuinely need the user to act:
 * a paused pipeline (blocked on a decision), a failing scheduled job, or a
 * background tab waiting on an AskUserQuestion.
 *
 * Click semantics differ by kind (dispatched in AttentionSection):
 * - ``paused`` / ``job`` → inject a message into the current chat input (onItemClick)
 * - ``waiting`` → switch to the waiting tab (onSelectTab); the question lives there.
 */
export type AttentionItem =
  | {
      kind: 'paused';
      /** Pipeline run id (run_xxxx) — used to build the resume message. */
      id: string;
      /** Human-readable requirement (already truncated by backend). */
      title: string;
      project: string;
      /** Stage where it paused (e.g. "build"). */
      stage: string;
      /** The decision text from checkpoint.reason — WHY it needs the user. */
      reason: string;
    }
  | {
      kind: 'job';
      /** Job id. */
      id: string;
      /** Job display name. */
      title: string;
      /** Consecutive failure count (>0). */
      failures: number;
      /** WHY it failed — most recent error/summary (backend last_error), or null. */
      lastError?: string | null;
    }
  | {
      kind: 'waiting';
      /** The tab id to switch to (onSelectTab). NOT the session id. */
      id: string;
      /** Tab title / session label. */
      title: string;
      /** Short label of what it's asking (first question header, if any). */
      question: string;
    };

/** A running pipeline, shown in the bottom FYI bar (read-only, not clickable). */
export interface RunningPipeline {
  id: string;
  title: string;
  project: string;
  stage: string;
}

// ---------------------------------------------------------------------------
// Component prop interfaces
// ---------------------------------------------------------------------------

/**
 * Unified callback for item clicks across WelcomeScreen and RadarSidebar.
 * Populates ChatInput with a message and optional blockquote context.
 * Does NOT auto-send — user reviews and hits ⌘Enter.
 */
export type ItemClickHandler = (message: string, context?: string) => void;

/** Props for the History mode session browser. */
export interface HistoryViewProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  onBack: () => void;
  /**
   * Controlled search text. When BOTH `searchText` and `onSearchTextChange`
   * are provided, the search input is controlled by the parent (so the parent
   * can debounce + drive a backend FTS query). When omitted, HistoryView keeps
   * its own internal search state (legacy title-only client filter).
   */
  searchText?: string;
  onSearchTextChange?: (value: string) => void;
  /**
   * Injected search results (content FTS). When non-null, these sessions are
   * rendered as a flat "search results" list INSTEAD of the internally-filtered
   * groupedSessions. `null`/undefined → show the time-grouped fallback. An empty
   * array → render the "no matching sessions" empty state.
   */
  searchResults?: ChatSession[] | null;
  /** True while a backend search request is in flight (shows a hint). */
  isSearching?: boolean;
  /** Suppress the internal back-arrow header (the host Modal provides its own). */
  hideHeader?: boolean;
  /**
   * Row-click handler for the History overlay's read-only preview. When
   * provided, clicking a row calls this (to preview the session in-place)
   * INSTEAD of `onSelectSession`, and does NOT close the overlay. When absent,
   * row-click falls back to `onSelectSession` (legacy behavior).
   */
  onPreview?: (session: ChatSession) => void;
  /** The session id currently shown in the preview pane — highlights its row. */
  selectedSessionId?: string;
}

