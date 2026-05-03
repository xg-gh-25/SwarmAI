/**
 * Settings page tab layout wrapper.
 *
 * 7 tabs: General, AI & Models, Channels, Skills, MCP Servers, System, About.
 * Supports initialTab prop so sidebar icons can deep-link to a specific tab.
 */
import { useState, useEffect, useMemo } from 'react';
import GeneralTab from './GeneralTab';
import AIModelsTab from './AIModelsTab';
import ChannelsTab from './ChannelsTab';
import SkillsSettingsTab from './SkillsTab';
import MCPServersTab from './MCPServersTab';
import SystemTab from './SystemTab';
import EngineMetricsTab from './EngineMetricsTab';
import AboutTab from './AboutTab';
import HiveTab from './HiveTab';
import BackupTab from './BackupTab';
import { isDesktop } from '../../services/tauri';

const ALL_TABS = [
  { id: 'general', label: 'General', icon: 'settings' },
  { id: 'ai-models', label: 'AI & Models', icon: 'smart_toy' },
  { id: 'channels', label: 'Channels', icon: 'forum' },
  { id: 'skills', label: 'Skills', icon: 'extension' },
  { id: 'mcp-servers', label: 'MCP Servers', icon: 'device_hub' },
  { id: 'hive', label: 'Hive', icon: 'cloud', desktopOnly: true },
  { id: 'backup', label: 'Backup', icon: 'cloud_upload' },
  { id: 'engine', label: 'Core Engine', icon: 'psychology' },
  { id: 'system', label: 'System', icon: 'dns' },
  { id: 'about', label: 'About', icon: 'info' },
] as const;

type TabId = typeof ALL_TABS[number]['id'];

interface SettingsTabsProps {
  initialTab?: string;
}

export default function SettingsTabs({ initialTab }: SettingsTabsProps) {
  // Hive management (deploy/stop/delete) is desktop-only — a Hive instance
  // should not be able to create/destroy other Hives or manage AWS accounts.
  const TABS = useMemo(() => {
    const desktop = isDesktop();
    return ALL_TABS.filter(t => !('desktopOnly' in t && t.desktopOnly) || desktop);
  }, []);

  const [activeTab, setActiveTab] = useState<TabId>(() => {
    const valid = TABS.find(t => t.id === initialTab);
    return valid ? valid.id : 'general';
  });

  // Update when initialTab prop changes (e.g., sidebar navigation)
  useEffect(() => {
    if (initialTab) {
      const valid = TABS.find(t => t.id === initialTab);
      if (valid) setActiveTab(valid.id);
    }
  }, [initialTab, TABS]);

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar — pinned at top */}
      <div className="shrink-0 px-6 pt-4 border-b border-[var(--color-border)] overflow-x-auto">
        <div className="flex gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2.5 text-sm font-medium transition-colors flex items-center gap-1.5 border-b-2 -mb-px whitespace-nowrap ${
                activeTab === tab.id
                  ? 'text-[var(--color-primary)] border-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)] hover:border-[var(--color-border)]'
              }`}
            >
              <span className="material-symbols-outlined text-base">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content — scrollable, fills remaining space */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* Wide tabs (data tables, lists) use full width; form tabs stay constrained */}
        {activeTab === 'skills' ? (
          <SkillsSettingsTab />
        ) : activeTab === 'mcp-servers' ? (
          <div className="max-w-6xl mx-auto p-6">
            <MCPServersTab />
          </div>
        ) : activeTab === 'hive' ? (
          <div className="max-w-6xl mx-auto p-6">
            <HiveTab />
          </div>
        ) : activeTab === 'engine' ? (
          <div className="max-w-6xl mx-auto p-6">
            <EngineMetricsTab />
          </div>
        ) : (
          <div className="max-w-4xl mx-auto p-6">
            {activeTab === 'general' && <GeneralTab />}
            {activeTab === 'ai-models' && <AIModelsTab />}
            {activeTab === 'channels' && <ChannelsTab />}
            {activeTab === 'backup' && <BackupTab />}
            {activeTab === 'system' && <SystemTab />}
            {activeTab === 'about' && <AboutTab />}
          </div>
        )}
      </div>
    </div>
  );
}
