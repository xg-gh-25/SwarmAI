/**
 * openExternal opens only http, https and mailto links. Anything else is refused before the
 * opener plugin or window.open sees it: the old catch-all fallback handed a link the
 * plugin's scope refused (file:, javascript:) to window.open, which bypassed that scope.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const openUrl = vi.fn();
vi.mock('@tauri-apps/plugin-opener', () => ({ openUrl: (u: string) => openUrl(u) }));

import { openExternal } from '../openExternal';

describe('openExternal', () => {
  let winOpen: ReturnType<typeof vi.spyOn>;
  beforeEach(() => { openUrl.mockReset(); winOpen = vi.spyOn(window, 'open').mockImplementation(() => null); });
  afterEach(() => winOpen.mockRestore());

  it.each(['javascript:alert(1)', 'file:///etc/passwd', 'vscode://x', 'not a url'])('refuses %s and opens nothing', async (u) => {
    await expect(openExternal(u)).rejects.toThrow();
    expect(openUrl).not.toHaveBeenCalled();
    expect(winOpen).not.toHaveBeenCalled();
  });

  it.each(['https://example.com/a', 'http://127.0.0.1:18321/x', 'mailto:a@example.com'])('opens %s through the plugin', async (u) => {
    await openExternal(u);
    expect(openUrl).toHaveBeenCalledWith(u);
    expect(winOpen).not.toHaveBeenCalled();
  });

  it('falls back to a new tab only for an allowed link when the plugin fails', async () => {
    openUrl.mockRejectedValueOnce(new Error('no tauri'));
    await openExternal('https://example.com/b');
    expect(winOpen).toHaveBeenCalledWith('https://example.com/b', '_blank', 'noopener,noreferrer');
  });
});
