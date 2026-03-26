/**
 * Shared channel configuration form.
 *
 * Used by both OnboardingPage (compact mode) and Settings Channels tab (full mode).
 * Handles token input, verify, and save for Slack and Feishu channels.
 */
import { useState } from 'react';
import { channelsService } from '../../services/channels';
import type { Channel } from '../../types';

interface ChannelConfigFormProps {
  channelType: 'slack' | 'feishu';
  existingConfig?: Channel | null;
  compact?: boolean;   // true = onboarding (tokens only), false = settings (full config)
  onSave: () => void;
  onCancel: () => void;
}

export default function ChannelConfigForm({
  channelType,
  existingConfig,
  compact: _compact = false,
  onSave,
  onCancel,
}: ChannelConfigFormProps) {
  // Slack fields
  const [botToken, setBotToken] = useState(
    (existingConfig?.config as Record<string, string>)?.bot_token || ''
  );
  const [appToken, setAppToken] = useState(
    (existingConfig?.config as Record<string, string>)?.app_token || ''
  );

  // Feishu fields
  const [appId, setAppId] = useState(
    (existingConfig?.config as Record<string, string>)?.app_id || ''
  );
  const [appSecret, setAppSecret] = useState(
    (existingConfig?.config as Record<string, string>)?.app_secret || ''
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setError(null);

    try {
      const config = channelType === 'slack'
        ? { bot_token: botToken, app_token: appToken }
        : { app_id: appId, app_secret: appSecret };

      if (existingConfig) {
        await channelsService.update(existingConfig.id, { config });
      } else {
        const channel = await channelsService.create({
          name: channelType === 'slack' ? 'Slack' : 'Feishu',
          channelType,
          agentId: 'default',
          config,
        });
        // Start the channel after creation
        await channelsService.start(channel.id);
      }
      onSave();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const isValid = channelType === 'slack'
    ? botToken.startsWith('xoxb-') && appToken.startsWith('xapp-')
    : appId.length > 0 && appSecret.length > 0;

  return (
    <div className="space-y-3">
      {channelType === 'slack' ? (
        <>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Bot Token</label>
            <input
              type="password"
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              placeholder="xoxb-..."
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">App Token</label>
            <input
              type="password"
              value={appToken}
              onChange={(e) => setAppToken(e.target.value)}
              placeholder="xapp-..."
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <a
            href="https://api.slack.com/apps"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-[var(--color-primary)] hover:underline"
          >
            How to create a Slack bot app
          </a>
        </>
      ) : (
        <>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">App ID</label>
            <input
              type="text"
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
              placeholder="cli_..."
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">App Secret</label>
            <input
              type="password"
              value={appSecret}
              onChange={(e) => setAppSecret(e.target.value)}
              placeholder="..."
              className="w-full px-3 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <a
            href="https://open.feishu.cn/app"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-[var(--color-primary)] hover:underline"
          >
            Feishu Open Platform
          </a>
        </>
      )}

      {error && (
        <div className="p-2 bg-red-500/10 border border-red-500/20 rounded text-xs text-red-400">
          {error}
        </div>
      )}

      <div className="flex gap-2 pt-1">
        <button
          onClick={handleSave}
          disabled={!isValid || saving}
          className="px-4 py-1.5 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {saving ? 'Verifying...' : 'Verify & Connect'}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
