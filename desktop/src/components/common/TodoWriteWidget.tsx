import { useState } from 'react';
import clsx from 'clsx';

interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
}

interface TodoWriteWidgetProps {
  todos: TodoItem[];
}

const statusConfig = {
  pending: {
    icon: 'radio_button_unchecked',
    color: 'text-[var(--color-text-muted)]',
    bgColor: 'bg-[var(--color-hover)]',
  },
  in_progress: {
    icon: 'pending',
    color: 'text-primary',
    bgColor: 'bg-primary/10',
  },
  completed: {
    icon: 'check_circle',
    color: 'text-status-online',
    bgColor: 'bg-status-online/10',
  },
} as const;

function TodoItemCard({ todo }: { todo: TodoItem }) {
  const config = statusConfig[todo.status];

  return (
    <div
      className={clsx(
        'flex items-start gap-3 p-3 rounded-lg transition-colors',
        config.bgColor
      )}
    >
      <span
        className={clsx(
          'material-symbols-outlined text-xl flex-shrink-0',
          config.color
        )}
      >
        {config.icon}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-[var(--color-text)] text-sm font-medium">{todo.content}</p>
        {todo.status === 'in_progress' && todo.activeForm && (
          <p className="text-[var(--color-text-muted)] text-xs mt-0.5">{todo.activeForm}</p>
        )}
      </div>
    </div>
  );
}

export default function TodoWriteWidget({ todos }: TodoWriteWidgetProps) {
  const [showRawJson, setShowRawJson] = useState(false);

  if (!todos || todos.length === 0) {
    return (
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg p-4">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-sm">
            checklist
          </span>
          <span className="text-sm font-medium text-[var(--color-text)]">
            Tool Call: TodoWrite
          </span>
        </div>
        <p className="text-[var(--color-text-muted)] text-sm mt-2">No tasks to display</p>
      </div>
    );
  }

  const completedCount = todos.filter((t) => t.status === 'completed').length;

  const handleCopy = () => {
    const text = todos
      .map((t) => `[${t.status}] ${t.content}`)
      .join('\n');
    navigator.clipboard.writeText(text);
  };

  return (
    <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-sm">
            checklist
          </span>
          <span className="text-sm font-medium text-[var(--color-text)]">
            Tool Call: TodoWrite
          </span>
        </div>
        <span className="text-xs text-[var(--color-text-muted)]">
          {completedCount}/{todos.length} completed
        </span>
      </div>

      {/* Todo List */}
      <div className="p-4 space-y-2" role="list">
        {todos.map((todo, index) => (
          <TodoItemCard key={index} todo={todo} />
        ))}
      </div>

      {/* Footer with actions */}
      <div className="px-4 py-2 border-t border-[var(--color-border)] flex items-center justify-between">
        <button
          onClick={() => setShowRawJson(!showRawJson)}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          {showRawJson ? 'Hide' : 'View'} Raw JSON
        </button>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] rounded transition-colors"
        >
          <span className="material-symbols-outlined text-sm">content_copy</span>
          Copy
        </button>
      </div>

      {/* Collapsible Raw JSON */}
      {showRawJson && (
        <div className="px-4 pb-4">
          <pre className="text-sm text-[var(--color-text-muted)] overflow-x-auto bg-[var(--color-hover)] p-3 rounded">
            <code>{JSON.stringify({ todos }, null, 2)}</code>
          </pre>
        </div>
      )}
    </div>
  );
}
