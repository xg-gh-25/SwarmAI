import { test, expect } from '@playwright/test';

test('opens from the left nav and closes', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-todo').click();
  const panel = page.getByRole('dialog', { name: 'ToDo' });
  await expect(panel).toBeVisible();
  await expect(panel).toBeInViewport({ ratio: 1 });
  await panel.getByRole('button', { name: 'Close' }).click();
  await expect(panel).toBeHidden();
  await expect(page.getByTestId('chat-input')).toBeVisible();
});
