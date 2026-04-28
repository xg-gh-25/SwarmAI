/**
 * Channels settings tab.
 *
 * Lists connected channels with status. Add/edit/disconnect Slack.
 * Uses shared ChannelConfigForm for token input.
 */
import { useState, useEffect } from 'react';
import { channelsService } from '../../services/channels';
import type { Channel } from '../../types';
import ChannelConfigForm from './ChannelConfigForm';

export default function ChannelsTab() {
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
      await loadChannels();
    } catch (e) {
      console.error('Failed to disconnect channel:', e);
    }
  };

  const handleReconnect = async (channel: Channel) => {
    try {
      await channelsService.restart(channel.id);
      await loadChannels();
    } catch (e) {
      console.error('Failed to reconnect channel:', e);
    }
  };

  const slackChannel = channels.find(c => c.channelType === 'slack');

  if (loading) {
    return <div className="text-[var(--color-text-muted)]">Loading channels...</div>;
  }

  const isConnected = slackChannel && slackChannel.status === 'active';
  const isError = slackChannel && (slackChannel.status === 'error' || slackChannel.status === 'inactive' || slackChannel.status === 'failed');
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
                  Connected
                </span>
              ) : isError ? (
                <span className="ml-2 text-red-400 text-xs flex items-center gap-1 inline-flex">
                  <span className="w-1.5 h-1.5 bg-red-400 rounded-full" />
                  {slackChannel.status === 'error' ? 'Error' : slackChannel.status === 'failed' ? 'Failed' : 'Disconnected'}
                </span>
              ) : (
                <span className="ml-2 text-[var(--color-text-muted)] text-xs">Not set up</span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            {isConnected && !showForm && (
              <>
                <button
                  onClick={() => setEditingId(slackChannel?.id || null)}
                  className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] transition-colors"
                >
                  Edit
                </button>
                <button
                  onClick={() => slackChannel && handleDisconnect(slackChannel)}
                  className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  Disconnect
                </button>
              </>
            )}
            {isError && !showForm && (
              <>
                <button
                  onClick={() => slackChannel && handleReconnect(slackChannel)}
                  className="px-3 py-1 text-xs bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 transition-colors"
                >
                  Reconnect
                </button>
                <button
                  onClick={() => setEditingId(slackChannel?.id || null)}
                  className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] transition-colors"
                >
                  Edit
                </button>
                <button
                  onClick={() => slackChannel && handleDisconnect(slackChannel)}
                  className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
                >
                  Remove
                </button>
              </>
            )}
            {!slackChannel && !addingSlack && (
              <button
                onClick={() => setAddingSlack(true)}
                className="px-3 py-1 text-xs bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 transition-colors"
              >
                Set Up
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

function ConfigSummary({ channel }: { channel: Channel }) {
  const cfg = channel.config as Record<string, string>;
  const fields = [
    { label: 'Bot Token', value: cfg.bot_token },
    { label: 'App Token', value: cfg.app_token },
  ];

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-[var(--color-text-muted)] font-medium">Configuration</p>
      {fields.map(({ label, value }) => (
        <div key={label} className="flex items-center gap-3 text-xs">
          <span className="text-[var(--color-text-muted)] shrink-0 w-20">{label}</span>
          <code className="text-[var(--color-text)] font-mono bg-[var(--color-bg)] px-2 py-0.5 rounded truncate select-all">
            {value || '—'}
          </code>
        </div>
      ))}
      {channel.errorMessage && (
        <div className="text-xs text-red-400 mt-1">
          Error: {channel.errorMessage}
        </div>
      )}
    </div>
  );
}
