/**
 * The UI language survives a restart, and a first launch follows the OS language.
 *
 * Settings > General writes localStorage 'language'; i18n/index.ts must read it at
 * startup. With nothing valid stored, the OS language decides: zh* -> 'zh', anything
 * else -> 'en'. Each case re-imports the real i18n module so its init runs again.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';

const realLanguage = Object.getOwnPropertyDescriptor(window.navigator, 'language');

function setNavigatorLanguage(lang: string) {
  Object.defineProperty(window.navigator, 'language', { value: lang, configurable: true });
}

async function freshI18n() {
  vi.resetModules();
  return (await import('../index')).default;
}

afterEach(() => {
  localStorage.clear();
  if (realLanguage) Object.defineProperty(window.navigator, 'language', realLanguage);
  else delete (window.navigator as unknown as Record<string, unknown>).language;
});

describe('initial UI language', () => {
  it('uses the stored choice: stored zh -> zh (even on an English OS)', async () => {
    localStorage.setItem('language', 'zh');
    setNavigatorLanguage('en-US');
    expect((await freshI18n()).language).toBe('zh');
  });

  it('uses the stored choice: stored en -> en (even on a Chinese OS)', async () => {
    localStorage.setItem('language', 'en');
    setNavigatorLanguage('zh-CN');
    expect((await freshI18n()).language).toBe('en');
  });

  it('ignores a stored value that is not en or zh and falls back to the OS', async () => {
    localStorage.setItem('language', 'klingon');
    setNavigatorLanguage('zh-TW');
    expect((await freshI18n()).language).toBe('zh');
  });

  it('first launch on a zh-CN OS -> zh', async () => {
    setNavigatorLanguage('zh-CN');
    expect((await freshI18n()).language).toBe('zh');
  });

  it('first launch on an en-US OS -> en', async () => {
    setNavigatorLanguage('en-US');
    expect((await freshI18n()).language).toBe('en');
  });

  it('first launch on any other OS language -> en', async () => {
    setNavigatorLanguage('fr-FR');
    expect((await freshI18n()).language).toBe('en');
  });
});
