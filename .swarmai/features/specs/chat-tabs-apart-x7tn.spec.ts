import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

// The MessageStore isolation invariant, from the user's side: two tabs streaming at the
// same time never show each other's reply.
test('two tabs stream at once, each keeps its own reply', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  const panel = page.getByTestId('chat-panel');
  const newTab = page.getByRole('button', { name: 'New Session' }).last();
  const before = await tabs.count();
  await input.fill('first tab [fm:slow-a]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('alpha', { timeout: 60_000 });
  await newTab.click();                                  // while the first still streams
  await expect(tabs).toHaveCount(before + 1);
  await input.fill('second tab [fm:slow-b]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('beta beta beta', { timeout: 60_000 });
  await expect(shown('streaming-bee')).toHaveCount(0, { timeout: 60_000 });
  await expect(panel).not.toContainText('alpha', { useInnerText: true });
  const first = tabs.filter({ hasText: 'first tab [fm:slow-a]' }).last();   // by its title, not its place
  await first.click();
  await expect(first).toHaveAttribute('aria-selected', 'true');
  await expect(shown('turn-answer').last()).toContainText('alpha alpha alpha', { timeout: 60_000 });
  await expect(shown('streaming-bee')).toHaveCount(0, { timeout: 60_000 });
  await expect(panel).not.toContainText('beta', { useInnerText: true });
  const second = tabs.filter({ hasText: 'second tab [fm:slow-b]' });   // an earlier walk's tab may share the title
  const n = await second.count();
  await second.last().getByRole('button', { name: /^Close / }).click();
  await expect(second).toHaveCount(n - 1);
  await page.reload();                       // a closed tab stays closed, even right after the close
  await expect(first).toBeVisible();         // the saved tabs are back
  await expect(second).toHaveCount(n - 1);
  const firsts = tabs.filter({ hasText: 'first tab [fm:slow-a]' });
  const m = await firsts.count();
  await first.getByRole('button', { name: /^Close / }).click();
  await expect(firsts).toHaveCount(m - 1);
});
