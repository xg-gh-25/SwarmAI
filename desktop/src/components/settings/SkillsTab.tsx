/**
 * Skills settings tab.
 *
 * Full skill list with search, rescan, generate, and delete.
 * Replaces the standalone SkillsPage/SkillsModal — now lives in Settings.
 * Reuses the SkillsPage component directly (it's already well-structured).
 *
 * SkillsPage has its own p-8 padding and a breadcrumb — both are fine
 * when rendered standalone but when embedded in Settings the breadcrumb
 * is redundant (Settings tab bar already gives context).  For now we
 * keep it as-is since SkillsPage is also used standalone; a future
 * refactor can accept a `hideHeader` prop.
 */
import SkillsPage from '../../pages/SkillsPage';

export default function SkillsSettingsTab() {
  return <SkillsPage embedded />;
}
