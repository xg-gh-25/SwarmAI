import clsx from 'clsx';
import type { Message } from '../../../types';
import { ContentBlockRenderer } from './ContentBlockRenderer';

interface MessageBubbleProps {
  message: Message;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

/**
 * Message Bubble Component for displaying chat messages
 */
export function MessageBubble({
  message,
  onAnswerQuestion,
  pendingToolUseId,
  isStreaming,
}: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className={clsx('flex gap-4', isUser && 'flex-row-reverse')}>
      <div
        className={clsx(
          'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0',
          isUser ? 'bg-orange-500/20' : 'bg-[var(--color-card)]'
        )}
      >
        <span className={clsx('material-symbols-outlined', isUser ? 'text-orange-400' : 'text-primary')}>
          {isUser ? 'person' : 'smart_toy'}
        </span>
      </div>

      <div className={clsx('flex-1 max-w-3xl', isUser && 'text-right')}>
        <div className={clsx('flex items-center gap-2 mb-1', isUser && 'justify-end')}>
          <span className="font-medium text-[var(--color-text)]">{isUser ? 'User' : 'AI Agent'}</span>
          <span className="text-xs text-[var(--color-text-muted)]">
            {new Date(message.timestamp).toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        </div>

        <div className={clsx('space-y-3', isUser && 'inline-block text-left')}>
          {message.content.map((block, index) => (
            <ContentBlockRenderer
              key={index}
              block={block}
              onAnswerQuestion={onAnswerQuestion}
              pendingToolUseId={pendingToolUseId}
              isStreaming={isStreaming}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
