import { describe, it, expect, vi, beforeEach } from 'vitest';
import { pushUiLanguage, startUiLanguageSync } from './syncUiLanguage';
import { settingsService } from '../services/settings';

vi.mock('../services/settings', () => ({
  settingsService: { getAPIConfiguration: vi.fn(), updateAPIConfiguration: vi.fn() },
}));
const svc = settingsService as unknown as { getAPIConfiguration: ReturnType<typeof vi.fn>; updateAPIConfiguration: ReturnType<typeof vi.fn> };

describe('the app language reaches the backend', () => {
  beforeEach(() => { svc.getAPIConfiguration.mockReset(); svc.updateAPIConfiguration.mockReset(); });

  it('writes the language only when the backend holds another one', async () => {
    svc.getAPIConfiguration.mockResolvedValue({ uiLanguage: '' });
    await pushUiLanguage('zh-CN');
    expect(svc.updateAPIConfiguration).toHaveBeenCalledWith({ uiLanguage: 'zh' });
    svc.updateAPIConfiguration.mockReset();
    svc.getAPIConfiguration.mockResolvedValue({ uiLanguage: 'zh' });
    await pushUiLanguage('zh');
    expect(svc.updateAPIConfiguration).not.toHaveBeenCalled();
  });

  it('pushes at start and on every change, and a failure is swallowed', async () => {
    svc.getAPIConfiguration.mockRejectedValue(new Error('down'));
    const handlers: Record<string, (l: string) => void> = {};
    const i18n = { language: 'en', on: vi.fn((e, f) => { handlers[e] = f; }), off: vi.fn() };
    const stop = startUiLanguageSync(i18n as never);
    await Promise.resolve();
    expect(svc.getAPIConfiguration).toHaveBeenCalledTimes(1);
    svc.getAPIConfiguration.mockResolvedValue({ uiLanguage: 'en' });
    handlers.languageChanged('zh');
    await vi.waitFor(() => expect(svc.updateAPIConfiguration).toHaveBeenCalledWith({ uiLanguage: 'zh' }));
    stop();
    expect(i18n.off).toHaveBeenCalledWith('languageChanged', handlers.languageChanged);
  });
});
