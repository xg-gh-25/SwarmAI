/**
 * Tell the backend which language the person uses the app in (`ui_language` in its
 * config), so what it writes for them on its own (the Inbox's reason lines and trend
 * titles, core/inbox/runner._user_profile) is in that language. The language itself lives
 * in localStorage (i18n/index.ts); this mirrors it once the backend is up and on every
 * change. A failed write only means the backend keeps its previous value.
 */
import type { i18n as I18n } from 'i18next';
import { settingsService } from '../services/settings';

export async function pushUiLanguage(lang: string): Promise<void> {
  const value = lang.startsWith('zh') ? 'zh' : 'en';
  try {
    const cfg = await settingsService.getAPIConfiguration();
    if ((cfg as Record<string, unknown>).uiLanguage === value) return;
    await settingsService.updateAPIConfiguration({ uiLanguage: value });
  } catch {
    // backend unreachable or refused: it keeps the old value until the next change
  }
}

/** Start mirroring; returns the cleanup that stops it. */
export function startUiLanguageSync(i18n: I18n): () => void {
  const onChange = (lang: string) => void pushUiLanguage(lang);
  void pushUiLanguage(i18n.language);
  i18n.on('languageChanged', onChange);
  return () => i18n.off('languageChanged', onChange);
}
