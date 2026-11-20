import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

test('send, stream, survive a reload', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  await input.fill('hello [fm:hello]');
  await input.press('Enter');
  const answer = shown('turn-answer').last();
  await expect(answer).toContainText('Hello from the scripted model.', { timeout: 60_000 });
  await expect(shown('streaming-bee')).toHaveCount(0, { timeout: 30_000 });
  await expect(input).toBeEditable();
  await page.reload();
  const mine = tabs.filter({ hasText: 'hello [fm:hello]' }).last();   // by its title, not its place
  await mine.click();
  await expect(mine).toHaveAttribute('aria-selected', 'true');
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 30_000 });
  const same = tabs.filter({ hasText: 'hello [fm:hello]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await mine.getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
