import { test, expect } from '@playwright/test';

test('shows the built-in Brains', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-brain-hub').click();
  const panel = page.getByRole('dialog', { name: 'Brain Hub' });
  await expect(panel).toBeVisible();
  await expect(panel).toBeInViewport({ ratio: 1 });
  await expect(panel.getByTestId('hub-card-SwarmAI')).toBeVisible();
  await expect(panel.getByTestId('hub-card-builtin-general')).toBeVisible();
  await expect(panel.getByTestId('hub-new-brain')).toBeVisible();
});
