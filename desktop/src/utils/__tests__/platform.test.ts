/**
 * Platform labels: shortcut modifier and file manager name follow the OS the app
 * runs on, not the Mac the app was written on.
 */
import { describe, it, expect, afterEach } from 'vitest';
import i18n from '../../i18n';
import { detectOs, isMac, isWindows, modKeyLabel, fileManagerName, defaultDataDir, joinDir } from '../platform';

const UA = {
  mac: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)',
  windows: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/120.0',
  linux: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko)',
};

const saved = {
  userAgent: Object.getOwnPropertyDescriptor(window.navigator, 'userAgent'),
  platform: Object.getOwnPropertyDescriptor(window.navigator, 'platform'),
};

function setNavigator(userAgent: string, platform = '') {
  Object.defineProperty(window.navigator, 'userAgent', { value: userAgent, configurable: true });
  Object.defineProperty(window.navigator, 'platform', { value: platform, configurable: true });
}

afterEach(async () => {
  for (const [k, d] of Object.entries(saved)) {
    if (d) Object.defineProperty(window.navigator, k, d);
    else delete (window.navigator as unknown as Record<string, unknown>)[k];
  }
  await i18n.changeLanguage('en');
});

describe('detectOs', () => {
  it('reads macOS, Windows and Linux from the user agent', () => {
    setNavigator(UA.mac);
    expect(detectOs()).toBe('mac');
    setNavigator(UA.windows);
    expect(detectOs()).toBe('windows');
    setNavigator(UA.linux);
    expect(detectOs()).toBe('linux');
  });

  it('reads navigator.platform when the user agent says nothing', () => {
    setNavigator('Mozilla/5.0', 'MacIntel');
    expect(detectOs()).toBe('mac');
    setNavigator('Mozilla/5.0', 'Win32');
    expect(detectOs()).toBe('windows');
  });

  it('does not take "darwin" (a jsdom user agent) for Windows', () => {
    setNavigator('Mozilla/5.0 (darwin) AppleWebKit/537.36 (KHTML, like Gecko) jsdom/26.0.0');
    expect(isWindows()).toBe(false);
  });
});

describe('modKeyLabel', () => {
  it('is ⌘ on macOS', () => {
    setNavigator(UA.mac);
    expect(isMac()).toBe(true);
    expect(modKeyLabel()).toBe('⌘');
  });

  it('is Ctrl on Windows and Linux', () => {
    setNavigator(UA.windows);
    expect(isMac()).toBe(false);
    expect(modKeyLabel()).toBe('Ctrl');
    setNavigator(UA.linux);
    expect(modKeyLabel()).toBe('Ctrl');
  });
});

describe('fileManagerName', () => {
  it('names Finder, File Explorer or Files by OS', () => {
    setNavigator(UA.mac);
    expect(fileManagerName()).toBe('Finder');
    setNavigator(UA.windows);
    expect(fileManagerName()).toBe('File Explorer');
    setNavigator(UA.linux);
    expect(fileManagerName()).toBe('Files');
  });

  it('is translated', async () => {
    await i18n.changeLanguage('zh');
    setNavigator(UA.mac);
    expect(fileManagerName()).toBe('访达');
    setNavigator(UA.windows);
    expect(fileManagerName()).toBe('文件资源管理器');
  });
});

describe('the {{mod}} variable in locale strings', () => {
  it('fills the shortcut modifier without the caller passing it', () => {
    // brainLearned.confirm.shortcut is "{{mod}}↵ to withdraw"; the value comes from
    // i18n's defaultVariables, set once from modKeyLabel() at startup.
    const text = i18n.t('brainLearned.confirm.shortcut');
    expect(text).not.toContain('{{');
    expect(text).toContain('↵');
    expect(text).toContain(modKeyLabel());
  });
});

describe('data dir labels', () => {
  it('shows the default data dir in the OS notation until the backend says where it is', () => {
    setNavigator(UA.mac);
    expect(defaultDataDir()).toBe('~/.swarm-ai/');
    setNavigator(UA.linux);
    expect(defaultDataDir()).toBe('~/.swarm-ai/');
    setNavigator(UA.windows);
    expect(defaultDataDir()).toBe('%USERPROFILE%\\.swarm-ai\\');
  });

  it('joins a sub folder with the separator the dir already uses', () => {
    expect(joinDir('/Users/a/.swarm-ai', 'logs')).toBe('/Users/a/.swarm-ai/logs/');
    expect(joinDir('/Users/a/.swarm-ai/', 'logs')).toBe('/Users/a/.swarm-ai/logs/');
    expect(joinDir('C:\\Users\\a\\.swarm-ai', 'logs')).toBe('C:\\Users\\a\\.swarm-ai\\logs\\');
    expect(joinDir('/srv/data', '')).toBe('/srv/data/');
  });
});
