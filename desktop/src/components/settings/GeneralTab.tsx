/**
 * General settings tab: Language + Theme.
 *
 * Extracted from SettingsPage.tsx — same UI, scoped to its own tab.
 */
import { useTranslation } from 'react-i18next';
import { useTheme } from '../../contexts/ThemeContext';

export default function GeneralTab() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();

  const handleLanguageChange = (lang: 'zh' | 'en') => {
    i18n.changeLanguage(lang);
    localStorage.setItem('language', lang);
  };

  return (
    <div className="space-y-6">
      {/* Language */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('settings.language.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">{t('settings.language.description')}</p>
        <div className="flex gap-3">
          {(['zh', 'en'] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => handleLanguageChange(lang)}
              className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                i18n.language === lang
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
              }`}
            >
              {i18n.language === lang && <span className="material-symbols-outlined text-sm">check</span>}
              {t(`settings.language.${lang}`)}
            </button>
          ))}
        </div>
      </section>

      {/* Theme */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('settings.theme.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">{t('settings.theme.description')}</p>
        <div className="flex gap-3">
          {([
            { id: 'light', icon: 'light_mode' },
            { id: 'dark', icon: 'dark_mode' },
            { id: 'system', icon: 'contrast' },
          ] as const).map((opt) => (
            <button
              key={opt.id}
              onClick={() => setTheme(opt.id)}
              className={`flex-1 px-4 py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                theme === opt.id
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]'
              }`}
            >
              {theme === opt.id && <span className="material-symbols-outlined text-sm">check</span>}
              <span className="material-symbols-outlined text-sm">{opt.icon}</span>
              {t(`settings.theme.${opt.id}`)}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
