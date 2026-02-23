import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

interface TodoRadarSidebarProps {
  width: number;
  isResizing: boolean;
  /** Optional close handler. If not provided, close button is hidden. */
  onClose?: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
}

// Mock ToDo items for demonstration
const MOCK_OVERDUE_ITEMS = [
  { id: '1', title: 'Review PR #123' },
  { id: '2', title: 'Reply to email' },
];

const MOCK_PENDING_ITEMS = [
  { id: '3', title: 'Update documentation' },
  { id: '4', title: 'Schedule meeting' },
  { id: '5', title: 'Complete report' },
];

/**
 * Right Sidebar - ToDo Radar Component (Mock)
 * Displays placeholder ToDo items with Overdue and Pending sections
 */
export function TodoRadarSidebar({
  width,
  isResizing,
  onClose,
  onMouseDown,
}: TodoRadarSidebarProps) {
  const { t } = useTranslation();

  return (
    <div
      className="flex flex-col bg-[var(--color-card)] border-l border-[var(--color-border)] relative"
      style={{ width }}
    >
      {/* Resize Handle (on the left side) */}
      <div
        className={clsx(
          'absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-primary/50 transition-colors z-10',
          isResizing && 'bg-primary'
        )}
        onMouseDown={onMouseDown}
      >
        <div className="absolute inset-y-0 -left-1 w-3" />
      </div>

      {/* Header */}
      <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-lg">checklist</span>
          <span className="font-medium text-[var(--color-text)] text-sm">ToDo Radar</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            aria-label={t('common.close', 'Close ToDo Radar')}
          >
            <span className="material-symbols-outlined text-lg">close</span>
          </button>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Overdue Section */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            <span className="text-sm font-medium text-[var(--color-text)]">
              Overdue ({MOCK_OVERDUE_ITEMS.length})
            </span>
          </div>
          <ul className="space-y-2 pl-4">
            {MOCK_OVERDUE_ITEMS.map((item) => (
              <li
                key={item.id}
                className="text-sm text-[var(--color-text-muted)] flex items-start gap-2"
              >
                <span className="text-[var(--color-text-muted)] opacity-50">├─</span>
                <span>{item.title}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Pending Section */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-2 h-2 rounded-full bg-yellow-500" />
            <span className="text-sm font-medium text-[var(--color-text)]">
              Pending ({MOCK_PENDING_ITEMS.length})
            </span>
          </div>
          <ul className="space-y-2 pl-4">
            {MOCK_PENDING_ITEMS.map((item, index) => (
              <li
                key={item.id}
                className="text-sm text-[var(--color-text-muted)] flex items-start gap-2"
              >
                <span className="text-[var(--color-text-muted)] opacity-50">
                  {index === MOCK_PENDING_ITEMS.length - 1 ? '└─' : '├─'}
                </span>
                <span>{item.title}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Mock Notice */}
        <div className="mt-auto pt-4 border-t border-[var(--color-border)]">
          <p className="text-xs text-[var(--color-text-muted)] text-center italic">
            (Mock data - not functional)
          </p>
        </div>
      </div>
    </div>
  );
}
