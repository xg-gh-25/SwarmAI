/**
 * Settings page tab layout wrapper.
 *
 * 5 tabs: General, AI & Models, Channels, System, About.
 * Replaces the flat section layout in SettingsPage.
 */
import { useState } from 'react';
import GeneralTab from './GeneralTab';
import AIModelsTab from './AIModelsTab';
import ChannelsTab from './ChannelsTab';
import SystemTab from './SystemTab';
import AboutTab from './AboutTab';

const TABS = [
  { id: 'general', label: 'General', icon: 'settings' },
  { id: 'ai-models', label: 'AI & Models', icon: 'smart_toy' },
  { id: 'channels', label: 'Channels', icon: 'forum' },
  { id: 'system', label: 'System', icon: 'dns' },
  { id: 'about', label: 'About', icon: 'info' },
] as const;

type TabId = typeof TABS[number]['id'];

export default function SettingsTabs() {
  const [activeTab, setActiveTab] = useState<TabId>('general');

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-[var(--color-text)] mb-6">Settings</h1>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b border-[var(--color-border)]">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2.5 text-sm font-medium transition-colors flex items-center gap-1.5 border-b-2 -mb-px ${
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
        {activeTab === 'system' && <SystemTab />}
        {activeTab === 'about' && <AboutTab />}
      </div>
    </div>
  );
}
