import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  SearchBar,
  Button,
  Modal,
  SkeletonTable,
  ResizableTable,
  ResizableTableCell,
  ConfirmDialog,
  Dropdown,
} from '../components/common';
import type { Channel, ChannelCreateRequest, ChannelUpdateRequest, ChannelType, ChannelAccessMode, Agent } from '../types';
import { channelsService } from '../services/channels';
import { agentsService } from '../services/agents';

const getChannelColumns = (t: (key: string) => string) => [
  { key: 'name', header: t('channels.table.name'), initialWidth: 180, minWidth: 120 },
  { key: 'type', header: t('channels.table.type'), initialWidth: 120, minWidth: 90 },
  { key: 'agent', header: t('channels.table.agent'), initialWidth: 160, minWidth: 120 },
  { key: 'status', header: t('channels.table.status'), initialWidth: 110, minWidth: 80 },
  { key: 'accessMode', header: t('channels.table.accessMode'), initialWidth: 110, minWidth: 80 },
  { key: 'actions', header: '', initialWidth: 160, minWidth: 120, align: 'right' as const },
];

const CHANNEL_TYPE_LABELS: Record<string, string> = {
  feishu: 'Feishu',
  slack: 'Slack',
  discord: 'Discord',
  web_widget: 'Web Widget',
};

function StatusBadgeInline({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    active: 'bg-status-success/20 text-status-success',
    inactive: 'bg-[var(--color-hover)] text-[var(--color-text-muted)]',
    error: 'bg-status-error/20 text-status-error',
    starting: 'bg-status-warning/20 text-status-warning',
    failed: 'bg-status-error/20 text-status-error',
  };
  return (
    <span className={`px-2 py-0.5 text-xs rounded ${colorMap[status] || colorMap.inactive}`}>
      {status}
    </span>
  );
}

export default function ChannelsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Channel | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null);

  const CHANNEL_COLUMNS = getChannelColumns(t);

  const { data: channels = [], isLoading } = useQuery({
    queryKey: ['channels'],
    queryFn: channelsService.list,
  });

  const deleteMutation = useMutation({
    mutationFn: channelsService.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['channels'] });
      setDeleteTarget(null);
    },
  });

  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  const startMutation = useMutation({
    mutationFn: channelsService.start,
    onSuccess: () => {
      setLifecycleError(null);
      queryClient.invalidateQueries({ queryKey: ['channels'] });
    },
    onError: (err: Error) => setLifecycleError(err.message),
  });

  const stopMutation = useMutation({
    mutationFn: channelsService.stop,
    onSuccess: () => {
      setLifecycleError(null);
      queryClient.invalidateQueries({ queryKey: ['channels'] });
    },
    onError: (err: Error) => setLifecycleError(err.message),
  });

  const filteredChannels = channels.filter((ch) =>
    ch.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    ch.channelType.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('channels.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('channels.subtitle')}</p>
        </div>
        <Button icon="add" onClick={() => setIsCreateOpen(true)}>
          {t('channels.addChannel')}
        </Button>
      </div>

      {/* Search */}
      <div className="mb-6">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t('channels.searchPlaceholder')}
          className="max-w-md"
        />
      </div>

      {/* Lifecycle Error Banner */}
      {lifecycleError && (
        <div className="mb-4 p-3 bg-status-error/10 border border-status-error/30 rounded-lg flex items-center justify-between">
          <p className="text-sm text-status-error">{lifecycleError}</p>
          <button onClick={() => setLifecycleError(null)} className="text-status-error hover:text-status-error/70">
            <span className="material-symbols-outlined text-lg">close</span>
          </button>
        </div>
      )}

      {/* Table */}
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {isLoading ? (
          <SkeletonTable rows={5} columns={6} />
        ) : filteredChannels.length === 0 ? (
          <div className="py-16 flex flex-col items-center justify-center">
            <span className="material-symbols-outlined text-5xl text-[var(--color-text-muted)] mb-4">hub</span>
            <p className="text-[var(--color-text)] font-medium mb-1">{t('channels.noChannels')}</p>
            <p className="text-[var(--color-text-muted)] text-sm mb-6">{t('channels.subtitle')}</p>
            {!searchQuery && (
              <Button icon="add" onClick={() => setIsCreateOpen(true)}>
                {t('channels.addChannel')}
              </Button>
            )}
          </div>
        ) : (
          <ResizableTable columns={CHANNEL_COLUMNS}>
            {filteredChannels.map((channel) => (
              <tr key={channel.id} className="border-b border-[var(--color-border)] hover:bg-[var(--color-hover)]">
                <ResizableTableCell>
                  <div>
                    <span className="text-[var(--color-text)] font-medium">{channel.name}</span>
                    {channel.errorMessage && (
                      <p className="text-xs text-status-error line-clamp-1 mt-0.5">{channel.errorMessage}</p>
                    )}
                  </div>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)]">
                    {CHANNEL_TYPE_LABELS[channel.channelType] || channel.channelType}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)]">{channel.agentName || channel.agentId}</span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <StatusBadgeInline status={channel.status} />
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)] text-sm">{channel.accessMode}</span>
                </ResizableTableCell>
                <ResizableTableCell align="right">
                  <div className="flex items-center justify-end gap-1">
                    {channel.status === 'active' ? (
                      <button
                        onClick={() => stopMutation.mutate(channel.id)}
                        disabled={stopMutation.isPending}
                        className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-warning hover:bg-status-warning/10 transition-colors"
                        title={t('channels.stop')}
                      >
                        <span className="material-symbols-outlined text-lg">stop_circle</span>
                      </button>
                    ) : (
                      <button
                        onClick={() => startMutation.mutate(channel.id)}
                        disabled={startMutation.isPending}
                        className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-success hover:bg-status-success/10 transition-colors"
                        title={t('channels.start')}
                      >
                        <span className="material-symbols-outlined text-lg">play_circle</span>
                      </button>
                    )}
                    <button
                      onClick={() => setEditTarget(channel)}
                      className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-primary hover:bg-primary/10 transition-colors"
                      title={t('common.button.edit')}
                    >
                      <span className="material-symbols-outlined text-lg">edit</span>
                    </button>
                    <button
                      onClick={() => setDeleteTarget(channel)}
                      className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                      title={t('common.button.delete')}
                    >
                      <span className="material-symbols-outlined text-lg">delete</span>
                    </button>
                  </div>
                </ResizableTableCell>
              </tr>
            ))}
          </ResizableTable>
        )}
      </div>

      {/* Delete Confirmation */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        title={t('channels.deleteChannel')}
        message={
          <>
            {t('common.message.confirmDelete')}{' '}
            <strong className="text-[var(--color-text)]">{deleteTarget?.name}</strong>?
            <br />
            <span className="text-sm text-[var(--color-text-muted)]">{t('common.message.cannotUndo')}</span>
          </>
        }
        confirmText={t('common.button.delete')}
        cancelText={t('common.button.cancel')}
        isLoading={deleteMutation.isPending}
      />

      {/* Create Channel Modal */}
      <Modal
        isOpen={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        title={t('channels.createChannel')}
        size="lg"
      >
        <ChannelFormModal
          onClose={() => setIsCreateOpen(false)}
        />
      </Modal>

      {/* Edit Channel Modal */}
      <Modal
        isOpen={editTarget !== null}
        onClose={() => setEditTarget(null)}
        title={t('channels.editChannel')}
        size="lg"
      >
        {editTarget && (
          <ChannelFormModal
            channel={editTarget}
            onClose={() => setEditTarget(null)}
          />
        )}
      </Modal>
    </div>
  );
}

// ============== Channel Form Modal ==============

function ChannelFormModal({
  channel,
  onClose,
}: {
  channel?: Channel;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const isEdit = !!channel;

  const [name, setName] = useState(channel?.name || '');
  const [channelType, setChannelType] = useState<ChannelType>(channel?.channelType || 'feishu');
  const [agentId, setAgentId] = useState(channel?.agentId || '');
  const [accessMode, setAccessMode] = useState<ChannelAccessMode>(channel?.accessMode || 'allowlist');
  const [allowedSenders, setAllowedSenders] = useState(channel?.allowedSenders?.join(', ') || '');
  const [blockedSenders, setBlockedSenders] = useState(channel?.blockedSenders?.join(', ') || '');
  const [rateLimitPerMinute, setRateLimitPerMinute] = useState(channel?.rateLimitPerMinute ?? 10);
  const [enableSkills, setEnableSkills] = useState(channel?.enableSkills ?? false);
  const [enableMcp, setEnableMcp] = useState(channel?.enableMcp ?? false);
  const [configFields, setConfigFields] = useState<Record<string, string>>(() => {
    const cfg = channel?.config || {};
    const result: Record<string, string> = {};
    for (const [k, v] of Object.entries(cfg)) {
      result[k] = typeof v === 'string' ? v : JSON.stringify(v);
    }
    return result;
  });
  const [error, setError] = useState<string | null>(null);

  // Fetch agents for dropdown
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: agentsService.list,
  });

  // Fetch channel types for config fields
  const { data: channelTypes = [] } = useQuery({
    queryKey: ['channelTypes'],
    queryFn: channelsService.listTypes,
  });

  const currentTypeInfo = channelTypes.find((ct) => ct.id === channelType);

  const createMutation = useMutation({
    mutationFn: (data: ChannelCreateRequest) => channelsService.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['channels'] });
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: (data: ChannelUpdateRequest) => channelsService.update(channel!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['channels'] });
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const testMutation = useMutation({
    mutationFn: () => channelsService.test(channel!.id),
    onError: (err: Error) => setError(err.message),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Build config from dynamic fields
    const config: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(configFields)) {
      if (v.trim()) config[k] = v.trim();
    }

    const parsedAllowed = allowedSenders
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    const parsedBlocked = blockedSenders
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    if (isEdit) {
      updateMutation.mutate({
        name,
        agentId,
        config,
        accessMode,
        allowedSenders: parsedAllowed,
        blockedSenders: parsedBlocked,
        rateLimitPerMinute,
        enableSkills,
        enableMcp,
      });
    } else {
      createMutation.mutate({
        name,
        channelType: channelType as ChannelCreateRequest['channelType'],
        agentId,
        config,
        accessMode,
        allowedSenders: parsedAllowed,
        rateLimitPerMinute,
        enableSkills,
        enableMcp,
      });
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-2">
          {t('channels.form.name')} <span className="text-status-error">*</span>
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('channels.form.namePlaceholder')}
          className="w-full px-4 py-2.5 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary"
          disabled={isPending}
          required
        />
      </div>

      {/* Channel Type (only for create) */}
      {!isEdit && (
        <Dropdown
          label={t('channels.form.channelType')}
          options={channelTypes.map((ct) => ({
            id: ct.id,
            name: ct.available ? ct.label : `${ct.label} (${t('channels.form.notInstalled')})`,
            description: ct.description,
          }))}
          selectedId={channelType}
          onChange={(id) => {
            setChannelType(id as ChannelType);
            setConfigFields({});
          }}
          placeholder={t('channels.form.channelType')}
          disabled={isPending}
        />
      )}

      {/* Agent */}
      <Dropdown
        label={t('channels.form.agent')}
        options={agents.map((agent: Agent) => ({
          id: agent.id,
          name: agent.name,
          description: agent.description,
        }))}
        selectedId={agentId}
        onChange={setAgentId}
        placeholder={t('channels.form.selectAgent')}
        disabled={isPending}
      />

      {/* Dynamic Config Fields */}
      {currentTypeInfo && currentTypeInfo.configFields.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text)] mb-2">
            {t('channels.form.configuration')}
          </label>
          <div className="space-y-3">
            {currentTypeInfo.configFields.map((field) => (
              <div key={field.key}>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                  {field.label} {field.required && <span className="text-status-error">*</span>}
                </label>
                <input
                  type={field.type === 'password' ? 'password' : 'text'}
                  value={configFields[field.key] || ''}
                  onChange={(e) => setConfigFields((prev) => ({ ...prev, [field.key]: e.target.value }))}
                  className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary text-sm"
                  disabled={isPending}
                  required={field.required}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Access Mode */}
      <Dropdown
        label={t('channels.form.accessMode')}
        options={[
          { id: 'open', name: t('channels.form.accessOpen') },
          { id: 'allowlist', name: t('channels.form.accessAllowlist') },
          { id: 'blocklist', name: t('channels.form.accessBlocklist') },
        ]}
        selectedId={accessMode}
        onChange={(id) => setAccessMode(id as ChannelAccessMode)}
        placeholder={t('channels.form.accessMode')}
        disabled={isPending}
      />

      {/* Allowed Senders (shown only for allowlist mode) */}
      {accessMode === 'allowlist' && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text)] mb-2">
            {t('channels.form.allowedSenders')}
          </label>
          <input
            type="text"
            value={allowedSenders}
            onChange={(e) => setAllowedSenders(e.target.value)}
            placeholder={t('channels.form.allowedSendersPlaceholder')}
            className="w-full px-4 py-2.5 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary"
            disabled={isPending}
          />
          <p className="mt-1.5 text-xs text-[var(--color-text-muted)]">{t('channels.form.allowedSendersHelp')}</p>
        </div>
      )}

      {/* Blocked Senders (shown only for blocklist mode) */}
      {accessMode === 'blocklist' && (
        <div>
          <label className="block text-sm font-medium text-[var(--color-text)] mb-2">
            {t('channels.form.blockedSenders')}
          </label>
          <input
            type="text"
            value={blockedSenders}
            onChange={(e) => setBlockedSenders(e.target.value)}
            placeholder={t('channels.form.blockedSendersPlaceholder')}
            className="w-full px-4 py-2.5 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-primary"
            disabled={isPending}
          />
          <p className="mt-1.5 text-xs text-[var(--color-text-muted)]">{t('channels.form.blockedSendersHelp')}</p>
        </div>
      )}

      {/* Rate Limit */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-2">
          {t('channels.form.rateLimit')}
        </label>
        <input
          type="number"
          value={rateLimitPerMinute}
          onChange={(e) => setRateLimitPerMinute(Number(e.target.value))}
          min={1}
          max={100}
          className="w-full px-4 py-2.5 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:border-primary"
          disabled={isPending}
        />
        <p className="mt-1.5 text-xs text-[var(--color-text-muted)]">{t('channels.form.rateLimitHelp')}</p>
      </div>

      {/* Toggles */}
      <div className="flex items-center gap-6">
        <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={enableSkills}
            onChange={(e) => setEnableSkills(e.target.checked)}
            className="w-4 h-4 rounded border-[var(--color-border)] text-primary focus:ring-primary"
            disabled={isPending}
          />
          {t('channels.form.enableSkills')}
        </label>
        <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={enableMcp}
            onChange={(e) => setEnableMcp(e.target.checked)}
            className="w-4 h-4 rounded border-[var(--color-border)] text-primary focus:ring-primary"
            disabled={isPending}
          />
          {t('channels.form.enableMcp')}
        </label>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-status-error/10 border border-status-error/30 rounded-lg">
          <p className="text-sm text-status-error">{error}</p>
        </div>
      )}

      {/* Test config result */}
      {testMutation.isSuccess && (
        <div
          className={`p-3 rounded-lg border ${
            testMutation.data.valid
              ? 'bg-status-success/10 border-status-success/30'
              : 'bg-status-error/10 border-status-error/30'
          }`}
        >
          <p className={`text-sm ${testMutation.data.valid ? 'text-status-success' : 'text-status-error'}`}>
            {testMutation.data.valid ? t('channels.testSuccess') : testMutation.data.error || t('channels.testFailed')}
          </p>
        </div>
      )}

      {/* Actions */}
      <div className="flex justify-between pt-2">
        <div>
          {isEdit && (
            <Button
              type="button"
              variant="secondary"
              onClick={() => testMutation.mutate()}
              isLoading={testMutation.isPending}
              disabled={isPending}
            >
              {t('channels.testConfig')}
            </Button>
          )}
        </div>
        <div className="flex gap-3">
          <Button type="button" variant="secondary" onClick={onClose} disabled={isPending}>
            {t('common.button.cancel')}
          </Button>
          <Button type="submit" isLoading={isPending} disabled={!name.trim() || !agentId}>
            {isEdit ? t('common.button.saveChanges') : t('common.button.create')}
          </Button>
        </div>
      </div>
    </form>
  );
}
