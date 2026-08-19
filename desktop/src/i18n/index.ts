import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import zh from './locales/zh.json';

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    // zh MUST be registered or changeLanguage('zh') silently falls back to English —
    // every zh.json translation (this whole app's, not just backup) would be dead code.
    // Regression from the v1.0.0 rebrand which dropped this import; restored 2026-08-19.
    zh: { translation: zh },
  },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
