import { test, expect } from './fixtures';

/**
 * D-19 (probe C, 2026-09-20): "How many skill families do you have and what
 * state is each in? Read the real state before answering." matched the
 * page's LOOK pattern ("read the") and was routed to the webcam instead of
 * the brain - /api/ask was never called and the step timed out at 155 s.
 * A question about Friday HERSELF (skills, families, providers, state,
 * memory, what she did) goes to the brain, which reads the real state.
 * Negative controls: a real look request still routes to sight, and a
 * self-state question that names the camera still does.
 */
test.describe('page routing - self-state questions reach the brain', () => {
  test('a skills/state question is posted to /api/ask, not the camera', async ({ bootedPage: page }) => {
    let posted = '';
    await page.route('**/api/ask', async (route) => {
      posted = (route.request().postDataJSON() as { text: string }).text;
      await route.fulfill({ json: { reply: 'Fourteen families, sir.', used_capabilities: [] } });
    });
    const ask = page.locator('#asktext');
    await ask.fill('How many skill families do you have and what state is each in? Read the real state before answering.');
    await ask.press('Enter');
    await expect.poll(() => posted).toContain('How many skill families');
    await expect(page.locator('#conv')).toContainText('Fourteen families');
  });

  test('"did you open Notepad" and "which model are you running on" reach the brain too', async ({ bootedPage: page }) => {
    const seen: string[] = [];
    await page.route('**/api/ask', async (route) => {
      seen.push((route.request().postDataJSON() as { text: string }).text);
      await route.fulfill({ json: { reply: 'ok', used_capabilities: [] } });
    });
    const ask = page.locator('#asktext');
    for (const q of ['Did you open Notepad for me just now? Read what you actually did.',
                     'Which model are you running on right now? Look at your real state.']) {
      await ask.fill(q);
      await ask.press('Enter');
    }
    await expect.poll(() => seen.length).toBe(2);
  });

  test('a real look request still goes to sight (negative control)', async ({ bootedPage: page }) => {
    let asks = 0;
    await page.route('**/api/ask', async (route) => { asks += 1; await route.fulfill({ json: { reply: 'x' } }); });
    const routed = await page.evaluate(() => (window as any).wantsSight('Look at my screen and tell me what this error says'));
    expect(routed).toBe('screen');
    const still = await page.evaluate(() => (window as any).wantsSight('Look through the camera - what state is my desk in?'));
    expect(still).toBe('camera');
    const self = await page.evaluate(() => (window as any).wantsSight('What state is each of your skill families in? Read the real state.'));
    expect(self).toBeNull();
    expect(asks).toBe(0);
  });
});
