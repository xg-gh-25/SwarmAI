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

import type { RefObject, MutableRefObject, ReactNode } from 'react';
import type { Agent, ChatSession } from '../../../../types';
import type { UnifiedTab, TabStatus } from '../../../../hooks/useUnifiedTabState';
import type { OpenTab } from '../../types';
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
// Component prop interfaces
// ---------------------------------------------------------------------------

/** Props for the top-level RadarSidebar shell component. */
export interface RadarSidebarProps {
  tabMapRef: RefObject<Map<string, UnifiedTab>>;
  activeTabIdRef: RefObject<string | null>;
  openTabs: OpenTab[];
  tabStatuses: Record<string, TabStatus>;
  onTabSelect: (tabId: string) => void;
  inputValueMapRef: MutableRefObject<Map<string, string>>;
  onInputValueChange: (tabId: string, value: string) => void;
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  workspaceId: string | null;
}

/** Props for the shared collapsible section wrapper. */
export interface CollapsibleSectionProps {
  name: string;
  icon: string;
  label: string;
  count: number;
  statusHint?: string;
  defaultExpanded?: boolean;
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
