import { test, expect } from './fixtures';

/**
 * S3b: the Work section in the control room lists running Hermes jobs
 * (id, model, status, latest line) from /api/work, on the existing poll
 * cadence -- no new polling machinery, same shape as pollDeck/pollGates.
 */
test.describe('the Work panel', () => {
  test('renders a running job from /api/work', async ({ bootedPage: page }) => {
    await page.route('**/api/work', async (route) => {
      await route.fulfill({
        json: {
          runs: [{
            id: 'hermes-work1', model: 'sonnet', status: 'WORKING',
            latest: 'editing policy.py - step 3', route_reason: 'default route',
          }],
          objectives: [], digest: '',
        },
      });
    });
    await page.evaluate(() => (window as any).setView('room'));
    await page.evaluate(() => (window as any).pollWork());
    await expect.poll(() => page.evaluate(() => !!(window as any)._WORK)).toBe(true);
    const list = page.locator('#work-list');
    await expect(list).toContainText('hermes-work1');
    await expect(list).toContainText('editing policy.py - step 3');
  });
});
