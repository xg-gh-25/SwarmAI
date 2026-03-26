/**
 * Skills settings tab.
 *
 * Full skill list with search, rescan, generate, and delete.
 * Replaces the standalone SkillsPage/SkillsModal — now lives in Settings.
 * Reuses the SkillsPage component directly (it's already well-structured).
 */
import SkillsPage from '../../pages/SkillsPage';

export default function SkillsSettingsTab() {
  return (
    <div className="-mx-6 -mt-2">
      <SkillsPage />
    </div>
  );
}
