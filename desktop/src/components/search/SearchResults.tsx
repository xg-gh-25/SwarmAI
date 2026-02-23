/**
 * SearchResults component - groups results by entity type with badges.
 * Requirements: 38.4, 38.6, 38.12
 */
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { SearchResultItem } from '../../services/search';

/** Entity type display configuration */
const ENTITY_TYPE_CONFIG: Record<string, { icon: string; color: string }> = {
  todos: { icon: 'notifications', color: 'bg-yellow-500/20 text-yellow-400' },
  tasks: { icon: 'play_arrow', color: 'bg-blue-500/20 text-blue-400' },
  planItems: { icon: 'calendar_today', color: 'bg-purple-500/20 text-purple-400' },
  communications: { icon: 'chat', color: 'bg-green-500/20 text-green-400' },
  artifacts: { icon: 'inventory_2', color: 'bg-orange-500/20 text-orange-400' },
  reflections: { icon: 'psychology', color: 'bg-pink-500/20 text-pink-400' },
};

function formatTimestamp(dateString?: string): string {
  if (!dateString) return '';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

interface SearchResultsListProps {
  results: SearchResultItem[];
  selectedIndex?: number;
  onSelect: (item: SearchResultItem) => void;
}

export default function SearchResultsList({ results, selectedIndex = -1, onSelect }: SearchResultsListProps) {
  const { t } = useTranslation();

  // Group results by entity type
  const grouped = useMemo(() => {
    const groups: Record<string, SearchResultItem[]> = {};
    for (const item of results) {
      const type = item.entityType;
      if (!groups[type]) groups[type] = [];
      groups[type].push(item);
    }
    return groups;
  }, [results]);

  let flatIndex = 0;

  return (
    <div data-testid="search-results">
      {Object.entries(grouped).map(([type, items]) => {
        const config = ENTITY_TYPE_CONFIG[type] || { icon: 'article', color: 'bg-gray-500/20 text-gray-400' };

        return (
          <div key={type}>
            {/* Group header */}
            <div className="px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider bg-[var(--color-bg)]/50 sticky top-0">
              {t(`search.entityType.${type}`)}
              <span className="ml-1 text-[var(--color-text-muted)]">({items.length})</span>
            </div>

            {/* Items */}
            {items.map((item) => {
              const currentFlatIndex = flatIndex++;
              const isSelected = currentFlatIndex === selectedIndex;
              const isArchived = item.status === 'archived';

              return (
                <button
                  key={item.id}
                  id={`search-result-${currentFlatIndex}`}
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => onSelect(item)}
                  className={clsx(
                    'w-full px-3 py-2 flex items-start gap-2.5 text-left transition-colors cursor-pointer',
                    isSelected
                      ? 'bg-[var(--color-primary)]/10'
                      : 'hover:bg-[var(--color-hover)]'
                  )}
                  data-testid={`search-result-item-${item.id}`}
                >
                  {/* Type icon */}
                  <span className={clsx('material-symbols-outlined text-base mt-0.5 shrink-0 rounded p-0.5', config.color)}>
                    {config.icon}
                  </span>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-[var(--color-text)] truncate">
                        {item.title}
                      </span>
                      {isArchived && (
                        <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-500/20 text-gray-400" data-testid="archived-badge">
                          {t('search.archived')}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      {item.workspaceName && (
                        <span className="text-xs text-[var(--color-text-muted)] truncate">
                          {item.workspaceName}
                        </span>
                      )}
                      {item.updatedAt && (
                        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
                          {formatTimestamp(item.updatedAt)}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
