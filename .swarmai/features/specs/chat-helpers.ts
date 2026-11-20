import { expect, type Page } from '@playwright/test';

// inactive tabs keep their messages in the page, hidden: only what is shown counts
export const view = (page: Page) => (id: string) => page.getByTestId(id).filter({ visible: true });

export const tabList = (page: Page) => page.getByRole('tablist', { name: 'Session tabs' }).getByRole('tab');

/** Open the app and wait until its saved tabs are back: the page starts with a temporary
 *  tab and replaces it when the saved tabs load, so counting before that counts the wrong
 *  tabs (and a tab opened then is dropped). */
export async function openRestored(page: Page) {
  const restored = page.waitForResponse((r) => r.url().includes('/settings/open-tabs')
    && r.request().method() === 'GET' && r.ok());
  await page.goto('/');
  await restored;
  await expect(page.getByTestId('chat-input')).toBeVisible();
}

/** Open the app and a tab of the walk's own. Tabs an earlier walk left open (one that
 *  failed before its close) are closed first, so the tab limit never decides a walk. */
export async function freshTab(page: Page) {
  await openRestored(page);
  const input = page.getByTestId('chat-input');
  const tabs = tabList(page);
  await expect(input).toBeVisible();
  const left = tabs.filter({ hasText: '[fm:' });
  for (let n = await left.count(); n > 0; n--) {
    await left.first().getByRole('button', { name: /^Close / }).click();
    await expect(left).toHaveCount(n - 1);
  }
  const before = await tabs.count();
  await page.getByRole('button', { name: 'New Session' }).last().click();
  await expect(tabs).toHaveCount(before + 1);
  return { input, tabs };
}
