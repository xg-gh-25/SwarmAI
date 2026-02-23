import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { sectionsService } from '../services/sections';
import { Breadcrumb } from '../components/common';
import type { Communication, CommunicationStatus } from '../types/communication';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

const STATUS_CONFIG: Record<CommunicationStatus, { icon: string; color: string }> = {
  pendingReply: { icon: 'hourglass_top', color: 'bg-yellow-500/20 text-yellow-400' },
  aiDraft: { icon: 'smart_toy', color: 'bg-purple-500/20 text-purple-400' },
  followUp: { icon: 'reply', color: 'bg-blue-500/20 text-blue-400' },
  sent: { icon: 'check_circle', color: 'bg-green-500/20 text-green-400' },
  cancelled: { icon: 'cancel', color: 'bg-gray-500/20 text-gray-400' },
};

function CommStatusBadge({ status }: { status: CommunicationStatus }) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pendingReply;
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', config.color)}>
      <span className="material-symbols-outlined text-sm">{config.icon}</span>
      {t(`communicate.status.${status}`)}
    </span>
  );
}

function CommunicationCard({ item }: { item: Communication }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="p-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-[var(--color-text)] font-medium">{item.title}</p>
          <div className="flex items-center gap-3 mt-1 text-sm text-[var(--color-text-muted)]">
            <span className="flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">person</span>
              {item.recipient}
            </span>
            <span className="capitalize">{item.channelType}</span>
          </div>
        </div>
        <CommStatusBadge status={item.status} />
      </div>

      {item.description && (
        <p className="text-sm text-[var(--color-text-muted)] mt-2">{item.description}</p>
      )}

      {/* AI Draft expandable section */}
      {item.aiDraftContent && (
        <div className="mt-3">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-sm text-primary hover:text-primary/80 transition-colors"
          >
            <span className="material-symbols-outlined text-sm">
              {expanded ? 'expand_less' : 'expand_more'}
            </span>
            {t('communicate.aiDraft')}
          </button>
          {expanded && (
            <div className="mt-2 p-3 bg-[var(--color-input-bg)] rounded-lg text-sm text-[var(--color-text)] whitespace-pre-wrap">
              {item.aiDraftContent}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CommunicatePage() {
  const { t } = useTranslation();
  const workspaceId = useWorkspaceId('all');

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<CommunicationStatus | 'all'>('all');

  const { data, isLoading } = useQuery({
    queryKey: ['sections', 'communicate', workspaceId],
    queryFn: () => sectionsService.getCommunicate(workspaceId),
    refetchInterval: 15000,
  });

  const groups = data?.groups ?? [];

  // Apply filters
  const filteredGroups = useMemo(() => {
    let result = groups;

    // Filter by status (group name)
    if (statusFilter !== 'all') {
      result = result.filter((g) => g.name === statusFilter);
    }

    // Filter by search
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result
        .map((group) => ({
          ...group,
          items: group.items.filter(
            (item) =>
              item.title.toLowerCase().includes(query) ||
              item.recipient.toLowerCase().includes(query) ||
              item.description?.toLowerCase().includes(query)
          ),
        }))
        .filter((group) => group.items.length > 0);
    }

    return result;
  }, [groups, statusFilter, searchQuery]);

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('communicate.title')} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('communicate.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('communicate.subtitle')}</p>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('communicate.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as CommunicationStatus | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('communicate.filter.all')}</option>
          <option value="pendingReply">{t('communicate.filter.pendingReply')}</option>
          <option value="aiDraft">{t('communicate.filter.aiDraft')}</option>
          <option value="followUp">{t('communicate.filter.followUp')}</option>
          <option value="sent">{t('communicate.filter.sent')}</option>
          <option value="cancelled">{t('communicate.filter.cancelled')}</option>
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-12">
          <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
        </div>
      ) : filteredGroups.length === 0 ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">{t('communicate.empty')}</div>
      ) : (
        <div className="space-y-6">
          {filteredGroups.map((group) => {
            const statusKey = group.name as CommunicationStatus;
            const config = STATUS_CONFIG[statusKey] || STATUS_CONFIG.pendingReply;
            return (
              <div key={group.name}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={clsx('material-symbols-outlined text-lg', config.color.split(' ')[1])}>
                    {config.icon}
                  </span>
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">
                    {t(`communicate.group.${group.name}`)}
                  </h2>
                  <span className="text-sm text-[var(--color-text-muted)]">({group.items.length})</span>
                </div>
                <div className="space-y-2">
                  {group.items.map((item) => (
                    <CommunicationCard key={item.id} item={item} />
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
