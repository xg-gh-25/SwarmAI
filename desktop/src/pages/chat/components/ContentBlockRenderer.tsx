/**
 * Routes content blocks to type-specific renderers.
 *
 * Markdown text and the interactive interrupts (question / permission /
 * escalation). Tool calls never come here: they render as TurnZones strips
 * from the turn projection (turnView), so a tool_use / tool_result block
 * renders nothing.
 *
 * @exports ContentBlockRenderer — The routing component
 */

import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import type { ContentBlock, EscalationContent } from '../../../types';
import { MarkdownRenderer, AskUserQuestion } from '../../../components/common';
import { InlinePermissionRequest } from './InlinePermissionRequest';
import { EscalationBlock } from './EscalationBlock';

/**
 * Re-render budget while streaming. On each throttled fire MarkdownRenderer
 * re-parses the FULL growing string via its remark/rehype plugin chain
 * (remarkGfm + remarkBreaks + remarkMath + rehypeKatex — NOT highlight.js, which
 * runs post-render in a per-CodeBlock useEffect, so it is not multiplied by this
 * throttle). A streaming text block grows by a token on every SSE chunk, so an
 * un-throttled parse is O(n²) over the stream (run_00e0e872 jank). Throttling the
 * markdown INPUT to one update per window bounds the parse COUNT by elapsed time,
 * not by token count — O(n²) → O(duration/window · n).
 *
 * 200ms ≈ 5 parses/sec (run_954d7c48, raised from 100ms/~10fps): halves the reparse
 * frequency vs 100ms with imperceptible added latency before a token becomes visible
 * (a streamed reply arrives far faster than a reader consumes it), while keeping
 * formatted markdown live during streaming (the deliberate run_087e097e UX — NOT
 * reverted to plaintext). Do not raise much further: too-long a window makes the
 * live-formatting update visibly laggy.
 */
const STREAM_RENDER_THROTTLE_MS = 200;

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
function StreamingMarkdownText({ text, live = true }: { text: string; live?: boolean }) {
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

  // Resting: the full final text at once, from the SAME component instance, so the
  // stream's end re-renders the markdown in place instead of remounting it (a remount
  // re-runs mermaid and code highlighting, which reads as a flash).
  return <MarkdownRenderer content={live ? throttledText : text} />;
}

/**
 * Thinking block — a quiet collapsible panel (streaming-simplification Run 1).
 *
 * A separate component (not an inline branch) so its hooks obey the Rules of
 * Hooks — ContentBlockRenderer early-returns per block type and cannot host
 * hooks itself (same reason as StreamingMarkdownText above).
 *
 * Default OPEN while actively streaming (a user watching the model reason sees
 * the thinking land live), then auto-collapses once the turn becomes historical
 * (the quiet default the whole Run 1 is about). Genuinely CONTROLLED: a user
 * toggle during streaming STICKS (we don't force it back open on every render),
 * and only the stream→historical transition auto-collapses — avoiding the
 * uncontrolled-`<details>` footgun where a prop-driven `open` silently reopens a
 * panel the user closed, or force-closes one they're still reading.
 */
function ThinkingBlock({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<boolean>(isStreaming);
  // When the turn finishes streaming, collapse to the quiet resting state. This
  // fires ONLY on the streaming→historical edge, so a user's manual toggle while
  // streaming is preserved until that single transition.
  const wasStreamingRef = useRef<boolean>(isStreaming);
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      setOpen(false);
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming]);

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="group border border-[var(--color-border)] rounded-lg overflow-hidden text-sm"
    >
      <summary className="px-3 py-2 cursor-pointer bg-[var(--color-card)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] select-none flex items-center gap-1.5">
        <span className="text-xs">💭</span>
        <span>{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
        <span className="ml-auto text-xs opacity-60">{text.length > 200 ? `~${Math.ceil(text.length / 4)} tokens` : ''}</span>
      </summary>
      <div className="px-3 py-2 border-t border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text-muted)] italic text-xs leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
        {text}
      </div>
    </details>
  );
}

interface ContentBlockRendererProps {
  block: ContentBlock;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  /** Callback when user approves/denies a permission request. */
  onPermissionDecision?: (requestId: string, decision: 'approve' | 'deny') => void;
  /** Callback when user clicks an escalation option — sends as chat response. */
  onEscalationSelect?: (escalationId: string, optionLabel: string) => void;
  pendingToolUseId?: string;
  /** The request ID of the currently pending permission (buttons enabled for this one). */
  pendingPermissionRequestId?: string;
  isStreaming?: boolean;
  /** Read-only render (History preview): interactive blocks (question /
   *  permission / escalation) must be inert regardless of historical pending
   *  status — interactivity is gated on the pending/status signals, NOT on
   *  callback presence, so omitting callbacks alone would still render
   *  live-looking controls. */
  readOnly?: boolean;
}

export function ContentBlockRenderer({
  block,
  onAnswerQuestion,
  onPermissionDecision,
  onEscalationSelect,
  pendingToolUseId,
  pendingPermissionRequestId,
  isStreaming,
  readOnly,
}: ContentBlockRendererProps) {
  if (block.type === 'text') {
    // Render markdown in BOTH states so formatting (headings/lists/code/math) is
    // visible live, not just at stream end. While streaming, the value fed to the
    // markdown parser is THROTTLED (StreamingMarkdownText) so the O(n²) per-token
    // reparse storm (run_00e0e872) is bounded by time (~5/sec). On stream end the
    // same instance renders the full final text unthrottled — what every historical
    // message renders. (run_087e097e supersedes the plaintext branch of run_00e0e872.)
    // One element type in both states: the end of the stream only stops the throttle.
    return <StreamingMarkdownText text={block.text || ''} live={!!isStreaming} />;
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
    return <ThinkingBlock text={thinkingText} isStreaming={!!isStreaming} />;
  }

  return null;
}
