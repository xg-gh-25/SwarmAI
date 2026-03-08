/**
 * AssistantMessageView — renders assistant messages with branded layout.
 *
 * This sub-component handles the full rendering of assistant-role messages:
 * - Branded header via AssistantHeader (🐝 SwarmAI · timestamp)
 * - Left-aligned content blocks with no avatar indentation
 * - Red border error wrapper when message.isError is true
 * - min-w-0 overflow constraint on content (prevents flex overflow)
 * - Save-to-Memory button on the last assistant message (hover-to-reveal,
 *   next to the Copy button) with per-session status tracking
 *
 * Key layout change: content starts at the left margin directly below the
 * header line — no avatar column or indentation gap.
 *
 * @exports AssistantMessageView      — The view React component
 * @exports AssistantMessageViewProps  — Props interface
 *
 * Validates: Requirements 2.1, 2.4, 3.1, 3.2, 3.5
 */

import React, { useState, useCallback, useMemo } from 'react';
import clsx from 'clsx';
import type { Message, ToolResultContent } from '../../../types';
import { ContentBlockRenderer } from './ContentBlockRenderer';
import { AssistantHeader } from './AssistantHeader';
import { useMemorySave } from '../../../hooks/useMemorySave';
import type { MemorySaveStatus } from '../../../hooks/useMemorySave';
import { Toast } from '../../../components/common/Toast';

export interface AssistantMessageViewProps {
  /** The assistant message to render */
  message: Message;
  /** Callback when the user answers an ask_user_question block */
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** The tool_use ID currently awaiting a user answer */
  pendingToolUseId?: string;
  /** Whether the assistant message is still streaming */
  isStreaming?: boolean;
  /** The current session ID for the save API call */
  sessionId?: string;
  /** Whether this is the last assistant message in the session */
  isLastAssistant?: boolean;
}

/** Map of memory save status to Material Symbols icon names. */
const MEMORY_ICON_MAP: Record<MemorySaveStatus, string> = {
  idle: 'neurology',
  loading: 'progress_activity',
  saved: 'check_circle',
  empty: 'neurology',
  error: 'error',
};

export const AssistantMessageView: React.FC<AssistantMessageViewProps> = ({
  message,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
  sessionId,
  isLastAssistant,
}) => {
  const [copied, setCopied] = useState(false);

  // Per-session memory save state
  const { statusMap, toastMap, save: saveMemory, reset: resetMemory } = useMemorySave();
  const memorySaveStatus: MemorySaveStatus = sessionId ? (statusMap[sessionId] || 'idle') : 'idle';
  const memoryToastMessage = sessionId ? (toastMap[sessionId] || null) : null;

  /** Extract plain text from all content blocks for clipboard copy. */
  const extractMessageText = useCallback((): string => {
    return message.content
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('\n');
  }, [message.content]);

  // Pre-build result map for O(1) tool_use → tool_result pairing
  const resultMap = useMemo(() => {
    const map = new Map<string, ToolResultContent>();
    for (const block of message.content) {
      if (block.type === 'tool_result') {
        map.set(block.toolUseId, block);
      }
    }
    return map;
  }, [message.content]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(extractMessageText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [extractMessageText]);

  const handleSaveMemory = useCallback(() => {
    if (sessionId && memorySaveStatus !== 'loading') {
      saveMemory(sessionId);
    }
  }, [sessionId, memorySaveStatus, saveMemory]);

  const contentBlocks = message.content.map((block, index) => {
    // Use block-specific IDs for stable keys to prevent state mix-ups
    // when multiple tool blocks are rendered consecutively
    const key = block.type === 'tool_use' ? `tu-${block.id || index}`
      : block.type === 'tool_result' ? `tr-${block.toolUseId || index}`
      : `cb-${index}`;
    return (
      <ContentBlockRenderer
        key={key}
        block={block}
        resultMap={resultMap}
        allBlocks={message.content}
        onAnswerQuestion={onAnswerQuestion}
        pendingToolUseId={pendingToolUseId}
        isStreaming={isStreaming}
      />
    );
  });

  return (
    <div className="group/msg min-w-0">
      <AssistantHeader
        timestamp={message.timestamp}
        isStreaming={isStreaming}
      />

      {message.isError ? (
        <div
          className="border border-red-500/60 bg-red-500/10 rounded-lg p-3"
          role="alert"
          aria-label="Error message"
        >
          <div className="space-y-3">{contentBlocks}</div>
        </div>
      ) : (
        <div className="space-y-3">
          {contentBlocks}
          {/* Streaming cursor — blinking caret at end of streaming content */}
          {isStreaming && (
            <span className="inline-block w-2 h-4 bg-primary/70 rounded-sm animate-pulse align-text-bottom" aria-hidden="true" />
          )}
        </div>
      )}

      {/* Action buttons — appear on hover below content, hidden while streaming */}
      {!isStreaming && extractMessageText().length > 0 && (
        <div className="opacity-0 group-hover/msg:opacity-100 transition-opacity mt-1 flex items-center gap-2">
          {/* Copy button (unchanged) */}
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1 px-2 py-0.5 text-xs text-[var(--color-text-muted)]
                       hover:text-[var(--color-text)] rounded transition-colors"
            title={copied ? 'Copied!' : 'Copy message'}
          >
            <span className="material-symbols-outlined text-sm">
              {copied ? 'check' : 'content_copy'}
            </span>
            {copied ? 'Copied!' : 'Copy'}
          </button>

          {/* Save to Memory button — only on last assistant message */}
          {isLastAssistant && sessionId && (
            <button
              type="button"
              onClick={handleSaveMemory}
              disabled={memorySaveStatus === 'loading'}
              className={clsx(
                'flex items-center gap-1 px-2 py-0.5 text-xs rounded transition-colors',
                memorySaveStatus === 'saved'
                  ? 'text-green-500 hover:text-green-400'
                  : memorySaveStatus === 'error'
                    ? 'text-red-500 hover:text-red-400'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                memorySaveStatus === 'loading' && 'opacity-50 cursor-not-allowed'
              )}
              title={memorySaveStatus === 'saved' ? 'Saved!' : memorySaveStatus === 'loading' ? 'Saving...' : 'Save to Memory'}
              aria-label="Save to Memory"
            >
              <span className={clsx(
                'material-symbols-outlined text-sm',
                memorySaveStatus === 'loading' && 'animate-spin'
              )}>
                {MEMORY_ICON_MAP[memorySaveStatus]}
              </span>
              {memorySaveStatus === 'saved' ? 'Saved!' : memorySaveStatus === 'loading' ? 'Saving...' : 'Save'}
            </button>
          )}
        </div>
      )}

      {/* Toast for memory save results — only on last assistant message */}
      {isLastAssistant && sessionId && memoryToastMessage && (
        <Toast
          message={memoryToastMessage}
          type={memorySaveStatus === 'saved' ? 'success' : memorySaveStatus === 'error' ? 'error' : 'info'}
          duration={4000}
          onDismiss={() => resetMemory(sessionId)}
        />
      )}
    </div>
  );
};

export default AssistantMessageView;
