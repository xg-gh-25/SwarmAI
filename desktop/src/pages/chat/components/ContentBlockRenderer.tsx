/**
 * Routes content blocks to type-specific renderers.
 *
 * Pairs tool_use + tool_result blocks into MergedToolBlock components
 * using a pre-built resultMap for O(1) lookup. Orphaned tool_result
 * blocks (no matching tool_use) fall back to standalone ToolResultBlock.
 *
 * @exports ContentBlockRenderer — The routing component
 */

import { useState, useRef, useEffect } from 'react';
import type { ContentBlock, ToolResultContent, EscalationContent } from '../../../types';
import { MarkdownRenderer, AskUserQuestion } from '../../../components/common';
import { MergedToolBlock } from './MergedToolBlock';
import { ToolResultBlock } from './ToolResultBlock';
import { InlinePermissionRequest } from './InlinePermissionRequest';
import { EscalationBlock } from './EscalationBlock';

/**
 * Re-render budget while streaming. MarkdownRenderer re-parses the FULL string
 * (4 remark/rehype plugins + KaTeX + highlight.js) whenever its `content` prop
 * changes, and a streaming text block grows by a token on every SSE chunk. Parsing
 * per-token is O(n²) over the stream (run_00e0e872 jank). Throttling the markdown
 * INPUT to one update per window bounds the parse COUNT by elapsed time (~10/sec),
 * not by token count — O(n²) → O(duration/window · n). 100ms ≈ 10fps: formatted
 * markdown stays visibly live without the per-token reparse storm.
 */
const STREAM_RENDER_THROTTLE_MS = 100;

/**
 * Renders streaming markdown text, but throttles the value fed to MarkdownRenderer
 * so the expensive parse runs at most once per STREAM_RENDER_THROTTLE_MS. This is a
 * separate component (not an inline branch) so its hooks obey the Rules of Hooks —
 * ContentBlockRenderer early-returns per block type and cannot host hooks itself.
 *
 * Leading + trailing edge: the first value renders immediately; subsequent rapid
 * updates within a window are coalesced and the LATEST value is flushed when the
 * window elapses (trailing edge) so the final token is never dropped.
 */
function StreamingMarkdownText({ text }: { text: string }) {
  const [throttledText, setThrottledText] = useState(text);
  // Timestamp of the last committed render (0 = none yet → leading edge fires now).
  const lastRenderRef = useRef<number>(0);
  // Pending trailing-edge timer + the latest text it should flush.
  const trailingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestTextRef = useRef<string>(text);

  useEffect(() => {
    latestTextRef.current = text;
    const now = Date.now();
    const elapsed = now - lastRenderRef.current;

    if (elapsed >= STREAM_RENDER_THROTTLE_MS) {
      // Leading edge / window already elapsed → render immediately.
      lastRenderRef.current = now;
      setThrottledText(text);
      if (trailingTimerRef.current) {
        clearTimeout(trailingTimerRef.current);
        trailingTimerRef.current = null;
      }
    } else if (!trailingTimerRef.current) {
      // Inside an active window → schedule a single trailing flush of the LATEST text.
      trailingTimerRef.current = setTimeout(() => {
        lastRenderRef.current = Date.now();
        trailingTimerRef.current = null;
        setThrottledText(latestTextRef.current);
      }, STREAM_RENDER_THROTTLE_MS - elapsed);
    }
    // else: a trailing flush is already pending; latestTextRef updated above, so it
    // will flush the newest text when it fires. No extra timer (coalesced).
  }, [text]);

  // On unmount, drop any pending timer (avoids setState-after-unmount).
  useEffect(() => {
    return () => {
      if (trailingTimerRef.current) clearTimeout(trailingTimerRef.current);
    };
  }, []);

  return <MarkdownRenderer content={throttledText} />;
}

interface ContentBlockRendererProps {
  block: ContentBlock;
  /** Pre-built map from toolUseId → ToolResultContent for O(1) pairing. */
  resultMap: Map<string, ToolResultContent>;
  /** Full content array for orphaned tool_result check. */
  allBlocks: ContentBlock[];
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** Callback when user approves/denies a permission request. */
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  /** Callback when user clicks an escalation option — sends as chat response. */
  onEscalationSelect?: (escalationId: string, optionLabel: string) => void;
  pendingToolUseId?: string;
  /** The request ID of the currently pending permission (buttons enabled for this one). */
  pendingPermissionRequestId?: string;
  isStreaming?: boolean;
  /** The ID of the last tool_use block without a result — only this one gets a spinner. */
  lastPendingToolUseId?: string | null;
  /** Read-only render (History preview): interactive blocks (question /
   *  permission / escalation) must be inert regardless of historical pending
   *  status — interactivity is gated on the pending/status signals, NOT on
   *  callback presence, so omitting callbacks alone would still render
   *  live-looking controls. */
  readOnly?: boolean;
  /** Owning tab's session id — forwarded to MergedToolBlock so its
   *  swarm:file-referenced dispatch is tab-scoped (keep-mounted background tabs
   *  otherwise leak into the active tab's Canvas / Referenced-Files). */
  sessionId?: string;
}

export function ContentBlockRenderer({
  block,
  resultMap,
  allBlocks,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  lastPendingToolUseId,
  readOnly,
  sessionId,
}: ContentBlockRendererProps) {
  if (block.type === 'text') {
    // Render markdown in BOTH states so formatting (headings/lists/code/math) is
    // visible live, not just at stream end. While streaming, the value fed to the
    // markdown parser is THROTTLED (StreamingMarkdownText) so the O(n²) per-token
    // reparse storm (run_00e0e872) is bounded by time (~10/sec). On stream end we
    // render MarkdownRenderer directly with the full final text — the same resting
    // path every historical message uses. (run_087e097e supersedes the plaintext
    // branch of run_00e0e872.)
    if (isStreaming) {
      return <StreamingMarkdownText text={block.text || ''} />;
    }
    return <MarkdownRenderer content={block.text || ''} />;
  }

  if (block.type === 'tool_use') {
    // Pair with matching tool_result via resultMap (O(1))
    const matchingResult = resultMap.get(block.id);

    return (
      <MergedToolBlock
        name={block.name || 'Unknown'}
        summary={block.summary || ''}
        toolUseId={block.id}
        category={block.category}
        resultContent={matchingResult?.content}
        resultTruncated={matchingResult?.truncated}
        resultIsError={matchingResult?.isError}
        isPending={lastPendingToolUseId != null ? block.id === lastPendingToolUseId : (!matchingResult && !!isStreaming)}
        isStreaming={isStreaming}
        sessionId={sessionId}
      />
    );
  }

  if (block.type === 'tool_result') {
    // Skip if already consumed by a MergedToolBlock
    const hasMatchingToolUse = allBlocks.some(
      (b) => b.type === 'tool_use' && b.id === block.toolUseId,
    );
    if (hasMatchingToolUse) return null;

    // Orphaned tool_result — standalone fallback
    return (
      <ToolResultBlock
        content={block.content}
        isError={block.isError}
        truncated={block.truncated ?? false}
      />
    );
  }

  if (block.type === 'ask_user_question') {
    // Guard: skip rendering if questions payload is missing/malformed
    // (prevents crash when DB returns incomplete ask_user_question blocks)
    if (!block.questions || !Array.isArray(block.questions) || block.questions.length === 0) {
      // Root 3 / 3A #4: log the silent drop — a question block with no
      // questions means the user can never answer; surfacing it in the console
      // turns an invisible dead-end into a debuggable signal.
      console.warn('[ContentBlockRenderer] ask_user_question block dropped — empty/malformed questions', {
        toolUseId: block.toolUseId,
        questions: block.questions,
      });
      return null;
    }
    const isPending = pendingToolUseId === block.toolUseId;
    // Answered = the block carries the user's submitted answers. This is the
    // authoritative signal (persisted on the block), distinct from "not pending"
    // — a question can be non-pending yet unanswered (e.g. mid-stream). When
    // answered, AskUserQuestion renders its read-only summary (ignores disabled).
    const hasAnswers = !!block.answers && Object.keys(block.answers).length > 0;
    // Disable the interactive form when it's not the live pending question or
    // while streaming — unchanged behavior for the still-unanswered case.
    // readOnly (History preview) always disables (answered blocks still render
    // their read-only summary, which ignores `disabled`).
    const disabled = !isPending || !!isStreaming || !!readOnly;

    return (
      <AskUserQuestion
        questions={block.questions}
        toolUseId={block.toolUseId}
        onSubmit={onAnswerQuestion || (() => {})}
        disabled={disabled && !hasAnswers}
        answers={block.answers}
      />
    );
  }

  if (block.type === 'cmd_permission_request') {
    return (
      <InlinePermissionRequest
        requestId={block.requestId}
        toolName={block.toolName}
        toolInput={block.toolInput}
        reason={block.reason}
        isPending={!readOnly && pendingPermissionRequestId === block.requestId}
        decision={block.decision}
        onDecision={readOnly ? undefined : onPermissionDecision}
        readOnly={readOnly}
      />
    );
  }

  if (block.type === 'escalation') {
    const esc = block as EscalationContent;
    return (
      <EscalationBlock
        id={esc.id}
        severity={esc.severity}
        reason={esc.reason}
        options={esc.options || []}
        status={esc.status}
        resolution={esc.resolution}
        onSelectOption={(!readOnly && esc.status === 'pending') ? onEscalationSelect : undefined}
      />
    );
  }

  if (block.type === 'thinking') {
    const thinkingText = (block as { thinking?: string }).thinking || '';
    if (!thinkingText) return null;
    return (
      <details open className="group border border-[var(--color-border)] rounded-lg overflow-hidden text-sm">
        <summary className="px-3 py-2 cursor-pointer bg-[var(--color-card)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] select-none flex items-center gap-1.5">
          <span className="text-xs">💭</span>
          <span>Thinking</span>
          <span className="ml-auto text-xs opacity-60">{thinkingText.length > 200 ? `~${Math.ceil(thinkingText.length / 4)} tokens` : ''}</span>
        </summary>
        <div className="px-3 py-2 border-t border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text-muted)] italic text-xs leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
          {thinkingText}
        </div>
      </details>
    );
  }

  return null;
}
