import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { sectionsService } from '../services/sections';
import { Breadcrumb } from '../components/common';
import type { Artifact, ArtifactType } from '../types/artifact';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

const TYPE_CONFIG: Record<ArtifactType, { icon: string; color: string }> = {
  plan: { icon: 'description', color: 'text-blue-400' },
  report: { icon: 'summarize', color: 'text-green-400' },
  doc: { icon: 'article', color: 'text-purple-400' },
  decision: { icon: 'gavel', color: 'text-yellow-400' },
  other: { icon: 'folder', color: 'text-gray-400' },
};

function ArtifactCard({ item }: { item: Artifact }) {
  const config = TYPE_CONFIG[item.artifactType] || TYPE_CONFIG.other;

  return (
    <div className="p-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg hover:border-primary/30 transition-colors">
      <div className="flex items-start gap-3">
        <span className={clsx('material-symbols-outlined text-2xl mt-0.5', config.color)}>
          {config.icon}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-[var(--color-text)] font-medium truncate">{item.title}</p>
          <div className="flex items-center gap-3 mt-1 text-xs text-[var(--color-text-muted)]">
            <span className="flex items-center gap-1">
              <span className="material-symbols-outlined text-xs">history</span>
              v{item.version}
            </span>
            <span>{new Date(item.createdAt).toLocaleDateString()}</span>
            <span>{item.createdBy}</span>
          </div>
          {/* Tags */}
          {item.tags && item.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {item.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded-full"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ArtifactsPage() {
  const { t } = useTranslation();
  const workspaceId = useWorkspaceId('all');

  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<ArtifactType | 'all'>('all');

  const { data, isLoading } = useQuery({
    queryKey: ['sections', 'artifacts', workspaceId],
    queryFn: () => sectionsService.getArtifacts(workspaceId),
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
          items: group.items.filter(
            (item) =>
              item.title.toLowerCase().includes(query) ||
              item.tags?.some((tag) => tag.toLowerCase().includes(query))
          ),
        }))
        .filter((group) => group.items.length > 0);
    }

    return result;
  }, [groups, typeFilter, searchQuery]);

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('artifacts.title')} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('artifacts.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('artifacts.subtitle')}</p>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('artifacts.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as ArtifactType | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('artifacts.filter.all')}</option>
          <option value="plan">{t('artifacts.filter.plan')}</option>
          <option value="report">{t('artifacts.filter.report')}</option>
          <option value="doc">{t('artifacts.filter.doc')}</option>
          <option value="decision">{t('artifacts.filter.decision')}</option>
          <option value="other">{t('artifacts.filter.other')}</option>
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
        </div>
      ) : filteredGroups.length === 0 ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">{t('artifacts.empty')}</div>
      ) : (
        <div className="space-y-6">
          {filteredGroups.map((group) => {
            const artifactType = group.name as ArtifactType;
            const config = TYPE_CONFIG[artifactType] || TYPE_CONFIG.other;
            return (
              <div key={group.name}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={clsx('material-symbols-outlined', config.color)}>{config.icon}</span>
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">
                    {t(`artifacts.group.${group.name}`)}
                  </h2>
                  <span className="text-sm text-[var(--color-text-muted)]">({group.items.length})</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {group.items.map((item) => (
                    <ArtifactCard key={item.id} item={item} />
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
