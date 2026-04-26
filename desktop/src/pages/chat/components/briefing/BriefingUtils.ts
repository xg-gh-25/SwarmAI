/**
 * Shared utilities for Briefing Hub components.
 *
 * Context builders produce blockquote strings for the ChatInput.
 * Shared across WelcomeScreen and RadarSidebar.
 *
 * @exports buildTodoContext, buildWorkingContext, buildSignalContext
 * @exports buildHotContext, openWorkspaceFile, formatRelativeTime
 */

import type {
  BriefingTodo,
  WorkingItem,
  BriefingSignal,
  HotNewsItem,
} from '../../../../services/system';

/** Build blockquote context for a todo item. */
export function buildTodoContext(todo: BriefingTodo): string {
  const lines: string[] = [];
  if (todo.priority) lines.push(`Priority: ${todo.priority}`);
  if (todo.nextStep) lines.push(`Next: ${todo.nextStep}`);
  if (todo.files?.length) lines.push(`Files: ${todo.files.join(', ')}`);
  if (todo.description) lines.push(todo.description.slice(0, 150));
  return lines.join('\n');
}

/** Build blockquote context for a working item. */
export function buildWorkingContext(item: WorkingItem): string {
  const lines: string[] = [];
  lines.push(`Source: ${item.source} · ${item.sourceDetail || ''}`);
  if (item.summary) lines.push(item.summary.slice(0, 150));
  if (item.action) lines.push(`Suggested action: ${item.action}`);
  return lines.join('\n');
}

/** Build blockquote context for a signal item. */
export function buildSignalContext(signal: BriefingSignal): string {
  const lines: string[] = [];
  if (signal.source) lines.push(`Source: ${signal.source}`);
  if (signal.summary) lines.push(signal.summary.slice(0, 150));
  if (signal.sourceUrl) lines.push(`URL: ${signal.sourceUrl}`);
  return lines.join('\n');
}

/** Build blockquote context for a hot news item. */
export function buildHotContext(item: HotNewsItem): string {
  const lines: string[] = [];
  let platformInfo = item.platform || '';
  if (item.rank) platformInfo += ` #${item.rank}`;
  if (platformInfo) lines.push(platformInfo);
  if (item.url) lines.push(`URL: ${item.url}`);
  return lines.join('\n');
}

/** Dispatch swarm:open-file event to open a file in the editor panel. */
export function openWorkspaceFile(relativePath: string): void {
  if (!relativePath) return; // guard: empty path → no-op
  document.dispatchEvent(
    new CustomEvent('swarm:open-file', { detail: { path: relativePath } }),
  );
}

/** Convert ISO timestamp to relative time string. */
export function formatRelativeTime(isoTimestamp: string): string {
  const now = Date.now();
  const then = new Date(isoTimestamp).getTime();
  if (Number.isNaN(then)) return '';

  const diffMs = now - then;
  if (diffMs < 0) return 'just now';

  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
