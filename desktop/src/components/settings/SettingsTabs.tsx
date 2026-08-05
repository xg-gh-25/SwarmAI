/**
 * Settings page tab layout wrapper.
 *
 * 11 tabs (10 in non-desktop builds — Hive Accounts is desktop-only): General, AI &
 * Models, Channels, Skills, MCP Servers, Hive Accounts, Backup, Core Engine, System,
 * Capabilities, About. The Hive Accounts tab is the slim AWS-account-config surface;
 * fleet/instance MANAGEMENT lives in the first-class "Hive" nav card (HiveFleetOverlay,
 * run_b450108e) — R27 dual-entry convergence, shared components (settings/hiveComponents).
 * Supports initialTab prop so sidebar icons can deep-link to a specific tab.
 */
import { useState, useEffect, useMemo } from 'react';
import GeneralTab from './GeneralTab';
import AIModelsTab from './AIModelsTab';
import ChannelsTab from './ChannelsTab';
import SkillsSettingsTab from './SkillsTab';
import MCPServersTab from './MCPServersTab';
import SystemTab from './SystemTab';
import CapabilitiesTab from './CapabilitiesTab';
import EngineMetricsTab from './EngineMetricsTab';
import AboutTab from './AboutTab';
import HiveTab from './HiveTab';
import BackupTab from './BackupTab';
import { isDesktop } from '../../services/tauri';

/**
 * Width tiers:
 * - 'full'  — no max-width, own padding (data tables like Skills)
 * - '6xl'   — max-w-5xl 1024px (card grids: MCP, Hive, Engine)
 * - '4xl'   — max-w-3xl 768px  (forms: General, AI, Channels, etc.)
 */
const ALL_TABS = [
  { id: 'general', label: 'General', icon: 'settings', width: '4xl' as const },
  { id: 'ai-models', label: 'AI & Models', icon: 'smart_toy', width: '4xl' as const },
  { id: 'channels', label: 'Channels', icon: 'forum', width: '4xl' as const },
  { id: 'skills', label: 'Skills', icon: 'extension', width: 'full' as const },
  { id: 'mcp-servers', label: 'MCP Servers', icon: 'device_hub', width: '6xl' as const },
  { id: 'hive', label: 'Hive Accounts', icon: 'cloud', desktopOnly: true, width: '4xl' as const },
  { id: 'backup', label: 'Backup', icon: 'cloud_upload', width: '4xl' as const },
  { id: 'engine', label: 'Core Engine', icon: 'psychology', width: '6xl' as const },
  { id: 'system', label: 'System', icon: 'dns', width: '4xl' as const },
  { id: 'capabilities', label: 'Capabilities', icon: 'verified', width: '4xl' as const },
  { id: 'about', label: 'About', icon: 'info', width: '4xl' as const },
] as const;

const WIDTH_CLASSES = {
  'full': 'p-6',
  '6xl': 'max-w-5xl mx-auto p-6',
  '4xl': 'max-w-3xl mx-auto p-6',
} as const;

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
      {/* Tab bar — pinned at top, gradient hints when overflowing */}
      <div className="shrink-0 px-6 pt-4 border-b border-[var(--color-border)] overflow-x-auto [mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)] [-webkit-mask-image:linear-gradient(to_right,transparent,black_16px,black_calc(100%-16px),transparent)]">
        <div className="flex gap-1 justify-center-safe">
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
        {(() => {
          const widthClass = WIDTH_CLASSES[TABS.find(t => t.id === activeTab)?.width ?? '4xl'];
          const content = (
            <>
              {activeTab === 'general' && <GeneralTab />}
              {activeTab === 'ai-models' && <AIModelsTab />}
              {activeTab === 'channels' && <ChannelsTab />}
              {activeTab === 'skills' && <SkillsSettingsTab />}
              {activeTab === 'mcp-servers' && <MCPServersTab />}
              {activeTab === 'hive' && <HiveTab />}
              {activeTab === 'backup' && <BackupTab />}
              {activeTab === 'engine' && <EngineMetricsTab />}
              {activeTab === 'system' && <SystemTab />}
              {activeTab === 'capabilities' && <CapabilitiesTab />}
              {activeTab === 'about' && <AboutTab />}
            </>
          );
          return widthClass ? <div className={widthClass}>{content}</div> : content;
        })()}
      </div>
    </div>
  );
}
