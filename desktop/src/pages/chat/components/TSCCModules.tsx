/**
 * System prompt metadata module for the TSCC popover.
 *
 * Replaces the previous five cognitive modules (CurrentContextModule,
 * ActiveAgentsModule, WhatAIDoingModule, ActiveSourcesModule,
 * KeySummaryModule) with a single ``SystemPromptModule`` that displays
 * context file metadata: file list with token counts, truncation
 * indicators, total token usage, and a "View Full Prompt" button.
 *
 * Key exports:
 * - ``SystemPromptModule``  — File list + token counts + View Full Prompt
 *
 * Requirements: 6.1, 6.2, 6.3
 */

import { useState, useCallback } from 'react';
import type { SystemPromptMetadata } from '../../../types';
import { getSystemPromptMetadata } from '../../../services/tscc';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SystemPromptModuleProps {
  sessionId: string | null;
  metadata: SystemPromptMetadata | null;
}

// ---------------------------------------------------------------------------
// Full Prompt Modal
// ---------------------------------------------------------------------------

function FullPromptModal({
  fullText,
  onClose,
}: {
  fullText: string;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Full system prompt"
    >
      <div
        className="
          w-[90vw] max-w-2xl max-h-[80vh] flex flex-col
          bg-[var(--color-card)] border border-[var(--color-border)]
          rounded-lg shadow-xl overflow-hidden
        "
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">
            System Prompt
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)]"
            aria-label="Close"
          >
            <span className="material-symbols-outlined text-base">close</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <pre className="text-xs text-[var(--color-text)] whitespace-pre-wrap font-mono leading-relaxed">
            {fullText || '(empty)'}
          </pre>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SystemPromptModule
// ---------------------------------------------------------------------------

export function SystemPromptModule({
  sessionId,
  metadata,
}: SystemPromptModuleProps) {
  const [showModal, setShowModal] = useState(false);
  const [fullText, setFullText] = useState<string | null>(null);
  const [isLoadingFullText, setIsLoadingFullText] = useState(false);

  const handleViewFullPrompt = useCallback(async () => {
    if (!sessionId) return;

    // If we already have full text from metadata, use it directly
    if (metadata?.fullText) {
      setFullText(metadata.fullText);
      setShowModal(true);
      return;
    }

    // Otherwise fetch from the endpoint
    setIsLoadingFullText(true);
    try {
      const result = await getSystemPromptMetadata(sessionId);
      setFullText(result.fullText);
      setShowModal(true);
    } catch {
      setFullText('(Failed to load system prompt)');
      setShowModal(true);
    } finally {
      setIsLoadingFullText(false);
    }
  }, [sessionId, metadata?.fullText]);

  const files = metadata?.files ?? [];
  const totalTokens = metadata?.totalTokens ?? 0;
  const hasData = files.length > 0;

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-2">
        System Prompt
      </h4>

      {!hasData ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          {sessionId ? 'No context files loaded' : 'No active session'}
        </p>
      ) : (
        <>
          {/* File list with token counts */}
          <ul className="text-sm space-y-1 mb-2">
            {files.map((f) => (
              <li key={f.filename} className="flex items-center justify-between gap-2">
                <span className="text-[var(--color-text)] truncate flex items-center gap-1">
                  <span className="material-symbols-outlined text-xs text-[var(--color-text-muted)]">
                    description
                  </span>
                  {f.filename}
                  {f.truncated && (
                    <span
                      className="text-[10px] px-1 py-0 rounded bg-amber-500/20 text-amber-600"
                      title="File was truncated to fit token budget"
                    >
                      truncated
                    </span>
                  )}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] tabular-nums flex-shrink-0">
                  {f.tokens.toLocaleString()} tok
                </span>
              </li>
            ))}
          </ul>

          {/* Total token usage */}
          <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] pt-1 border-t border-[var(--color-border)]">
            <span>Total</span>
            <span className="tabular-nums font-medium">
              {totalTokens.toLocaleString()} tokens
            </span>
          </div>
        </>
      )}

      {/* View Full Prompt button */}
      {sessionId && (
        <button
          onClick={handleViewFullPrompt}
          disabled={isLoadingFullText}
          className="
            mt-2 w-full text-xs py-1.5 px-3 rounded
            bg-[var(--color-hover)] text-[var(--color-text)]
            hover:bg-[var(--color-border)] transition-colors
            disabled:opacity-50 disabled:cursor-not-allowed
          "
        >
          {isLoadingFullText ? 'Loading…' : 'View Full Prompt'}
        </button>
      )}

      {/* Full prompt modal */}
      {showModal && fullText !== null && (
        <FullPromptModal
          fullText={fullText}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  );
}
