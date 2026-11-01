/**
 * Channels settings tab.
 *
 * Lists connected channels with status. Add/edit/disconnect Slack.
 * Uses shared ChannelConfigForm for token input.
 */
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { channelsService } from '../../services/channels';
import type { Channel } from '../../types';
import ChannelConfigForm from './ChannelConfigForm';
import { useToast } from '../../contexts/ToastContext';

export default function ChannelsTab() {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [addingSlack, setAddingSlack] = useState(false);

  const loadChannels = async () => {
    try {
      const list = await channelsService.list();
      setChannels(list);
    } catch {
      // No channels configured
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadChannels(); }, []);

  const handleDisconnect = async (channel: Channel) => {
    try {
      await channelsService.stop(channel.id);
      await channelsService.delete(channel.id);
      addToast({ severity: 'success', message: t('ui.ChannelsTab.disconnected') });
      await loadChannels();
    } catch (e) {
      addToast({ severity: 'error', message: t('ui.ChannelsTab.disconnectFailed', { error: e instanceof Error ? e.message : t('ui.ChannelsTab.unknownError') }) });
    }
  };

  const handleReconnect = async (channel: Channel) => {
    try {
      await channelsService.restart(channel.id);
      addToast({ severity: 'success', message: t('ui.ChannelsTab.reconnecting') });
      await loadChannels();
    } catch (e) {
      addToast({ severity: 'error', message: t('ui.ChannelsTab.reconnectFailed', { error: e instanceof Error ? e.message : t('ui.ChannelsTab.unknownError') }) });
    }
  };

  const slackChannel = channels.find(c => c.channelType === 'slack');

  if (loading) {
    return <div className="text-[var(--color-text-muted)]">{t('ui.ChannelsTab.loading')}</div>;
  }

  const isConnected = slackChannel && slackChannel.status === 'active';
  const isError = slackChannel && (slackChannel.status === 'error' || slackChannel.status === 'inactive' || slackChannel.status === 'failed' || slackChannel.status === 'auth_error');
  const showForm = editingId === slackChannel?.id || addingSlack;

  return (
    <div className="space-y-4">
      <div className="bg-[var(--color-card)] rounded-lg p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xl">💬</span>
            <div>
              <span className="text-[var(--color-text)] font-medium">Slack</span>
              {isConnected ? (
                <span className="ml-2 text-green-400 text-xs flex items-center gap-1 inline-flex">
                  <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                  {t('onboarding.channels.connected')}
                </span>
              ) : isError ? (
                <span className="ml-2 text-red-400 text-xs flex items-center gap-1 inline-flex">
                  <span className="w-1.5 h-1.5 bg-red-400 rounded-full" />
                  {slackChannel.status === 'error' ? t('common.status.error') : slackChannel.status === 'failed' ? t('tasks.status.failed') : t('ui.ChannelsTab.statusDisconnected')}
                </span>
              ) : (
                <span className="ml-2 text-[var(--color-text-muted)] text-xs">{t('ui.ChannelsTab.notSetUp')}</span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            {isConnected && !showForm && (
              <>
                <button
                  onClick={() => setEditingId(slackChannel?.id || null)}
                  className="btn-sm btn-secondary transition-colors"
                >
                  {t('common.button.edit')}
                </button>
                <button
                  onClick={() => slackChannel && handleDisconnect(slackChannel)}
                  className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  {t('ui.ChannelsTab.disconnect')}
                </button>
              </>
            )}
            {isError && !showForm && (
              <>
                <button
                  onClick={() => slackChannel && handleReconnect(slackChannel)}
                  className="btn-sm btn-primary transition-colors"
                >
                  {t('ui.ChannelsTab.reconnect')}
                </button>
                <button
                  onClick={() => setEditingId(slackChannel?.id || null)}
                  className="btn-sm btn-secondary transition-colors"
                >
                  {t('common.button.edit')}
                </button>
                <button
                  onClick={() => slackChannel && handleDisconnect(slackChannel)}
                  className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  {t('ui.ChannelsTab.remove')}
                </button>
              </>
            )}
            {!slackChannel && !addingSlack && (
              <button
                onClick={() => setAddingSlack(true)}
                className="btn-sm btn-primary transition-colors"
              >
                {t('onboarding.channels.setUp')}
              </button>
            )}
          </div>
        </div>

        {/* Config summary */}
        {slackChannel && !showForm && (
          <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
            <ConfigSummary channel={slackChannel} />
          </div>
        )}

        {showForm && (
          <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
            <ChannelConfigForm
              channelType="slack"
              existingConfig={slackChannel}
              onSave={() => { setEditingId(null); setAddingSlack(false); loadChannels(); }}
              onCancel={() => { setEditingId(null); setAddingSlack(false); }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/** Mask a token to show only prefix + last 4 chars */
function maskToken(token: string | undefined): string {
  if (!token) return '—';
  if (token.length <= 10) return '••••••••';
  const prefix = token.slice(0, token.indexOf('-', 4) + 1) || token.slice(0, 5);
  const suffix = token.slice(-4);
  return `${prefix}••••${suffix}`;
}

function ConfigSummary({ channel }: { channel: Channel }) {
  const { t } = useTranslation();
  const cfg = channel.config as Record<string, string>;
  const fields = [
    { label: 'Bot Token', value: cfg.bot_token },
    { label: 'App Token', value: cfg.app_token },
  ];

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-[var(--color-text-muted)] font-medium">{t('channels.form.configuration')}</p>
      {fields.map(({ label, value }) => (
        <div key={label} className="flex items-center gap-3 text-xs">
          <span className="text-[var(--color-text-muted)] shrink-0 w-20">{label}</span>
          <code className="text-[var(--color-text)] font-mono bg-[var(--color-bg)] px-2 py-0.5 rounded truncate">
            {maskToken(value)}
          </code>
        </div>
      ))}
      {channel.errorMessage && (
        <div className="text-xs text-red-400 mt-1">
          {t('ui.ChannelsTab.errorPrefix', { error: channel.errorMessage })}
        </div>
      )}
    </div>
  );
}
