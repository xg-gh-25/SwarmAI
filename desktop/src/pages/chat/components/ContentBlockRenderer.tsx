/**
 * Routes content blocks to type-specific renderers.
 *
 * Pairs tool_use + tool_result blocks into MergedToolBlock components
 * using a pre-built resultMap for O(1) lookup. Orphaned tool_result
 * blocks (no matching tool_use) fall back to standalone ToolResultBlock.
 *
 * @exports ContentBlockRenderer — The routing component
 */

import type { ContentBlock, ToolResultContent, EscalationContent } from '../../../types';
import { MarkdownRenderer, AskUserQuestion } from '../../../components/common';
import { MergedToolBlock } from './MergedToolBlock';
import { ToolResultBlock } from './ToolResultBlock';
import { InlinePermissionRequest } from './InlinePermissionRequest';
import { EscalationBlock } from './EscalationBlock';

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
}: ContentBlockRendererProps) {
  if (block.type === 'text') {
    // While streaming, render plaintext (whitespace-pre-wrap) instead of markdown.
    // MarkdownRenderer re-parses the FULL string (4 remark/rehype plugins + KaTeX +
    // highlight.js) on every token, and block.text grows per token — O(n²) jank on
    // long replies. Plaintext is O(n) and visually close. On stream end (isStreaming
    // false), we render via MarkdownRenderer — the same path every historical message
    // already uses, so the resting state is unchanged. Typography is matched to the
    // markdown <p> (text/leading) + wrapper (markdown-content min-w-0) to minimize the
    // streaming→final reflow. (run_00e0e872)
    if (isStreaming) {
      return (
        <div className="markdown-content min-w-0">
          <p className="text-[var(--color-text)] mb-2 leading-normal whitespace-pre-wrap">
            {block.text || ''}
          </p>
        </div>
      );
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
    const disabled = !isPending || !!isStreaming;

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
        isPending={pendingPermissionRequestId === block.requestId}
        decision={block.decision}
        onDecision={onPermissionDecision}
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
        onSelectOption={esc.status === 'pending' ? onEscalationSelect : undefined}
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
