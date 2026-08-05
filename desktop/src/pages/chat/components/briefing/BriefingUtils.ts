/**
 * Shared utilities for Briefing Hub components.
 *
 * Context builders produce blockquote strings for the ChatInput.
 * Consumed by WelcomeScreen's Working card. (buildSignalContext /
 * openWorkspaceFile / formatRelativeTime removed 2026-08-05 with the
 * Signals / Stocks / Swarm Output sections.)
 *
 * @exports buildWorkingContext
 */

import type { WorkingItem } from '../../../../services/system';

/** Build blockquote context for a working item. */
export function buildWorkingContext(item: WorkingItem): string {
  const lines: string[] = [];
  lines.push(`Source: ${item.source} · ${item.sourceDetail || ''}`);
  if (item.summary) lines.push(item.summary.slice(0, 150));
  if (item.action) lines.push(`Suggested action: ${item.action}`);
  return lines.join('\n');
}
