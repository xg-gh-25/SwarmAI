import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

// A reply with a thought: the thought is shown with the answer, and the next turn (which
// sends the earlier thought back to the model) still gets its answer.
test('a thought, its answer, and the turn after', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  await input.fill('think first [fm:think]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('Here is the answer after thinking.', { timeout: 60_000 });
  await expect(shown('streaming-bee')).toHaveCount(0, { timeout: 30_000 });
  const thought = shown('thought').last();
  await expect(thought).toBeVisible();
  const toggle = thought.getByTestId('thought-toggle');
  if (await toggle.count()) await toggle.click();
  await expect(thought).toContainText('First read what is asked');
  await input.fill('and then [fm:hello]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 60_000 });
  const same = tabs.filter({ hasText: 'think first [fm:think]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await same.last().getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
