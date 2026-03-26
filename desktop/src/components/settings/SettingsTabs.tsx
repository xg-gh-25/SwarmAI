/**
 * Settings page tab layout wrapper.
 *
 * 7 tabs: General, AI & Models, Channels, Skills, MCP Servers, System, About.
 * Supports initialTab prop so sidebar icons can deep-link to a specific tab.
 */
import { useState, useEffect } from 'react';
import GeneralTab from './GeneralTab';
import AIModelsTab from './AIModelsTab';
import ChannelsTab from './ChannelsTab';
import SkillsSettingsTab from './SkillsTab';
import MCPServersTab from './MCPServersTab';
import SystemTab from './SystemTab';
import EngineMetricsTab from './EngineMetricsTab';
import AboutTab from './AboutTab';

const TABS = [
  { id: 'general', label: 'General', icon: 'settings' },
  { id: 'ai-models', label: 'AI & Models', icon: 'smart_toy' },
  { id: 'channels', label: 'Channels', icon: 'forum' },
  { id: 'skills', label: 'Skills', icon: 'extension' },
  { id: 'mcp-servers', label: 'MCP Servers', icon: 'device_hub' },
  { id: 'engine', label: 'Core Engine', icon: 'psychology' },
  { id: 'system', label: 'System', icon: 'dns' },
  { id: 'about', label: 'About', icon: 'info' },
] as const;

type TabId = typeof TABS[number]['id'];

interface SettingsTabsProps {
  initialTab?: string;
}

export default function SettingsTabs({ initialTab }: SettingsTabsProps) {
  const [activeTab, setActiveTab] = useState<TabId>(() => {
    // Validate initialTab is a valid tab id
    const valid = TABS.find(t => t.id === initialTab);
    return valid ? valid.id : 'general';
  });

  // Update when initialTab prop changes (e.g., sidebar navigation)
  useEffect(() => {
    if (initialTab) {
      const valid = TABS.find(t => t.id === initialTab);
      if (valid) setActiveTab(valid.id);
    }
  }, [initialTab]);

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-[var(--color-text)] mb-6">Settings</h1>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b border-[var(--color-border)] overflow-x-auto">
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

      {/* Tab content */}
      <div>
        {activeTab === 'general' && <GeneralTab />}
        {activeTab === 'ai-models' && <AIModelsTab />}
        {activeTab === 'channels' && <ChannelsTab />}
        {activeTab === 'skills' && <SkillsSettingsTab />}
        {activeTab === 'mcp-servers' && <MCPServersTab />}
        {activeTab === 'engine' && <EngineMetricsTab />}
        {activeTab === 'system' && <SystemTab />}
        {activeTab === 'about' && <AboutTab />}
      </div>
    </div>
  );
}
