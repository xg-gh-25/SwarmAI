/**
 * Settings page — thin wrapper over SettingsTabs.
 *
 * The old flat-section layout has been replaced with a 5-tab layout:
 * General, AI & Models, Channels, System, About.
 *
 * Dead sections removed: Claude Agent SDK, Self-Evolution (zero user value).
 */
import SettingsTabs from '../components/settings/SettingsTabs';

export default function SettingsPage() {
  return <SettingsTabs />;
}
