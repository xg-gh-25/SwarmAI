import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

test('one error, no spinner, the next message works', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  const panel = page.getByTestId('chat-panel');
  await input.fill('refuse this [fm:invalid]');
  await input.press('Enter');
  const refusal = panel.getByText('The scripted model refused this request.').filter({ visible: true });
  await expect(refusal.first()).toBeVisible({ timeout: 60_000 });
  await expect(panel.getByText('Send your message again to continue').filter({ visible: true })).toBeVisible();
  await expect(shown('streaming-bee')).toHaveCount(0, { timeout: 30_000 });
  await expect(refusal).toHaveCount(1);                 // shown once, not as text and again as a card
  await input.fill('hello [fm:hello]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 60_000 });
  await page.reload();                                // after a reload the error is still there, once
  const mine = tabs.filter({ hasText: 'refuse this [fm:invalid]' }).last();
  await mine.click();
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 30_000 });
  await expect(refusal).toHaveCount(1);
  // the same card as live, with its suggested action (a line of text before)
  await expect(panel.getByText('Send your message again to continue').filter({ visible: true })).toBeVisible();
  const same = tabs.filter({ hasText: 'refuse this [fm:invalid]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await tabs.filter({ hasText: 'refuse this [fm:invalid]' }).last().getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
