import { useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { sectionsService } from '../services/sections';
import { Breadcrumb } from '../components/common';
import type { PlanItem, FocusType } from '../types/plan-item';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

const FOCUS_TYPE_CONFIG: Record<FocusType, { icon: string; color: string }> = {
  today: { icon: 'today', color: 'text-green-400' },
  upcoming: { icon: 'upcoming', color: 'text-blue-400' },
  blocked: { icon: 'block', color: 'text-red-400' },
};

function PlanItemCard({
  item,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
}: {
  item: PlanItem;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const { t } = useTranslation();
  const priorityColors: Record<string, string> = {
    high: 'border-l-red-400',
    medium: 'border-l-yellow-400',
    low: 'border-l-blue-400',
    none: 'border-l-gray-400',
  };

  return (
    <div
      className={clsx(
        'flex items-center gap-3 p-3 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg border-l-4',
        priorityColors[item.priority] || priorityColors.none
      )}
    >
      <div className="flex flex-col gap-0.5">
        <button
          onClick={onMoveUp}
          disabled={isFirst}
          className="p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-30 transition-colors"
          title={t('plan.moveUp')}
        >
          <span className="material-symbols-outlined text-base">arrow_upward</span>
        </button>
        <button
          onClick={onMoveDown}
          disabled={isLast}
          className="p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-30 transition-colors"
          title={t('plan.moveDown')}
        >
          <span className="material-symbols-outlined text-base">arrow_downward</span>
        </button>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[var(--color-text)] font-medium truncate">{item.title}</p>
        {item.description && (
          <p className="text-[var(--color-text-muted)] text-sm truncate mt-0.5">{item.description}</p>
        )}
      </div>
      {item.scheduledDate && (
        <span className="text-xs text-[var(--color-text-muted)] shrink-0">
          {new Date(item.scheduledDate).toLocaleDateString()}
        </span>
      )}
    </div>
  );
}

export default function PlanPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId('all');

  const [searchQuery, setSearchQuery] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['sections', 'plan', workspaceId],
    queryFn: () => sectionsService.getPlan(workspaceId),
    refetchInterval: 15000,
  });

  const groups = data?.groups ?? [];

  // Filter items within groups by search
  const filteredGroups = useMemo(() => {
    if (!searchQuery) return groups;
    const query = searchQuery.toLowerCase();
    return groups
      .map((group) => ({
        ...group,
        items: group.items.filter(
          (item) =>
            item.title.toLowerCase().includes(query) ||
            item.description?.toLowerCase().includes(query)
        ),
      }))
      .filter((group) => group.items.length > 0);
  }, [groups, searchQuery]);

  const handleMoveUp = useCallback(
    (_groupName: string, _itemIndex: number) => {
      // Reordering would call a plan items API to update sort_order
      // For now, just invalidate to refresh
      queryClient.invalidateQueries({ queryKey: ['sections', 'plan'] });
    },
    [queryClient]
  );

  const handleMoveDown = useCallback(
    (_groupName: string, _itemIndex: number) => {
      queryClient.invalidateQueries({ queryKey: ['sections', 'plan'] });
    },
    [queryClient]
  );

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('plan.title')} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('plan.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('plan.subtitle')}</p>
        </div>
      </div>

      {/* Search */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('plan.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
      </div>

      {/* Loading */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
        </div>
      ) : filteredGroups.length === 0 ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">{t('plan.empty')}</div>
      ) : (
        <div className="space-y-6">
          {filteredGroups.map((group) => {
            const focusType = group.name as FocusType;
            const config = FOCUS_TYPE_CONFIG[focusType] || FOCUS_TYPE_CONFIG.upcoming;
            return (
              <div key={group.name}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={clsx('material-symbols-outlined', config.color)}>{config.icon}</span>
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">
                    {t(`plan.group.${group.name}`)}
                  </h2>
                  <span className="text-sm text-[var(--color-text-muted)]">({group.items.length})</span>
                </div>
                <div className="space-y-2">
                  {group.items.map((item, index) => (
                    <PlanItemCard
                      key={item.id}
                      item={item}
                      onMoveUp={() => handleMoveUp(group.name, index)}
                      onMoveDown={() => handleMoveDown(group.name, index)}
                      isFirst={index === 0}
                      isLast={index === group.items.length - 1}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
