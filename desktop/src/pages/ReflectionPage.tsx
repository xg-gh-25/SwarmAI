import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { sectionsService } from '../services/sections';
import { Breadcrumb } from '../components/common';
import type { Reflection, ReflectionType } from '../types/reflection';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

const TYPE_CONFIG: Record<ReflectionType, { icon: string; color: string }> = {
  dailyRecap: { icon: 'today', color: 'text-blue-400' },
  weeklySummary: { icon: 'date_range', color: 'text-green-400' },
  lessonsLearned: { icon: 'lightbulb', color: 'text-yellow-400' },
};

function ReflectionCard({ item }: { item: Reflection }) {
  const config = TYPE_CONFIG[item.reflectionType] || TYPE_CONFIG.dailyRecap;

  return (
    <div className="p-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg hover:border-primary/30 transition-colors">
      <div className="flex items-start gap-3">
        <span className={clsx('material-symbols-outlined text-2xl mt-0.5', config.color)}>
          {config.icon}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[var(--color-text)] font-medium truncate">{item.title}</p>
          <div className="flex items-center gap-3 mt-1 text-xs text-[var(--color-text-muted)]">
            <span>
              {new Date(item.periodStart).toLocaleDateString()} – {new Date(item.periodEnd).toLocaleDateString()}
            </span>
            <span className="capitalize">{item.generatedBy}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ReflectionPage() {
  const { t } = useTranslation();
  const workspaceId = useWorkspaceId('all');

  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<ReflectionType | 'all'>('all');

  const { data, isLoading } = useQuery({
    queryKey: ['sections', 'reflection', workspaceId],
    queryFn: () => sectionsService.getReflection(workspaceId),
    refetchInterval: 15000,
  });

  const groups = data?.groups ?? [];

  const filteredGroups = useMemo(() => {
    let result = groups;

    if (typeFilter !== 'all') {
      result = result.filter((g) => g.name === typeFilter);
    }

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result
        .map((group) => ({
          ...group,
          items: group.items.filter((item) => item.title.toLowerCase().includes(query)),
        }))
        .filter((group) => group.items.length > 0);
    }

    return result;
  }, [groups, typeFilter, searchQuery]);

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('reflection.title')} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('reflection.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('reflection.subtitle')}</p>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('reflection.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as ReflectionType | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('reflection.filter.all')}</option>
          <option value="dailyRecap">{t('reflection.filter.dailyRecap')}</option>
          <option value="weeklySummary">{t('reflection.filter.weeklySummary')}</option>
          <option value="lessonsLearned">{t('reflection.filter.lessonsLearned')}</option>
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
        </div>
      ) : filteredGroups.length === 0 ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">{t('reflection.empty')}</div>
      ) : (
        <div className="space-y-6">
          {filteredGroups.map((group) => {
            const reflectionType = group.name as ReflectionType;
            const config = TYPE_CONFIG[reflectionType] || TYPE_CONFIG.dailyRecap;
            return (
              <div key={group.name}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={clsx('material-symbols-outlined', config.color)}>{config.icon}</span>
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">
                    {t(`reflection.group.${group.name}`)}
                  </h2>
                  <span className="text-sm text-[var(--color-text-muted)]">({group.items.length})</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {group.items.map((item) => (
                    <ReflectionCard key={item.id} item={item} />
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
