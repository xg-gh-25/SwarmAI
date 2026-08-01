/**
 * Type definitions for the Radar sidebar and its sub-components.
 *
 * Key exports:
 * - ``DropPayload``              — Union type for drag-to-chat data transfer payloads
 * - ``RadarArtifact``            — Git-derived recently modified file entry
 * - ``RadarSidebarProps``        — Props for the top-level RadarSidebar shell
 * - ``CollapsibleSectionProps``  — Props for the shared expand/collapse section wrapper
 * - ``HistoryViewProps``         — Props for the History mode session browser
 * - localStorage key constants   — Canonical keys for sidebar persistence
 */

import type { ReactNode } from 'react';
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

/** Props for the top-level RadarSidebar shell component. */
export interface RadarSidebarProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  workspaceId: string | null;
  /** Current active session ID — used for Referenced Files tracking */
  sessionId?: string;
  /** Unified callback: populate ChatInput with message + context */
  onItemClick?: ItemClickHandler;
  /** Auto-send a message to the active chat tab (injects + sends immediately). */
  onSendMessage?: (text: string) => void;
  /**
   * Switch to another chat tab by id. Used by the 🔔 attention queue's
   * "waiting tab" items — the pending question lives in that tab, so the
   * correct action is to focus it, not to inject into the current input.
   */
  onSelectTab?: (tabId: string) => void;
  /**
   * Open tabs (id + sessionId) so the attention queue can map a
   * waiting session (streaming-state is keyed by session_id) back to a
   * tab id for onSelectTab, and exclude the currently-active session.
   */
  openTabs?: { id: string; sessionId?: string }[];
  /**
   * The 🔔 attention queue, polled ONCE at ChatPage (useRadarAttention) and
   * passed down — shared with the ChatHeader Alerts pill so there is a single
   * 30s poll (run_843962a5). Defaults to [] when absent.
   */
  attentionItems?: AttentionItem[];
}

/** Props for the shared collapsible section wrapper. */
export interface CollapsibleSectionProps {
  name: string;
  icon: string;
  label: string;
  count: number;
  statusHint?: string;
  defaultExpanded?: boolean;
  /** Left accent border color (CSS value). Omit for no accent. */
  accent?: string;
  children: ReactNode;
}

/** Props for the History mode session browser. */
export interface HistoryViewProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  onBack: () => void;
}

// ---------------------------------------------------------------------------
// localStorage key constants
// ---------------------------------------------------------------------------

/** Key for persisting the sidebar width (number, default 320). */
export const RADAR_SIDEBAR_WIDTH_KEY = 'radar-sidebar-width';

/** Prefix for per-section expand/collapse state (boolean). */
export const RADAR_SECTION_KEY_PREFIX = 'radar-section-';

/** Key for persisting the feature tip dismissal state (boolean). */
export const RADAR_TIP_DISMISSED_KEY = 'radar-tip-dismissed';
