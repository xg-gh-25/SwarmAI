import type { ContentBlock, TodoItem } from '../../../types';
import { MarkdownRenderer, AskUserQuestion, TodoWriteWidget } from '../../../components/common';
import { ToolUseBlock } from './ToolUseBlock';
import { ToolResultBlock } from './ToolResultBlock';

interface ContentBlockRendererProps {
  block: ContentBlock;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

/**
 * Renders different types of content blocks in chat messages
 */
export function ContentBlockRenderer({
  block,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
}: ContentBlockRendererProps) {
  if (block.type === 'text') {
    return <MarkdownRenderer content={block.text || ''} />;
  }

  if (block.type === 'tool_use') {
    // Special handling for TodoWrite
    if (block.name === 'TodoWrite') {
      const todos = block.input?.todos as TodoItem[] | undefined;
      if (Array.isArray(todos) && todos.length > 0) {
        return <TodoWriteWidget todos={todos} />;
      }
    }

    // Generic tool use rendering with collapsible input
    return <ToolUseBlock name={block.name || 'Unknown'} input={block.input || {}} />;
  }

  if (block.type === 'tool_result') {
    return <ToolResultBlock content={block.content} isError={block.isError} />;
  }

  if (block.type === 'ask_user_question') {
    const isPending = pendingToolUseId === block.toolUseId;
    const isAnswered = !isPending && !isStreaming;

    return (
      <AskUserQuestion
        questions={block.questions}
        toolUseId={block.toolUseId}
        onSubmit={onAnswerQuestion || (() => {})}
        disabled={isAnswered || isStreaming}
      />
    );
  }

  return null;
}
