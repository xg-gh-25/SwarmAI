/**
 * Runtime i18n registration test.
 *
 * The other backup tests import the locale JSON directly and check key presence —
 * that CANNOT catch a locale being absent from the i18next runtime `resources`.
 * A regression (v1.0.0 rebrand) dropped `zh` from the runtime config, so every
 * zh.json translation silently fell back to English. This test drives the REAL
 * i18n instance: switch to zh and assert t() returns the Chinese value, not the
 * English fallback.
 */
import { describe, it, expect, afterAll } from 'vitest';
import i18n from '../index';

describe('i18n runtime resources', () => {
  afterAll(async () => {
    await i18n.changeLanguage('en');
  });

  it('registers both en and zh (not just en)', () => {
    expect(i18n.hasResourceBundle('en', 'translation')).toBe(true);
    expect(i18n.hasResourceBundle('zh', 'translation')).toBe(true);
  });

  it('changeLanguage("zh") returns Chinese, not English fallback', async () => {
    await i18n.changeLanguage('zh');
    const zhTitle = i18n.t('settings.backup.title');
    expect(zhTitle).toBe('工作区备份');
    // guard against silent English fallback
    expect(zhTitle).not.toBe('Workspace Backup');
  });

  it('interpolates params under zh (ok toast)', async () => {
    await i18n.changeLanguage('zh');
    const msg = i18n.t('settings.backup.toast.ok', { tablesExported: 3, commit: 'abc' });
    expect(msg).toContain('3');
    expect(msg).toContain('abc');
    expect(msg).toContain('张表'); // Chinese, proves not English fallback
  });
});
