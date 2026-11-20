import { test, expect } from '@playwright/test';

// Each run starts as a fresh install: the walk itself finishes the first run, and it
// runs once per viewport against the same backend.
test.beforeEach(async ({ request }) => {
  const reset = await request.delete(`${process.env.VITE_API_URL}/api/system/onboarding-complete`);
  expect(reset.ok()).toBeTruthy();
});

test('setup wizard, then Configure later', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Connect your AWS account')).toBeVisible();
  // The sign-in cards follow the machine: ADA and AWS SSO where Amazon's internal tools are
  // installed (ada on PATH), AWS login and AWS SSO elsewhere (a Hive, a fresh machine). Once
  // detection is done, the wizard's own Switch shows the internal set, so the walk checks the
  // same cards on every machine.
  await expect(page.getByTestId('connect-detecting')).toBeHidden();
  if (!(await page.getByTestId('connect-method-ada').isVisible())) {
    await expect(page.getByTestId('connect-method-login')).toBeVisible();
    await page.getByTestId('connect-ctx-switch').click();
  }
  await expect(page.getByTestId('connect-method-ada')).toBeVisible();
  await expect(page.getByTestId('connect-method-sso')).toBeVisible();
  await page.getByTestId('connect-later').click();
  await expect(page.getByTestId('chat-input')).toBeVisible();
});
