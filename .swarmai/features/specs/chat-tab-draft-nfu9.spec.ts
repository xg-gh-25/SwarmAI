import { test, expect } from '@playwright/test';
import { newTabButton, openRestored, soleTab } from './chat-helpers';

// Open tabs persist in the backend: the walk starts from one tab of its own (soleTab, so
// the tab limit leaves room for the second it opens), and closes the tab it opened.
test('a new tab starts empty and the first keeps its draft', { tag: '@start:home' }, async ({ page }) => {
  await openRestored(page);
  const input = page.getByTestId('chat-input');
  const tabs = page.getByRole('tablist', { name: 'Session tabs' }).getByRole('tab');
  await expect(input).toBeVisible();
  await soleTab(page);
  const before = await tabs.count();
  const first = (await tabs.evaluateAll((ts) => ts.map((t) => t.getAttribute('aria-selected')))).indexOf('true');
  expect(first).toBeGreaterThanOrEqual(0);
  await input.fill('a draft in the first tab');
  await expect(newTabButton(page), 'the tab limit leaves room for a second tab').toHaveAttribute('aria-disabled', 'false');
  await newTabButton(page).click();
  await expect(tabs).toHaveCount(before + 1);
  const opened = tabs.nth(before);
  await expect(opened).toHaveAttribute('aria-selected', 'true');
  await expect(input).toHaveValue('');
  await tabs.nth(first).click();
  await expect(tabs.nth(first)).toHaveAttribute('aria-selected', 'true');
  await expect(input).toHaveValue('a draft in the first tab');
  await opened.getByRole('button', { name: /^Close / }).click();
  await expect(tabs).toHaveCount(before);
});
