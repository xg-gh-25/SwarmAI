/**
 * Artifacts section for the Radar sidebar.
 *
 * Displays recently modified files from the workspace git tree, fetched via
 * ``radarService.fetchRecentArtifacts``.  Items arrive pre-sorted from the
 * API in reverse chronological order (most recently modified first).
 *
 * Each item row includes a title (filename), a Material Symbols type icon,
 * a relative timestamp (e.g. "2m ago"), and a ``DragHandle`` with payload
 * type ``radar-artifact``.  Clicking a row invokes the ``onPreviewFile``
 * callback with the artifact path.
 *
 * Key exports:
 * - ``ArtifactsSection``     — The section component
 * - ``ARTIFACT_TYPE_ICONS``  — Type-to-Material-Symbols-icon mapping
 * - ``formatRelativeTime``   — Converts ISO timestamp to relative string
 */

import { useState, useEffect, useCallback } from 'react';
import { radarService } from '../../../../services/radar';
import { DragHandle } from './shared/DragHandle';
import type { RadarArtifact } from './types';
import type { DropPayload } from './types';

/**
 * Open a file in the FileEditorPanel via the global ``swarm:open-file`` event.
 *
 * This is the same mechanism used by MarkdownRenderer for clickable file paths
 * in chat messages.  ThreeColumnLayout listens for this event and routes the
 * file through resolve → open logic (text files in-panel, PDFs/Office via
 * system app).
 */
function dispatchOpenFile(path: string): void {
  document.dispatchEvent(
    new CustomEvent('swarm:open-file', { detail: { path } }),
  );
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Maps artifact type to a Material Symbols icon name.
 * Used to render the appropriate icon next to each artifact row.
 */
export const ARTIFACT_TYPE_ICONS: Record<RadarArtifact['type'], string> = {
  code: 'code',
  document: 'description',
  config: 'settings',
  image: 'image',
  other: 'insert_drive_file',
};

// ---------------------------------------------------------------------------
// Pure helpers (exported for testing)
// ---------------------------------------------------------------------------

/**
 * Convert an ISO timestamp to a human-friendly relative string.
 *
 * Returns strings like "just now", "2m ago", "1h ago", "3d ago".
 * Falls back to "just now" for future timestamps or invalid dates.
 */
export function formatRelativeTime(isoTimestamp: string): string {
  const now = Date.now();
  const then = new Date(isoTimestamp).getTime();
  if (Number.isNaN(then)) return 'just now';

  const diffMs = now - then;
  if (diffMs < 0) return 'just now';

  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return 'just now';

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;

  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;

  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ArtifactsSectionProps {
  /** Active workspace ID; null means no workspace selected. */
  workspaceId: string | null;
  /** Callback invoked when the user clicks an artifact row. */
  onPreviewFile?: (path: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ArtifactsSection({ workspaceId, onPreviewFile }: ArtifactsSectionProps) {
  // Default to dispatching swarm:open-file (handled by ThreeColumnLayout)
  const handleOpen = useCallback(
    (path: string) => (onPreviewFile ?? dispatchOpenFile)(path),
    [onPreviewFile],
  );
  const [artifacts, setArtifacts] = useState<RadarArtifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch artifacts on mount, when workspaceId changes, and every 30s
  useEffect(() => {
    if (!workspaceId) {
      setArtifacts([]);
      return;
    }

    let cancelled = false;

    const fetchArtifacts = (showLoading: boolean) => {
      if (showLoading) setLoading(true);
      setError(null);

      radarService
        .fetchRecentArtifacts(workspaceId)
        .then((data) => {
          if (!cancelled) {
            setArtifacts(data);
            setLoading(false);
          }
        })
        .catch((err) => {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : 'Failed to load artifacts');
            setLoading(false);
          }
        });
    };

    fetchArtifacts(true);
    const interval = setInterval(() => fetchArtifacts(false), 30_000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [workspaceId]);

  // --- Loading state ---
  if (loading) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        Loading artifacts…
      </p>
    );
  }

  // --- Error state ---
  if (error) {
    return (
      <p className="text-xs text-[var(--color-error)] py-2">
        {error}
      </p>
    );
  }

  // --- Empty state ---
  if (artifacts.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        No recent artifacts
      </p>
    );
  }

  // --- Item list ---
  return (
    <ul className="space-y-1">
      {artifacts.map((artifact) => {
        const payload: DropPayload = {
          type: 'radar-artifact',
          path: artifact.path,
          title: artifact.title,
        };
        const iconName = ARTIFACT_TYPE_ICONS[artifact.type] ?? ARTIFACT_TYPE_ICONS.other;

        return (
          <li
            key={artifact.path}
            className="group flex items-center gap-2 px-1 py-1 rounded hover:bg-[var(--color-hover)] transition-colors cursor-pointer"
            onClick={() => handleOpen(artifact.path)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleOpen(artifact.path);
              }
            }}
          >
            {/* Type icon (Material Symbols) */}
            <span
              className="material-symbols-outlined shrink-0 text-sm text-[var(--color-text-muted)]"
              aria-hidden="true"
            >
              {iconName}
            </span>

            {/* Title (filename) */}
            <span className="text-xs text-[var(--color-text)] truncate flex-1">
              {artifact.title}
            </span>

            {/* Relative timestamp */}
            <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
              {formatRelativeTime(artifact.modifiedAt)}
            </span>

            {/* Drag handle */}
            <DragHandle payload={payload} />
          </li>
        );
      })}
    </ul>
  );
}
