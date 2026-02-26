/**
 * Collapsible context preview panel for project detail / chat views.
 *
 * Displays the 8-layer context assembly with token counts, source paths,
 * and expandable content previews. Uses ETag-based polling to avoid
 * redundant updates when context hasn't changed.
 *
 * Key exports:
 * - ``ContextPreviewPanel`` — Main panel component
 *
 * Validates: Requirement 33.5, 33.6, PE Fix #6 (scalable preview)
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { getContextPreview } from '../../services/context';
import type { ContextPreview, ContextLayer } from '../../types';

interface ContextPreviewPanelProps {
  projectId: string;
  threadId?: string;
}

/** Polling interval in milliseconds (5 seconds per PE Fix #6). */
const POLL_INTERVAL_MS = 5_000;

/** Human-readable names for truncation stages. */
const TRUNCATION_STAGE_LABELS: Record<number, string> = {
  1: 'within-layer',
  2: 'snippet-removal',
  3: 'layer-drop',
};

/**
 * Format a token count for display (e.g. 1200 → "1.2k").
 */
function formatTokenCount(count: number): string {
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1)}k`;
  }
  return String(count);
}

/**
 * Single context layer row with expandable content preview.
 */
function LayerRow({ layer }: { layer: ContextLayer }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-[var(--color-border)]/50 last:border-b-0">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors text-left"
      >
        {/* Expand/collapse chevron */}
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)] flex-shrink-0">
          {expanded ? 'expand_more' : 'chevron_right'}
        </span>

        {/* Layer number badge */}
        <span className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-xs font-medium bg-[var(--color-hover)] text-[var(--color-text-muted)]">
          {layer.layerNumber}
        </span>

        {/* Layer name */}
        <span className="flex-1 text-sm text-[var(--color-text)] truncate">
          {layer.name}
        </span>

        {/* Truncation indicator */}
        {layer.truncated && (
          <span
            className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)]/15 text-[var(--color-warning)]"
            title={`Truncated: stage ${layer.truncationStage} (${TRUNCATION_STAGE_LABELS[layer.truncationStage] ?? 'unknown'})`}
          >
            truncated · {TRUNCATION_STAGE_LABELS[layer.truncationStage] ?? `stage ${layer.truncationStage}`}
          </span>
        )}

        {/* Token count badge */}
        <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium">
          {formatTokenCount(layer.tokenCount)} tokens
        </span>
      </button>

      {/* Source path (always visible below the row) */}
      <div className="px-3 pb-1 pl-12">
        <span className="text-xs text-[var(--color-text-muted)] font-mono truncate block">
          {layer.sourcePath}
        </span>
      </div>

      {/* Expandable content preview */}
      {expanded && layer.contentPreview && (
        <div className="mx-3 mb-2 ml-12 p-2 rounded bg-[var(--color-hover)] border border-[var(--color-border)]/50 overflow-x-auto">
          <pre className="text-xs text-[var(--color-text-muted)] font-mono whitespace-pre-wrap break-words">
            {layer.contentPreview}
          </pre>
        </div>
      )}
    </div>
  );
}

export function ContextPreviewPanel({ projectId, threadId }: ContextPreviewPanelProps) {
  const [collapsed, setCollapsed] = useState(true);
  const [preview, setPreview] = useState<ContextPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /**
   * Fetch context preview, handling ETag 304 (null return = no change).
   */
  const fetchPreview = useCallback(async () => {
    try {
      const result = await getContextPreview(projectId, threadId);
      // null means 304 — context unchanged, skip re-render
      if (result !== null) {
        setPreview(result);
        setError(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load context preview');
    }
  }, [projectId, threadId]);

  /**
   * Initial fetch on mount; polling starts only when panel is expanded.
   * Pauses polling when collapsed to avoid unnecessary network traffic
   * (PE Fix P2).
   */
  useEffect(() => {
    let cancelled = false;

    const doInitialFetch = async () => {
      setLoading(true);
      try {
        const result = await getContextPreview(projectId, threadId);
        if (!cancelled && result !== null) {
          setPreview(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load context preview');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    doInitialFetch();

    // Only poll when panel is expanded (PE Fix P2)
    if (!collapsed) {
      timerRef.current = setInterval(() => {
        if (!cancelled) fetchPreview();
      }, POLL_INTERVAL_MS);
    }

    return () => {
      cancelled = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [projectId, threadId, fetchPreview, collapsed]);

  return (
    <div className="border border-[var(--color-border)] rounded-lg bg-[var(--color-card)] overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors text-left"
      >
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
          {collapsed ? 'chevron_right' : 'expand_more'}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">
          Context Preview
        </span>

        {/* Total token count badge */}
        {preview && (
          <span className="ml-auto text-xs px-2 py-0.5 rounded-full bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium">
            {formatTokenCount(preview.totalTokenCount)} tokens
          </span>
        )}
      </button>

      {/* Panel body */}
      {!collapsed && (
        <div className="border-t border-[var(--color-border)]">
          {/* Loading state */}
          {loading && !preview && (
            <div className="flex items-center justify-center py-6 text-[var(--color-text-muted)]">
              <span className="material-symbols-outlined animate-spin mr-2 text-base">progress_activity</span>
              <span className="text-sm">Loading context…</span>
            </div>
          )}

          {/* Error state */}
          {error && !preview && (
            <div className="flex flex-col items-center justify-center py-6 text-[var(--color-error)] px-4 text-center">
              <span className="material-symbols-outlined text-xl mb-1">error</span>
              <span className="text-sm">{error}</span>
            </div>
          )}

          {/* Content */}
          {preview && (
            <>
              {/* Truncation summary banner */}
              {preview.truncationSummary && (
                <div className="flex items-start gap-2 px-3 py-2 bg-[var(--color-warning)]/10 border-b border-[var(--color-border)]">
                  <span className="material-symbols-outlined text-sm text-[var(--color-warning)] mt-0.5 flex-shrink-0">
                    info
                  </span>
                  <span className="text-xs text-[var(--color-warning)]">
                    {preview.truncationSummary}
                  </span>
                </div>
              )}

              {/* Layer list */}
              {preview.layers.length > 0 ? (
                <div>
                  {preview.layers.map((layer) => (
                    <LayerRow key={layer.layerNumber} layer={layer} />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-6 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined text-xl mb-1">layers_clear</span>
                  <span className="text-sm">No context layers assembled</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default ContextPreviewPanel;
