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

/** The "+" that opens a tab. At the tab limit it is renamed ("System resources are
 *  limited…") and marked aria-disabled, so it is found by either name: a plain
 *  `name: 'New Session'` would then match a tab's own "Close New Session" button. */
export const newTabButton = (page: Page) =>
  page.getByRole('button', { name: /^(New Session|System resources are limited)/ });

/** Leave one empty tab, active, and no other of the user's tabs. Every tab holds a slot
 *  of the tab limit, and the limit follows the machine's free memory (3 to 5 slots, two
 *  of them Swarm Out and the channel slot): a tab left from start-up or an earlier walk
 *  takes the slot a walk's second tab needs, so whether that walk passes would depend on
 *  the memory free at that moment. The tab left is a fresh one: a tab with an earlier
 *  conversation is closed too (closing the last tab opens a new one). */
export async function soleTab(page: Page) {
  const tabs = tabList(page);
  const close = /^Close /;
  const own = tabs.filter({ has: page.getByRole('button', { name: close }) });   // Swarm Out has no Close
  for (let n = await own.count(); n > 1; n--) {
    await own.first().getByRole('button', { name: close }).click();
    await expect(own).toHaveCount(n - 1);
  }
  const fresh = own.filter({ hasText: 'New Session' });
  if (await fresh.count() === 0) {
    await own.first().getByRole('button', { name: close }).click();
    await expect(fresh).toHaveCount(1);
  }
  await own.first().click();
  await expect(own.first()).toHaveAttribute('aria-selected', 'true');
}

/** Open the app with a tab of the walk's own and no other (see `soleTab`). */
export async function freshTab(page: Page) {
  await openRestored(page);
  const input = page.getByTestId('chat-input');
  await expect(input).toBeVisible();
  await soleTab(page);
  return { input, tabs: tabList(page) };
}
