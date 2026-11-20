import { test, expect } from '@playwright/test';

test('opens with its sections, Escape closes', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-settings').click();
  const panel = page.getByRole('dialog', { name: 'Settings' });
  await expect(panel).toBeVisible();
  await expect(panel).toBeInViewport({ ratio: 1 });
  for (const section of ['General', 'Swarm AWS Account', 'LLM Models', 'Channels', 'Backup', 'System', 'About']) {
    await expect(panel.getByRole('tab', { name: section, exact: true })).toBeVisible();
  }
  const about = panel.getByRole('tab', { name: 'About', exact: true });
  await about.click();
  await expect(about).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
});
