/**
 * AssistantMessageView — renders assistant messages with branded layout.
 *
 * This sub-component handles the full rendering of assistant-role messages:
 * - Branded header via AssistantHeader (🐝 SwarmAI · timestamp)
 * - Content rendered from the ONE turn projection (`deriveTurnView`,
 *   pages/chat/turnView.ts), in stream order: ADJACENT thinking + tool calls
 *   form one ProcessBlock card; between-tool notes, landmarks, the answer
 *   (outcome) and interrupts (question / permission / escalation) render in
 *   the main stream
 * - Pipeline stage rows (`stage` units — written by the backend from run.json)
 *   render as PipelineStageRow; while streaming, the stage now running is shown
 *   above the live process row (PipelineStageLive)
 * - Next-step chips (NextSteps) from a trailing ```next-steps block, last
 *   assistant message only; Copy copies the answer, not the process
 * - Red border error wrapper when message.isError is true
 * - min-w-0 overflow constraint on content (prevents flex overflow)
 * - Save-to-Memory button on the last assistant message (hover-to-reveal,
 *   next to the Copy button) with per-session status tracking
 * - Compact Context button on the last assistant message (hover-to-reveal,
 *   after Save-to-Memory) — conditionally visible when contextWarning
 *   level is 'warn' or 'critical', with urgency coloring at critical level
 *
 * Key layout change: content starts at the left margin directly below the
 * header line — no avatar column or indentation gap.
 *
 * @exports AssistantMessageView      — The view React component
 * @exports AssistantMessageViewProps  — Props interface
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8,
 *            3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import clsx from 'clsx';
import type { Message, ContentBlock } from '../../../types';
import type { ContextWarning } from '../../../hooks/useChatStreamingLifecycle';
import { ContentBlockRenderer } from './ContentBlockRenderer';
import { ProcessBlock } from './ProcessBlock';
import { PipelineStageRow, PipelineStageLive } from './PipelineStageRow';
import { NextSteps } from './NextSteps';
import { deriveTurnView, extractNextSteps } from '../turnView';
import { AssistantHeader } from './AssistantHeader';
import { useMemorySave } from '../../../hooks/useMemorySave';
import { chatService } from '../../../services/chat';
import type { MemorySaveStatus } from '../../../hooks/useMemorySave';
import { useToast } from '../../../contexts/ToastContext';
import { copyToClipboard } from '../../../utils/clipboard';
import { isOt01DiagEnabled } from '../../../utils/diagFlags';
import { useSubAgentProgress } from '../../../hooks/useSubAgentProgress';
import { SubAgentProgressBanner } from '../../../components/chat/SubAgentProgressBanner';

export interface AssistantMessageViewProps {
  /** The assistant message to render */
  message: Message;
  /** Callback when the user answers an ask_user_question block */
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** Callback when the user approves/denies a permission request */
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  /** Callback when user clicks an escalation option — sends choice as chat message */
  onEscalationSelect?: (escalationId: string, optionLabel: string) => void;
  /** The tool_use ID currently awaiting a user answer */
  pendingToolUseId?: string;
  /** The request ID of the currently pending permission */
  pendingPermissionRequestId?: string;
  /** Whether the assistant message is still streaming */
  isStreaming?: boolean;
  /** The current session ID for the save API call */
  sessionId?: string;
  /** Whether this is the last assistant message in the session */
  isLastAssistant?: boolean;
  /** Context warning from the backend context monitor (per-session, display mirror) */
  contextWarning?: ContextWarning | null;
  /** Callback to send a continuation prompt when model stopped prematurely */
  onContinue?: () => void;
  /** Read-only render (History preview) — forces interactive blocks inert. */
  readOnly?: boolean;
}

/** Map of memory save status to Material Symbols icon names. */
const MEMORY_ICON_MAP: Record<MemorySaveStatus, string> = {
  idle: 'neurology',
  loading: 'progress_activity',
  saved: 'check_circle',
  empty: 'neurology',
  error: 'error',
};

/** Tool results are consumed by the projection; ContentBlockRenderer never pairs here. */
const EMPTY_RESULTS = new Map();

export const AssistantMessageView: React.FC<AssistantMessageViewProps> = ({
  message,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  sessionId,
  isLastAssistant,
  contextWarning,
  onContinue,
  readOnly,
}) => {
  const [copied, setCopied] = useState(false);
  // The user's open/closed CHOICE per process segment, keyed by the segment's
  // ordinal id (`seg-<n>`, see turnView.ts) — stable while the turn streams AND
  // across the stream-end reconcile that drops live-only tool_result blocks.
  // Lifted here (not inside ProcessBlock) so it survives ProcessBlock remounts.
  // No entry = automatic: open while live, folded 2s after (ProcessBlock).
  const [processChoice, setProcessChoice] = useState<ReadonlyMap<string, boolean>>(new Map());
  const chooseProcess = useCallback((id: string, open: boolean) => {
    setProcessChoice((prev) => new Map(prev).set(id, open));
  }, []);
  const { addToast } = useToast();

  // Sub-agent progress observability (polls only for last streaming message)
  const subAgentProgress = useSubAgentProgress(
    isLastAssistant && isStreaming ? (sessionId ?? null) : null,
    !!(isLastAssistant && isStreaming),
  );

  // Per-session memory save state
  const { statusMap, toastMap, save: saveMemory, reset: resetMemory } = useMemorySave();
  const memorySaveStatus: MemorySaveStatus = sessionId ? (statusMap[sessionId] || 'idle') : 'idle';
  const memoryToastMessage = sessionId ? (toastMap[sessionId] || null) : null;

  // Compact button local state
  const [compactStatus, setCompactStatus] = useState<'idle' | 'loading' | 'done'>('idle');

  const handleCompact = useCallback(async () => {
    if (!sessionId || compactStatus === 'loading') return;
    setCompactStatus('loading');
    try {
      const result = await chatService.compactSession(sessionId);
      setCompactStatus('done');
      addToast({
        severity: 'success',
        message: result.status === 'compacted'
          ? 'Context compacted successfully'
          : result.message,
        autoDismiss: true,
      });
      setTimeout(() => setCompactStatus('idle'), 3000);
    } catch {
      setCompactStatus('idle');
      addToast({
        severity: 'error',
        message: 'Failed to compact session',
        autoDismiss: true,
      });
    }
  }, [sessionId, compactStatus, addToast]);

  // The ONE display projection of this turn (pages/chat/turnView.ts): ordered
  // units (process segments / landmarks / outcome / interrupts), the single
  // done rule, the lifted next-steps block and what Copy copies.
  const view = useMemo(
    () => deriveTurnView(message.content, !!isStreaming),
    [message.content, isStreaming],
  );

  /** All text the message carries — gates the action row exactly as before the
   *  projection (a turn that ended mid-process, with no answer yet, still gets
   *  Continue / Save). */
  const allText = useMemo(
    () => message.content
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('\n'),
    [message.content],
  );
  /** Copy copies the answer (outcome text) — not process notes, not next-steps;
   *  a turn with no answer falls back to all of its text (still without a
   *  trailing next-steps block). */
  const extractMessageText = useCallback(
    (): string => view.copyText || extractNextSteps(allText, false).text,
    [view, allText],
  );

  const handleCopy = useCallback(() => {
    copyToClipboard(extractMessageText());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [extractMessageText]);

  const handleSaveMemory = useCallback(() => {
    if (sessionId && memorySaveStatus !== 'loading') {
      saveMemory(sessionId);
    }
  }, [sessionId, memorySaveStatus, saveMemory]);

  // Fire toast when memory save completes (replaces inline <Toast> JSX)
  useEffect(() => {
    if (!isLastAssistant || !sessionId || !memoryToastMessage) return;
    const severity = memorySaveStatus === 'saved' ? 'success' : memorySaveStatus === 'error' ? 'error' : 'info';
    addToast({ severity, message: memoryToastMessage, autoDismiss: true, durationMs: 4000 });
    resetMemory(sessionId);
  }, [memoryToastMessage, isLastAssistant, sessionId, memorySaveStatus, addToast, resetMemory]);

  // Compact button visibility: only on last assistant, not streaming, with active warning
  const showCompactButton = isLastAssistant
    && !isStreaming
    && sessionId
    && contextWarning
    && (contextWarning.level === 'warn' || contextWarning.level === 'critical');

  // ── OT01 render-loss diagnostic (opt-in via isOt01DiagEnabled; run_3451bbd1) — RENDER side ─
  // Gated on isOt01DiagEnabled() (DEV, or localStorage SWARM_OT01_DIAG=1 in
  // prod) — NOT import.meta.env.DEV, which is tree-shaken dead in the prod .app
  // where the bug actually happens (run_3451bbd1).
  // The RENDER half of the store-vs-render pair (store half: [OT01-diag]
  // store→React sync in useChatStreamingLifecycle). Logs the block-count this
  // view ACTUALLY renders for this msgId. In the live console, line up the two
  // by msgId: storeTextBlocks > renderedTextBlocks ⇒ content reached the store
  // but the view rendered fewer ⇒ loss is in the React prop/render path (the
  // single-tab layer headless tests can't expose). Equal counts ⇒ the loss is
  // NOT here (look at MarkdownRenderer/CSS/viewport). Removed once located.
  if (isOt01DiagEnabled()) {
    console.debug('[OT01-diag] render', {
      msgId: message.id,
      renderedBlocks: message.content.length,
      renderedTextBlocks: message.content.filter((b) => b.type === 'text').length,
      isStreaming: !!isStreaming,
    });
  }

  // Render ONE block via ContentBlockRenderer (markdown text + the interactive
  // interrupt widgets). Tool calls never come through here — they live inside a
  // ProcessBlock, which takes its status from the projection.
  const renderBlock = (block: ContentBlock, key: string) => (
    <ContentBlockRenderer
      key={key}
      block={block}
      resultMap={EMPTY_RESULTS}
      allBlocks={message.content}
      onAnswerQuestion={onAnswerQuestion}
      onPermissionDecision={onPermissionDecision}
      onEscalationSelect={onEscalationSelect}
      pendingToolUseId={pendingToolUseId}
      pendingPermissionRequestId={pendingPermissionRequestId}
      isStreaming={isStreaming}
      readOnly={readOnly}
      sessionId={sessionId}
    />
  );

  const contentBlocks = view.units.map((unit) => {
    if (unit.kind === 'process') {
      // Always a Fragment, so the element type never changes when the live stage
      // line comes and goes — ProcessBlock keeps its state instead of remounting.
      return (
        <React.Fragment key={`p-${unit.id}`}>
          {unit.isLive && unit.live?.stage && <PipelineStageLive stage={unit.live.stage} />}
          <ProcessBlock
            unit={unit}
            expanded={processChoice.get(unit.id)}
            onToggle={(open) => chooseProcess(unit.id, open)}
            startedAt={message.timestamp}
          />
        </React.Fragment>
      );
    }
    if (unit.kind === 'stage') return <PipelineStageRow key={`st-${unit.index}`} block={unit.block} />;
    if (unit.kind === 'interrupt') return renderBlock(unit.block, `i-${unit.index}`);
    // landmark / note / outcome are markdown text. A note and an outcome share a
    // key prefix: while streaming, the answer-so-far turns into a note the moment
    // the next tool call arrives, and the same text must not remount.
    const prefix = unit.kind === 'landmark' ? 'lm' : 'cb';
    return renderBlock({ type: 'text', text: unit.text }, `${prefix}-${unit.index}`);
  });

  return (
    <div className="group/msg min-w-0 flex gap-2.5 items-start">
      {/* Agent avatar — 24px bronze gradient circle matching mockup & nav logo */}
      <div
        className="w-6 h-6 rounded-[6px] flex items-center justify-center flex-shrink-0 mt-0.5 text-[13px]"
        style={{
          background: 'linear-gradient(180deg, #d4a537, #a67c20)',
          boxShadow: '0 1px 2px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.15)',
        }}
        aria-hidden="true"
      >
        <span role="img" aria-label="Swarm">&#x1F41D;</span>
      </div>
      <div className="flex-1 min-w-0">
        <AssistantHeader
        timestamp={message.timestamp}
        isStreaming={isStreaming}
      />

      {message.isError ? (
        <div
          className="border border-amber-500/60 bg-amber-500/10 rounded-lg p-3"
          role="alert"
          aria-label="Connection interrupted"
        >
          <div className="space-y-3">{contentBlocks}</div>
        </div>
      ) : (
        <div className="space-y-3 text-[var(--color-text-secondary)]">
          {contentBlocks}
          {/* Streaming cursor — blinking caret at end of streaming content */}
          {isStreaming && (
            <span className="inline-block w-2 h-4 bg-primary/70 rounded-sm animate-pulse align-text-bottom" aria-hidden="true" />
          )}
          {/* Sub-agent progress banner — tiered awareness for long-running agents */}
          {isStreaming && isLastAssistant && (
            <SubAgentProgressBanner progress={subAgentProgress} />
          )}
        </div>
      )}

      {/* Next steps — the reply's trailing ```next-steps block as clickable chips.
          Only on the latest answer (older ones are stale) and never in read-only. */}
      {!readOnly && !isStreaming && isLastAssistant && <NextSteps steps={view.nextSteps} />}

      {/* Action buttons — appear on hover below content, hidden while streaming.
          Hidden entirely in read-only (History preview) — Copy/Save/Compact/
          Continue are all side-effecting controls that don't belong there. */}
      {!readOnly && !isStreaming && allText.length > 0 && (
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

          {/* Compact Context button — only when context warning is active */}
          {showCompactButton && (
            <button
              type="button"
              onClick={handleCompact}
              disabled={compactStatus === 'loading'}
              className={clsx(
                'flex items-center gap-1 px-2 py-0.5 text-xs rounded transition-colors',
                contextWarning.level === 'critical'
                  ? 'text-red-500 hover:text-red-400'
                  : compactStatus === 'done'
                    ? 'text-green-500 hover:text-green-400'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                compactStatus === 'loading' && 'opacity-50 cursor-not-allowed'
              )}
              title={`Compact Context (${contextWarning.pct}% used)`}
              aria-label="Compact Context"
            >
              <span className={clsx(
                'material-symbols-outlined text-sm',
                compactStatus === 'loading' && 'animate-spin'
              )}>
                {compactStatus === 'loading' ? 'progress_activity'
                  : compactStatus === 'done' ? 'check_circle'
                  : 'compress'}
              </span>
              {compactStatus === 'loading' ? 'Compacting...'
                : compactStatus === 'done' ? 'Compacted!'
                : 'Compact'}
            </button>
          )}

          {/* Continue button — allows user to request continuation when model stopped prematurely.
              Only shown on the last assistant message when not an error. */}
          {isLastAssistant && onContinue && !message.isError && (
            <button
              type="button"
              onClick={onContinue}
              className="flex items-center gap-1 px-2 py-0.5 text-xs text-[var(--color-text-muted)]
                         hover:text-[var(--color-accent,var(--color-primary))] rounded transition-colors"
              title="Continue — ask the agent to continue its response"
              aria-label="Continue response"
              data-testid="continue-button"
            >
              <span className="material-symbols-outlined text-sm">
                play_arrow
              </span>
              Continue
            </button>
          )}
        </div>
      )}

    </div>
    </div>
  );
};

export default AssistantMessageView;
