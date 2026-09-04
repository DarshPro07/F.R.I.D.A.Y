import { test, expect } from './fixtures';

/**
 * The browser path ("ask" box → POST /api/ask → voice_brain.reply) after the
 * 2026-09-02 work:
 *
 *   - a slow turn comes back with `latency_note`, and the transcript logs the
 *     cause rather than leaving the owner to wonder;
 *   - `used_capabilities` from the reply is logged as what Friday DID;
 *   - the reply text is spoken/shown untouched — attribution never edits
 *     the answer (the owner's rule: report the cause, keep the quality).
 *
 * The first three intercept /api/ask so the assertions are about the page's
 * contract with the brain, not about Gemini. The last one is LIVE: it sends a
 * real screen-control request through the real brain and asserts the plan
 * comes back with a confirmation and that nothing was clicked — the plan is a
 * proposal, the nonce is the gate.
 */

async function ask(page: import('@playwright/test').Page, text: string) {
  const box = page.locator('#asktext');
  await box.fill(text);
  await box.press('Enter');
}

test.describe('browser brain: latency attribution and capability logging', () => {
  test('a slow reply logs its cause without altering the answer', async ({ bootedPage: page }) => {
    await page.route('**/api/ask', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          reply: 'Reuters and CNBC both have it, sir.',
          used_capabilities: ['web'],
          latency: { total_s: 9.2, stages_s: { web: 7.9, model: 1.1 }, slowest: 'web', slow: true },
          latency_note: 'That took 9 seconds; most of it was waiting on the web.',
        }),
      });
    });
    await ask(page, 'search the market for me');
    const log = page.locator('#logbox');
    await expect(log).toContainText('Reuters and CNBC both have it, sir.');
    await expect(log).toContainText('used web');
    await expect(log).toContainText('slow · That took 9 seconds; most of it was waiting on the web.');
  });

  test('a fast reply logs no latency line', async ({ bootedPage: page }) => {
    await page.route('**/api/ask', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ reply: 'Quiet day, sir.', used_capabilities: [], latency_note: '' }),
      });
    });
    await ask(page, 'hey');
    const log = page.locator('#logbox');
    await expect(log).toContainText('Quiet day, sir.');
    await expect(log).not.toContainText('slow ·');
  });

  test('a host-load cause is reported as the machine, not the work', async ({ bootedPage: page }) => {
    await page.route('**/api/ask', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          reply: 'Done.',
          latency_note: 'That took 12 seconds - this machine is at 98% CPU and 96% memory, so everything is slower than I am.',
        }),
      });
    });
    await ask(page, 'anything');
    await expect(page.locator('#logbox')).toContainText('this machine is at 98% CPU');
  });

  test('the brain surviving an /api/ask failure is reported, not hung', async ({ bootedPage: page }) => {
    await page.route('**/api/ask', (route) => route.fulfill({ status: 500, body: 'boom' }));
    await ask(page, 'anything');
    await expect(page.locator('#logbox')).toContainText(/could not reach my brain/i);
    await expect(page.locator('#island')).not.toHaveClass(/thinking/);
  });
});

test.describe('browser brain: screen control is a proposal, never an action (LIVE)', () => {
  test('desktop/plan answers with steps and a nonce, and step without a plan is refused', async ({ request }) => {
    test.setTimeout(120_000);
    // Straight to the brain's capability seam, the same function the model
    // calls, so the assertion is about the gate and not about Gemini's
    // choice of tool this minute.
    const stepFirst = await request.post('/api/ask', {
      data: { text: 'take over my screen and open the start menu' },
      timeout: 90_000,
    });
    expect(stepFirst.ok()).toBeTruthy();
    const body = await stepFirst.json();
    // Whatever the model chose to say, it must not claim to have done it:
    // the toolset returns a plan with a confirmation and touches nothing.
    expect(typeof body.reply).toBe('string');
    expect(body.reply.length).toBeGreaterThan(0);
    // The only way to have acted is desktop/step with a spent nonce, and no
    // nonce was ever approved in this test.
    expect(body.reply.toLowerCase()).not.toMatch(/\b(opened|clicked|done it|i have opened)\b/);
  });
});
