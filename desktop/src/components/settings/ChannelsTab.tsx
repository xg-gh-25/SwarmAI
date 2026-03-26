/**
 * Channels settings tab.
 *
 * Lists connected channels with status. Add/edit/disconnect Slack and Feishu.
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
  const [addingType, setAddingType] = useState<'slack' | 'feishu' | null>(null);

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
  const feishuChannel = channels.find(c => c.channelType === 'feishu');

  if (loading) {
    return <div className="text-[var(--color-text-muted)]">Loading channels...</div>;
  }

  return (
    <div className="space-y-4">
      {/* Slack card */}
      <ChannelCard
        icon="💬"
        name="Slack"
        channel={slackChannel}
        channelType="slack"
        editing={editingId === slackChannel?.id}
        adding={addingType === 'slack'}
        onEdit={() => setEditingId(slackChannel?.id || null)}
        onAdd={() => setAddingType('slack')}
        onDisconnect={() => slackChannel && handleDisconnect(slackChannel)}
        onReconnect={() => slackChannel && handleReconnect(slackChannel)}
        onSave={() => { setEditingId(null); setAddingType(null); loadChannels(); }}
        onCancel={() => { setEditingId(null); setAddingType(null); }}
      />

      {/* Feishu card */}
      <ChannelCard
        icon="🐦"
        name="Feishu"
        channel={feishuChannel}
        channelType="feishu"
        editing={editingId === feishuChannel?.id}
        adding={addingType === 'feishu'}
        onEdit={() => setEditingId(feishuChannel?.id || null)}
        onAdd={() => setAddingType('feishu')}
        onDisconnect={() => feishuChannel && handleDisconnect(feishuChannel)}
        onReconnect={() => feishuChannel && handleReconnect(feishuChannel)}
        onSave={() => { setEditingId(null); setAddingType(null); loadChannels(); }}
        onCancel={() => { setEditingId(null); setAddingType(null); }}
      />
    </div>
  );
}

interface ChannelCardProps {
  icon: string;
  name: string;
  channel?: Channel;
  channelType: 'slack' | 'feishu';
  editing: boolean;
  adding: boolean;
  onEdit: () => void;
  onAdd: () => void;
  onDisconnect: () => void;
  onReconnect: () => void;
  onSave: () => void;
  onCancel: () => void;
}

function ChannelCard({
  icon, name, channel, channelType, editing, adding,
  onEdit, onAdd, onDisconnect, onReconnect, onSave, onCancel,
}: ChannelCardProps) {
  const isConnected = channel && channel.status === 'active';
  const isError = channel && (channel.status === 'error' || channel.status === 'inactive' || channel.status === 'failed');

  return (
    <div className="bg-[var(--color-card)] rounded-lg p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl">{icon}</span>
          <div>
            <span className="text-[var(--color-text)] font-medium">{name}</span>
            {isConnected ? (
              <span className="ml-2 text-green-400 text-xs flex items-center gap-1 inline-flex">
                <span className="w-1.5 h-1.5 bg-green-400 rounded-full" />
                Connected
              </span>
            ) : isError ? (
              <span className="ml-2 text-red-400 text-xs flex items-center gap-1 inline-flex">
                <span className="w-1.5 h-1.5 bg-red-400 rounded-full" />
                {channel.status === 'error' ? 'Error' : channel.status === 'failed' ? 'Failed' : 'Disconnected'}
              </span>
            ) : (
              <span className="ml-2 text-[var(--color-text-muted)] text-xs">Not set up</span>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {/* Connected: Edit + Disconnect */}
          {isConnected && !editing && (
            <>
              <button
                onClick={onEdit}
                className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] transition-colors"
              >
                Edit
              </button>
              <button
                onClick={onDisconnect}
                className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
              >
                Disconnect
              </button>
            </>
          )}
          {/* Error/Stopped: Reconnect + Edit + Disconnect */}
          {isError && !editing && (
            <>
              <button
                onClick={onReconnect}
                className="px-3 py-1 text-xs bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 transition-colors"
              >
                Reconnect
              </button>
              <button
                onClick={onEdit}
                className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] transition-colors"
              >
                Edit
              </button>
              <button
                onClick={onDisconnect}
                className="px-3 py-1 text-xs text-red-400 hover:text-red-300 transition-colors"
              >
                Remove
              </button>
            </>
          )}
          {/* No channel: Set Up */}
          {!channel && !adding && (
            <button
              onClick={onAdd}
              className="px-3 py-1 text-xs bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 transition-colors"
            >
              Set Up
            </button>
          )}
        </div>
      </div>

      {/* Show existing config summary when channel exists but not editing */}
      {channel && !editing && !adding && (
        <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
          <ConfigSummary channel={channel} />
        </div>
      )}

      {(editing || adding) && (
        <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
          <ChannelConfigForm
            channelType={channelType}
            existingConfig={channel}
            onSave={onSave}
            onCancel={onCancel}
          />
        </div>
      )}
    </div>
  );
}

function ConfigSummary({ channel }: { channel: Channel }) {
  const cfg = channel.config as Record<string, string>;
  const fields = channel.channelType === 'slack'
    ? [
        { label: 'Bot Token', value: cfg.bot_token },
        { label: 'App Token', value: cfg.app_token },
      ]
    : [
        { label: 'App ID', value: cfg.app_id },
        { label: 'App Secret', value: cfg.app_secret },
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
