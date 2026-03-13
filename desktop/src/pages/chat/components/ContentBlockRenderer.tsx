/**
 * Routes content blocks to type-specific renderers.
 *
 * Pairs tool_use + tool_result blocks into MergedToolBlock components
 * using a pre-built resultMap for O(1) lookup. Orphaned tool_result
 * blocks (no matching tool_use) fall back to standalone ToolResultBlock.
 *
 * @exports ContentBlockRenderer — The routing component
 */

import type { ContentBlock, ToolResultContent } from '../../../types';
import { MarkdownRenderer, AskUserQuestion } from '../../../components/common';
import { MergedToolBlock } from './MergedToolBlock';
import { ToolResultBlock } from './ToolResultBlock';

interface ContentBlockRendererProps {
  block: ContentBlock;
  /** Pre-built map from toolUseId → ToolResultContent for O(1) pairing. */
  resultMap: Map<string, ToolResultContent>;
  /** Full content array for orphaned tool_result check. */
  allBlocks: ContentBlock[];
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
  /** The ID of the last tool_use block without a result — only this one gets a spinner. */
  lastPendingToolUseId?: string | null;
}

export function ContentBlockRenderer({
  block,
  resultMap,
  allBlocks,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
  lastPendingToolUseId,
}: ContentBlockRendererProps) {
  if (block.type === 'text') {
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
    const isPending = pendingToolUseId === block.toolUseId;
    const isAnswered = !isPending && !isStreaming;

    return (
      <AskUserQuestion
        questions={block.questions}
        toolUseId={block.toolUseId}
        onSubmit={onAnswerQuestion || (() => {})}
        disabled={isAnswered || !!isStreaming}
      />
    );
  }

  return null;
}
