import { test, expect } from './fixtures';

/**
 * S14: the typed prompt keeps everything after a LEADING wake word, with its
 * casing. 2026-09-04 21:17: "Friday, ... in friday/desk.py, cheapest model"
 * reached her as "/desk.py, cheapest model" because the stripper cut at the
 * last "friday" and lowercased the rest.
 */
test.describe('the wake word', () => {
  test('only a leading "Friday" is stripped and the case survives', async ({ bootedPage: page }) => {
    let posted = '';
    await page.route('**/api/ask', async (route) => {
      posted = (route.request().postDataJSON() as { text: string }).text;
      await route.fulfill({ json: { reply: 'Noted.', used_capabilities: [] } });
    });
    const ask = page.locator('#asktext');
    await ask.fill('Friday, hand this to Hermes: add a docstring to _Busy in friday/desk.py, cheapest model.');
    await ask.press('Enter');
    await expect.poll(() => posted).toBe('hand this to Hermes: add a docstring to _Busy in friday/desk.py, cheapest model.');
  });

  test('the wake word alone is answered without a request', async ({ bootedPage: page }) => {
    let requests = 0;
    await page.route('**/api/ask', async (route) => { requests += 1; await route.fulfill({ json: { reply: 'x' } }); });
    const ask = page.locator('#asktext');
    await ask.fill('Friday');
    await ask.press('Enter');
    await expect(page.locator('#conv')).toContainText('Yes, sir.');
    expect(requests).toBe(0);
  });
});
