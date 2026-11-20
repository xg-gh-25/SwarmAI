import { test, expect } from '@playwright/test';
import { freshTab, view } from './chat-helpers';

test('a Bash step, then the answer that quotes it', { tag: '@start:chat' }, async ({ page }) => {
  const shown = view(page);
  const { input, tabs } = await freshTab(page);
  await input.fill('run it [fm:tool-bash]');
  await input.press('Enter');
  await expect(shown('turn-result').last()).toContainText('The command printed: feature-map-marker', { timeout: 60_000 });
  const group = shown('group-toggle').last();
  await expect(group).toContainText('1 step');
  await group.click();
  const strip = shown('tool-strip').last();
  await expect(strip.getByTestId('strip-tool')).toContainText('Bash');
  await expect(strip).toContainText('echo feature-map-marker');
  const same = tabs.filter({ hasText: 'run it [fm:tool-bash]' });   // an earlier walk's tab may share the title
  const n = await same.count();
  await tabs.filter({ hasText: 'run it [fm:tool-bash]' }).last().getByRole('button', { name: /^Close / }).click();
  await expect(same).toHaveCount(n - 1);
});
