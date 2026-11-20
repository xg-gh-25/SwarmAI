import { test, expect } from '@playwright/test';

test('the banner opens Settings', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  const banner = page.getByTestId('credential-banner');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('No AWS account is connected');
  await page.getByTestId('credential-banner-action').click();
  const settings = page.getByRole('dialog', { name: 'Settings' });
  await expect(settings).toBeVisible();
  await expect(settings).toBeInViewport({ ratio: 1 });
  await expect(banner).toBeHidden();
});
