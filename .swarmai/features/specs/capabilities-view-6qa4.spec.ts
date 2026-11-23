import { test, expect } from '@playwright/test';

test('lists the apps, and the other views can be chosen', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-capabilities').click();
  const panel = page.getByRole('dialog', { name: 'Capabilities' });
  await expect(panel).toBeVisible();
  await expect(panel).toBeInViewport({ ratio: 1 });
  await expect(panel.getByTestId('cap-apps-view')).toBeVisible();
  await expect(panel.getByTestId('cap-app-frontend-design')).toBeVisible();
  await panel.getByTestId('cap-view-skills').click();
  await expect(panel.getByTestId('cap-apps-view')).toBeHidden();
});
