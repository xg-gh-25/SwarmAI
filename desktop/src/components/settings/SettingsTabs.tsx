/**
 * Settings page tab layout wrapper (design docs/design/2026-11-01-settings-account-merge.md §3).
 *
 * 7 tabs: General, Swarm AWS Account (the one AWS account: sign-in, main region,
 * capabilities, Hive count, Hives left in other accounts), LLM Models (how the model is
 * called and which model), Channels, Backup, System, About. The old Hive Accounts tab moved
 * into Swarm AWS Account; Core Engine and the runtime Capabilities readout were removed
 * (XG, 2026-11-01). Skills + MCP Servers live in the "Capabilities" nav card, Hive fleet
 * management in the "Hive" nav card. Tab ids are kept ("aws-account", "ai-models") so
 * deep links keep working. Supports initialTab so sidebar icons can deep-link to a tab.
 */
import { Tabs } from '../overlay';
import { useState, useEffect } from 'react';
import GeneralTab from './GeneralTab';
import AIModelsTab from './AIModelsTab';
import ChannelsTab from './ChannelsTab';
import SystemTab from './SystemTab';
import AboutTab from './AboutTab';
import AwsAccountTab from './AwsAccountTab';
import BackupTab from './BackupTab';

/**
 * Width tiers:
 * - '4xl'   — max-w-3xl 768px  (every tab)
 * ('full' is retained in WIDTH_CLASSES for any future full-bleed tab; no tab uses it
 *  now that Skills moved to the Capabilities overlay.)
 */
const ALL_TABS = [
  { id: 'general', label: 'General', icon: 'settings', width: '4xl' as const },
  { id: 'aws-account', label: 'Swarm AWS Account', icon: 'account_circle', width: '4xl' as const },
  { id: 'ai-models', label: 'LLM Models', icon: 'smart_toy', width: '4xl' as const },
  { id: 'channels', label: 'Channels', icon: 'forum', width: '4xl' as const },
  { id: 'backup', label: 'Backup', icon: 'cloud_upload', width: '4xl' as const },
  { id: 'system', label: 'System', icon: 'dns', width: '4xl' as const },
  { id: 'about', label: 'About', icon: 'info', width: '4xl' as const },
] as const;

const WIDTH_CLASSES = {
  'full': 'p-6',
  '4xl': 'max-w-3xl mx-auto p-6',
} as const;

type TabId = typeof ALL_TABS[number]['id'];

/** Tab ids that no longer exist → where their content went (old deep links). */
const MOVED: Record<string, TabId> = { hive: 'aws-account', engine: 'general', capabilities: 'general' };

function resolveTab(id: string | undefined): TabId | null {
  const target = id && id in MOVED ? MOVED[id] : id;
  return ALL_TABS.find((t) => t.id === target)?.id ?? null;
}

interface SettingsTabsProps {
  initialTab?: string;
}

export default function SettingsTabs({ initialTab }: SettingsTabsProps) {
  const TABS = ALL_TABS;

  const [activeTab, setActiveTab] = useState<TabId>(() => resolveTab(initialTab) ?? 'general');

  // Update when initialTab prop changes (e.g., sidebar navigation)
  useEffect(() => {
    const valid = resolveTab(initialTab);
    if (valid) setActiveTab(valid);
  }, [initialTab]);

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar — pinned at top, gradient hints when overflowing */}
      <div className="shrink-0 px-6 py-3 border-b border-[var(--color-border)] flex justify-center-safe">
        <Tabs label="Settings" value={activeTab} onChange={setActiveTab}
          items={TABS.map((tab) => ({ key: tab.id, label: tab.label, icon: tab.icon }))} />
      </div>

      {/* Tab content — scrollable, fills remaining space */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {(() => {
          const widthClass = WIDTH_CLASSES[TABS.find(t => t.id === activeTab)?.width ?? '4xl'];
          const content = (
            <>
              {activeTab === 'general' && <GeneralTab />}
              {activeTab === 'aws-account' && <AwsAccountTab />}
              {activeTab === 'ai-models' && <AIModelsTab />}
              {activeTab === 'channels' && <ChannelsTab />}
              {activeTab === 'backup' && <BackupTab />}
              {activeTab === 'system' && <SystemTab />}
              {activeTab === 'about' && <AboutTab />}
            </>
          );
          return widthClass ? <div className={widthClass}>{content}</div> : content;
        })()}
      </div>
    </div>
  );
}
