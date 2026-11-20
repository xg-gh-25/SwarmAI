import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

test('Stop ends the reply, adds nothing after, and the chat goes on', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  const panel = page.getByTestId('chat-panel');
  await input.fill('stream slowly [fm:slow]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('word word', { timeout: 60_000 });
  // the backend's stop lands 1.5 s after the click (a slow stop): the reply streams on
  // meanwhile, and none of it may reach the stopped turn
  await page.route('**/api/chat/stop/**', async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.getByRole('button', { name: 'Stop generation (Esc)' }).click();
  await expect(shown('turn-stopped').last()).toContainText('Stopped by you');
  await expect(shown('streaming-bee')).toHaveCount(0);
  const stopped = shown('turn-answer').filter({ hasText: 'word word' });
  // past the slow stop: the text streamed meanwhile was never added live (the turn may
  // then show its record, what the model wrote before the stop landed: live == reload)
  await page.waitForTimeout(2500);
  // the next message gets its answer
  await input.fill('hello again [fm:hello]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 60_000 });
  // still one block, still marked stopped, nothing after the marker
  await expect(stopped).toHaveCount(1);       // no second block of the stopped reply
  await expect(shown('turn-stopped').first()).toContainText('Stopped by you');
  await expect(panel).not.toContainText(/Stopped\s*word/, { useInnerText: true });
  const same = tabs.filter({ hasText: 'stream slowly [fm:slow]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await tabs.filter({ hasText: 'stream slowly [fm:slow]' }).last().getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
