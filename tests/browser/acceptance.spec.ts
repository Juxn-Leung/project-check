import { test, expect } from '../../assets/playwright/phase-errors';

// All cases intentionally request only `page`: browser error recording must be automatic.
test('happy-save-reload', async ({ page }) => {
  await test.step('pc:goto:open', () => page.goto('/'));
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
  await test.step('pc:fill:edit', () => page.getByRole('textbox', { name: 'Name' }).fill('Persisted browser user'));
  await test.step('pc:click:save', () => page.getByRole('button', { name: 'Save', exact: true }).click());
  await test.step('pc:assert:saved', () => expect(page.getByRole('status')).toHaveText('Saved: Persisted browser user'));
  await test.step('pc:reload:refresh', () => page.reload());
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
  await test.step('pc:assert:readback', () => expect(page.getByRole('textbox', { name: 'Name' })).toHaveValue('Persisted browser user'));
});

test('navigation-only-fake-click', async ({ page }) => {
  // A wrapper claims a click but executes only navigation. Runner passes; acceptance must block.
  await test.step('pc:click:save', () => page.goto('/'));
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
  await test.step('pc:assert:readback', () => expect(page.getByRole('heading')).toHaveText('Test profile'));
});

test('known-product-failure', async ({ page }) => {
  test.fail(true, 'Known broken behavior must remain failed in product acceptance');
  await test.step('pc:goto:open', () => page.goto('/'));
  await test.step('pc:assert:readback', () => expect(page.getByRole('heading')).toHaveText('This behavior is not implemented'));
});

test('empty-assert-wrapper', async ({ page }) => {
  await test.step('pc:goto:open', () => page.goto('/'));
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
  await test.step('pc:assert:readback', async () => {});
});

test('readback-before-reload', async ({ page }) => {
  await test.step('pc:goto:open', () => page.goto('/'));
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
  await test.step('pc:assert:readback', () => expect(page.getByRole('textbox', { name: 'Name' })).toHaveValue('Persisted browser user'));
  await test.step('pc:reload:refresh', () => page.reload());
  await expect(page.getByRole('status')).toHaveText('Loaded from server');
});

for (const fault of ['console', 'pageerror', 'network', 'requestfailed']) {
  test(`runtime-${fault}`, async ({ page }) => {
    await test.step('pc:goto:open', () => page.goto(`/?fault=${fault}`));
    await expect(page.getByRole('status')).toHaveText('Loaded from server');
    // Observe the error before checking the UI, avoiding timing-dependent sleeps.
    const emitted = fault === 'console' ? page.waitForEvent('console', message => message.type() === 'error')
      : fault === 'pageerror' ? page.waitForEvent('pageerror')
      : fault === 'network' ? page.waitForResponse(response => response.url().endsWith('/api/error'))
      : page.waitForEvent('requestfailed', request => request.url().endsWith('/api/disconnect'));
    await test.step('pc:click:trigger', () => page.getByRole('button', { name: 'Trigger fault' }).click());
    await emitted;
    await test.step('pc:assert:readback', () => expect(page.getByRole('status')).toHaveText('Fault triggered'));
    // Test body passes. Auto fixture teardown must fail on the observed browser error.
  });
}
