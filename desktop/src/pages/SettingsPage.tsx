/**
 * Settings page — thin wrapper over SettingsTabs.
 *
 * 7-tab layout: General, AI & Models, Channels, Skills, MCP Servers, System, About.
 * Supports initialTab prop so sidebar icons can deep-link to Skills or MCP tabs.
 */
import SettingsTabs from '../components/settings/SettingsTabs';

interface SettingsPageProps {
  initialTab?: string;
}

export default function SettingsPage({ initialTab }: SettingsPageProps) {
  return <SettingsTabs initialTab={initialTab} />;
}
