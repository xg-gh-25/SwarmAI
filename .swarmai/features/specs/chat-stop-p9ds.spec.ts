import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

test('Stop ends the reply, adds nothing after, and the chat goes on', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  const panel = page.getByTestId('chat-panel');
  await input.fill('stream slowly [fm:slow]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('word word', { timeout: 60_000 });
  await page.getByRole('button', { name: 'Stop generation (Esc)' }).click();
  await expect(shown('turn-stopped').last()).toContainText('Stopped by you');
  await expect(shown('streaming-bee')).toHaveCount(0);
  // the next message gets its answer (and by then anything late from the stopped
  // reply has arrived)
  await input.fill('hello again [fm:hello]');
  await input.press('Enter');
  await expect(shown('turn-answer').last()).toContainText('Hello from the scripted model.', { timeout: 60_000 });
  // nothing of the stopped reply came after its "Stopped" marker
  await expect(panel).not.toContainText(/Stopped\s*word/, { useInnerText: true });
  const same = tabs.filter({ hasText: 'stream slowly [fm:slow]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await tabs.filter({ hasText: 'stream slowly [fm:slow]' }).last().getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
