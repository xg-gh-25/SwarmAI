/**
 * Settings page — thin wrapper over SettingsTabs.
 *
 * 11-tab layout (10 in non-desktop builds — Hive is desktop-only). See SettingsTabs.
 * Supports initialTab prop so sidebar icons can deep-link to Skills or MCP tabs.
 */
import SettingsTabs from '../components/settings/SettingsTabs';

interface SettingsPageProps {
  initialTab?: string;
}

export default function SettingsPage({ initialTab }: SettingsPageProps) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      <SettingsTabs initialTab={initialTab} />
    </div>
  );
}
