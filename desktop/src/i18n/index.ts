import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zh from './locales/zh.json';
import { modKeyLabel } from '../utils/platform';

export type UiLanguage = 'en' | 'zh';

/** localStorage key Settings > General writes the chosen UI language to. */
export const LANGUAGE_STORAGE_KEY = 'language';

/**
 * The language the UI starts in: the stored choice when it is 'en' or 'zh';
 * otherwise the OS language ('zh' for any zh-*, else 'en').
 */
export function initialLanguage(): UiLanguage {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
  } catch {
    /* storage unavailable: use the OS language */
  }
  if (stored === 'en' || stored === 'zh') return stored;
  const os = typeof navigator !== 'undefined' ? navigator.language ?? '' : '';
  return os.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    // zh MUST be registered or changeLanguage('zh') silently falls back to English.
    zh: { translation: zh },
  },
  lng: initialLanguage(),
  fallbackLng: 'en',
  interpolation: {
    escapeValue: false,
    // {{mod}} in any string is the shortcut modifier for this OS (⌘ or Ctrl), so
    // a string like "{{mod}}↵ to send" needs nothing from its caller.
    defaultVariables: { mod: modKeyLabel() },
  },
});

export default i18n;
